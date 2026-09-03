#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""A CI gate must be un-revertible without a red check.

`lint-ci-required-gates.py` enforces one property: whatever jobs exist are all
aggregated by `ci-ok`. It is blind to the population — delete `sdk-contract`
and `packages` together with their `ci-ok.needs` entries and it still reports
OK, because a workflow with fewer jobs is still internally consistent. It is
equally blind to trigger semantics: re-adding
``&& github.event_name != 'pull_request'`` to `test` moves the entire suite
back to after the merge and every committed check stays green.

So the whole PR-time-gating rewrite could be reverted invisibly. This gate is
the missing half — it pins the SHAPE of ci.yml:

* every job in ``REQUIRED_JOBS`` exists, by name;
* every job can run on a human pull request (``PR_EXEMPT`` is the opt-out, and
  it is empty on purpose);
* the pre-merge gates also run on ``push: main`` — the branch this repository
  is actually written to, by mirror syncs that never open a pull request;
* no job DEPENDS on ``merge_group``, which never fires here (see the note on
  MERGE_GROUP_IS_LATENT below);
* the rule-6 dependabot filter is on the jobs that must carry it, and actually
  suppresses a bot PR;
* the rule-3 area filters gate the real work of the jobs they belong to;
* the widget-in-image assertion runs the command it claims to run.

Every assertion reads the PARSED document. Substring checks against the file
text are not acceptable here: ``'/app/static/cerid-widget.js' in text`` passes
when the literal survives only inside a comment, which is exactly how a gate
gets deleted without anyone noticing. The `if:` assertions go one further and
EVALUATE the expression against a simulated GitHub context, so a guard that is
present but inert still fails.

Usage:
    python scripts/lint-ci-gate-shape.py --workflow .github/workflows/ci.yml
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent

#: Every job ci.yml must define, with the reason it is load-bearing. A job
#: removed from ci.yml has to be removed from here too, in a commit someone
#: reviews.
REQUIRED_JOBS: dict[str, str] = {
    "changes": "area detection every rule-3 filter reads",
    "lint": "ruff, import contracts and the stdlib lint gates",
    "typecheck": "mypy",
    "test": "the pytest suite and the scripts/tests gate probes",
    "security": "detect-secrets, bandit, pip-audit, dlint",
    "lock-sync": "requirements.lock freshness",
    "frontend": "eslint, tsc, vitest, vite build, bundle caps",
    "license-scan": "the denylist scanner that the --fail-on flags missed",
    "sdk-contract": "the sdk-contract / sdk-openapi-drift gate six documents promise",
    "packages": "builds the distributed widget / SDK / extension bundles",
    "docker": "image builds, Trivy, and the widget-in-image assertion",
    "ci-ok": "the single required status check",
}

AGGREGATOR = "ci-ok"

#: Jobs allowed not to run on a pull request. Empty on purpose: a gate that
#: cannot fail before the merge does not gate anything, it reports.
PR_EXEMPT: frozenset[str] = frozenset()

#: Gates that must fire BOTH on a pull request and on push:main. Public `main`
#: receives direct mirror-sync pushes as well as merges, so a PR-only gate
#: would leave the sync path ungated.
PRE_MERGE_GATES = (
    "test",
    "security",
    "frontend",
    "license-scan",
    "sdk-contract",
    "packages",
    "docker",
)

#: Rule 6 — a dependency bump opts into the expensive behavioural jobs with a
#: label. The filter must be scoped to pull_request so push:main still runs
#: them unconditionally.
DEPENDABOT_FILTERED = ("test", "frontend", "sdk-contract", "packages", "docker")
DEPENDABOT_LABEL = "dependabot-full-ci"

#: Rule 3 — job -> the `changes` output that must gate its real work. The
#: filters sit on STEPS by design (a skipped job reads as a grey check), so a
#: job-level or step-level condition both satisfy this.
AREA_FILTERED = {"test": "mcp", "frontend": "web", "packages": "packages"}

#: The `changes` outputs that exist. An `if:` naming anything else is a
#: violation rather than a silent false.
CHANGES_OUTPUTS = ("code", "web", "mcp", "packages")

#: The docker job's widget assertion. `app/routers/widget.py` serves
#: GET /widget.js from this path; nothing put it in the image until 2026-08-31.
WIDGET_JOB = "docker"
WIDGET_COMMAND = "test -s /app/static/cerid-widget.js"

#: merge_group is kept in `on:` and in the `if:` expressions because it is
#: harmless, not because it works. There is no merge queue on this account and
#: none is configured (`gh api repos/Cerid-AI/cerid-ai/rulesets` -> []), so the
#: event never fires; a gate reachable ONLY through merge_group runs nowhere.
MERGE_GROUP_IS_LATENT = True


# ---------------------------------------------------------------------------
# A small evaluator for the subset of GitHub expressions that `if:` uses.
# ---------------------------------------------------------------------------


class ExprError(Exception):
    """The expression cannot be evaluated against the modelled context."""


_TOKEN = re.compile(
    r"""\s+
      | (?P<op>&&|\|\||==|!=|>=|<=|>|<|!|\(|\)|,)
      | (?P<str>'(?:[^']|'')*')
      | (?P<num>-?\d+(?:\.\d+)?)
      | (?P<path>[A-Za-z_][A-Za-z0-9_-]*(?:\.(?:[A-Za-z0-9_*-]+))*)
    """,
    re.X,
)


def _tokenize(expr: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    pos = 0
    while pos < len(expr):
        m = _TOKEN.match(expr, pos)
        if not m:
            raise ExprError(f"cannot tokenize at {expr[pos:pos + 20]!r}")
        pos = m.end()
        for kind in ("op", "str", "num", "path"):
            if m.group(kind) is not None:
                out.append((kind, m.group(kind)))
                break
    return out


class _Parser:
    def __init__(self, tokens: list[tuple[str, str]], ctx: "Context") -> None:
        self.t = tokens
        self.i = 0
        self.ctx = ctx

    def peek(self) -> tuple[str, str] | None:
        return self.t[self.i] if self.i < len(self.t) else None

    def eat(self, value: str) -> bool:
        tok = self.peek()
        if tok and tok[1] == value:
            self.i += 1
            return True
        return False

    def parse(self):
        value = self.or_()
        if self.i != len(self.t):
            raise ExprError(f"trailing tokens at {self.t[self.i:]!r}")
        return value

    def or_(self):
        value = self.and_()
        while self.eat("||"):
            right = self.and_()
            value = value if truthy(value) else right
        return value

    def and_(self):
        value = self.cmp()
        while self.eat("&&"):
            right = self.cmp()
            value = right if truthy(value) else value
        return value

    def cmp(self):
        left = self.unary()
        tok = self.peek()
        if tok and tok[1] in ("==", "!=", ">", "<", ">=", "<="):
            self.i += 1
            right = self.unary()
            return _compare(tok[1], left, right)
        return left

    def unary(self):
        if self.eat("!"):
            return not truthy(self.unary())
        return self.primary()

    def primary(self):
        tok = self.peek()
        if tok is None:
            raise ExprError("expression ended early")
        if tok[1] == "(":
            self.i += 1
            value = self.or_()
            if not self.eat(")"):
                raise ExprError("unbalanced parenthesis")
            return value
        self.i += 1
        kind, text = tok
        if kind == "str":
            return text[1:-1].replace("''", "'")
        if kind == "num":
            return float(text) if "." in text else int(text)
        if kind != "path":
            raise ExprError(f"unexpected token {text!r}")
        nxt = self.peek()
        if nxt and nxt[1] == "(":
            self.i += 1
            args = []
            if not self.eat(")"):
                args.append(self.or_())
                while self.eat(","):
                    args.append(self.or_())
                if not self.eat(")"):
                    raise ExprError(f"unbalanced call to {text}()")
            return _call(text, args)
        return self.ctx.resolve(text)


def _compare(op: str, left, right):
    if op == "==":
        return _loose_eq(left, right)
    if op == "!=":
        return not _loose_eq(left, right)
    try:
        if op == ">":
            return left > right
        if op == "<":
            return left < right
        if op == ">=":
            return left >= right
        return left <= right
    except TypeError as exc:  # pragma: no cover - not used by this workflow
        raise ExprError(f"cannot order {left!r} {op} {right!r}") from exc


def _loose_eq(left, right) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return truthy(left) == truthy(right)
    return left == right


def _call(name: str, args: list):
    if name in ("always",):
        return True
    if name in ("success",):
        return True
    if name in ("failure", "cancelled"):
        return False
    if name == "contains":
        if len(args) != 2:
            raise ExprError("contains() takes two arguments")
        haystack, needle = args
        if isinstance(haystack, (list, tuple)):
            return needle in haystack
        return str(needle) in str(haystack)
    if name == "startsWith":
        return str(args[0]).startswith(str(args[1]))
    if name == "endsWith":
        return str(args[0]).endswith(str(args[1]))
    raise ExprError(f"unmodelled function {name}()")


def truthy(value) -> bool:
    """GitHub truthiness: any non-empty string is true, including 'false'."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, str):
        return value != ""
    if isinstance(value, (int, float)):
        return value != 0
    return bool(value)


class Context:
    """A simulated GitHub context. An unmodelled path is an error, not False.

    That is deliberate. A guard keyed on a path this gate does not model could
    disable a job in a way the gate silently reads as "runs" — so the gate
    fails and the model has to be extended in the same commit.
    """

    def __init__(
        self,
        *,
        event_name: str,
        actor: str = "a-human",
        ref: str = "refs/heads/topic",
        draft: bool = False,
        labels: tuple[str, ...] = (),
        outputs: dict[str, str] | None = None,
        head_repo: str = "Cerid-AI/cerid-ai",
    ) -> None:
        self.event_name = event_name
        self.actor = actor
        self.ref = ref
        self.draft = draft
        self.labels = list(labels)
        self.outputs = dict(outputs or {k: "true" for k in CHANGES_OUTPUTS})
        self.head_repo = head_repo

    def with_output(self, key: str, value: str) -> "Context":
        clone = Context(
            event_name=self.event_name,
            actor=self.actor,
            ref=self.ref,
            draft=self.draft,
            labels=tuple(self.labels),
            outputs=self.outputs,
            head_repo=self.head_repo,
        )
        clone.outputs[key] = value
        return clone

    def resolve(self, path: str):
        table = {
            "true": True,
            "false": False,
            "null": None,
            "github.event_name": self.event_name,
            "github.actor": self.actor,
            "github.ref": self.ref,
            "github.repository": "Cerid-AI/cerid-ai",
            "github.event.pull_request.draft": self.draft,
            "github.event.pull_request.labels.*.name": self.labels,
            "github.event.pull_request.head.repo.full_name": self.head_repo,
        }
        if path in table:
            return table[path]
        m = re.fullmatch(r"needs\.([A-Za-z0-9_-]+)\.outputs\.([A-Za-z0-9_-]+)", path)
        if m:
            job, key = m.groups()
            if job != "changes" or key not in self.outputs:
                raise ExprError(
                    f"`if:` reads {path!r}, which this gate does not model — "
                    f"add the output to CHANGES_OUTPUTS so its effect is checked"
                )
            return self.outputs[key]
        if re.fullmatch(r"needs\.[A-Za-z0-9_-]+\.result", path):
            return "success"
        if path.startswith("vars."):
            # An unset repo variable. Modelling it as unset is what makes a
            # `vars.X != '1'` escape hatch fail SAFE in this gate: the state it
            # checks is the state where the gate still runs.
            return ""
        raise ExprError(f"`if:` reads unmodelled context path {path!r}")


def evaluate(expr: str, ctx: Context) -> bool:
    """Evaluate a workflow `if:` expression. Bare `${{ }}` wrappers allowed."""
    text = expr.strip()
    if text.startswith("${{") and text.endswith("}}"):
        text = text[3:-2].strip()
    if "${{" in text:
        raise ExprError("interpolated `if:` — cannot be evaluated statically")
    return truthy(_Parser(_tokenize(text), ctx).parse())


# ---------------------------------------------------------------------------
# Contexts the gates are checked against
# ---------------------------------------------------------------------------

PR_HUMAN = Context(event_name="pull_request")
PUSH_MAIN = Context(event_name="push", ref="refs/heads/main")
MERGE_GROUP = Context(event_name="merge_group", ref="refs/heads/gh-readonly-queue/main/x")
PR_DEPENDABOT = Context(event_name="pull_request", actor="dependabot[bot]")
PR_DEPENDABOT_OPTED_IN = Context(
    event_name="pull_request", actor="dependabot[bot]", labels=(DEPENDABOT_LABEL,)
)


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def _runs(job: dict, ctx: Context, where: str, problems: list[str]) -> bool:
    expr = job.get("if")
    if expr is None:
        return True
    try:
        return evaluate(str(expr), ctx)
    except ExprError as exc:
        problems.append(f"{where}: {exc}")
        return True


def _strip_shell_comments(script: str) -> str:
    """Drop whole-line shell comments so a commented-out command cannot pass."""
    return "\n".join(
        line for line in script.splitlines() if not line.lstrip().startswith("#")
    )


def check_document(doc: object, label: str = ".github/workflows/ci.yml") -> list[str]:
    """Return every way this workflow document fails the required shape."""
    problems: list[str] = []
    if not isinstance(doc, dict):
        return [f"{label}: not a mapping — the workflow did not parse"]

    triggers = doc.get(True, doc.get("on"))  # PyYAML reads a bare `on:` as True
    if not isinstance(triggers, dict) or "pull_request" not in triggers:
        problems.append(
            f"{label}: no `on: pull_request` trigger — every gate below it would "
            f"only ever report after the merge"
        )
    concurrency = doc.get("concurrency")
    if not isinstance(concurrency, dict) or concurrency.get("cancel-in-progress") is not True:
        problems.append(
            f"{label}: concurrency.cancel-in-progress is not true — running the "
            f"heavy gates at PR time is only affordable if superseded runs die"
        )

    jobs = doc.get("jobs")
    if not isinstance(jobs, dict):
        return problems + [f"{label}: no `jobs:` mapping"]

    for name, why in REQUIRED_JOBS.items():
        if name not in jobs:
            problems.append(f"{label}: required job {name!r} is missing — {why}")
    defined = {n: j for n, j in jobs.items() if isinstance(j, dict)}

    # The aggregator has to cover the population, not the list someone
    # remembered. Deleting a job AND its `needs:` entry is the mutation that
    # leaves lint-ci-required-gates.py green.
    agg = defined.get(AGGREGATOR)
    if agg is not None:
        covered = set(agg.get("needs") or [])
        for name in sorted(set(defined) - {AGGREGATOR} - covered):
            problems.append(
                f"{label}: {AGGREGATOR}.needs does not cover {name!r} — a skipped "
                f"gate would read as a passing one"
            )

    for name, job in defined.items():
        if name in PR_EXEMPT:
            continue
        where = f"{label}: job {name!r}"
        if not _runs(job, PR_HUMAN, where, problems):
            problems.append(
                f"{where}: does not run on a human pull_request — its `if:` "
                f"evaluates false there, so it can only report after the merge"
            )

    for name in PRE_MERGE_GATES:
        job = defined.get(name)
        if job is None:
            continue
        where = f"{label}: job {name!r}"
        if not _runs(job, PUSH_MAIN, where, problems):
            problems.append(
                f"{where}: does not run on push:main — `main` also takes direct "
                f"mirror-sync pushes, and nothing else gates those"
            )

    if MERGE_GROUP_IS_LATENT:
        for name, job in defined.items():
            where = f"{label}: job {name!r}"
            if (
                _runs(job, MERGE_GROUP, where, problems)
                and not _runs(job, PR_HUMAN, where, problems)
                and not _runs(job, PUSH_MAIN, where, problems)
            ):
                problems.append(
                    f"{where}: reachable only through merge_group. There is no "
                    f"merge queue on this plan and none is configured, so that "
                    f"event never fires and this gate runs nowhere"
                )

    for name in DEPENDABOT_FILTERED:
        job = defined.get(name)
        if job is None:
            continue
        where = f"{label}: job {name!r}"
        if _runs(job, PR_DEPENDABOT, where, problems):
            problems.append(
                f"{where}: no effective rule-6 dependabot filter — a bump runs "
                f"the full job without the {DEPENDABOT_LABEL!r} label"
            )
        if not _runs(job, PR_DEPENDABOT_OPTED_IN, where, problems):
            problems.append(
                f"{where}: the {DEPENDABOT_LABEL!r} label does not opt back in — "
                f"the filter cannot be overridden, so a bump can never be fully tested"
            )

    for name, area in AREA_FILTERED.items():
        job = defined.get(name)
        if job is None:
            continue
        problems += _check_area_filter(label, name, job, area)

    problems += _check_widget_assertion(label, defined.get(WIDGET_JOB))
    return problems


def _check_area_filter(label: str, name: str, job: dict, area: str) -> list[str]:
    """The job's real work must be gated on the `changes` output for its tree."""
    conditions = []
    if job.get("if") is not None:
        conditions.append(str(job["if"]))
    for step in job.get("steps") or []:
        if isinstance(step, dict) and step.get("if") is not None:
            conditions.append(str(step["if"]))
    on = PR_HUMAN.with_output(area, "true")
    off = PR_HUMAN.with_output(area, "false")
    for expr in conditions:
        try:
            if evaluate(expr, on) and not evaluate(expr, off):
                return []
        except ExprError:
            continue
    return [
        f"{label}: job {name!r} has no rule-3 filter on "
        f"needs.changes.outputs.{area} — it pays full price for a change it "
        f"cannot observe"
    ]


def _check_widget_assertion(label: str, job: dict | None) -> list[str]:
    """The widget-in-image step must RUN the command, not mention it."""
    if job is None:
        return []
    for step in job.get("steps") or []:
        if not isinstance(step, dict):
            continue
        script = _strip_shell_comments(str(step.get("run") or ""))
        if WIDGET_COMMAND in script and "docker run" in script:
            return []
    return [
        f"{label}: job {WIDGET_JOB!r} never runs {WIDGET_COMMAND!r} against the "
        f"built image — GET /widget.js can 404 in every container with CI green"
    ]


def check_workflow(path: Path) -> list[str]:
    return check_document(yaml.safe_load(path.read_text(encoding="utf-8")), str(path))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--workflow",
        type=Path,
        default=REPO / ".github" / "workflows" / "ci.yml",
        help="workflow to check",
    )
    ap.add_argument("--check", action="store_true", help="accepted for gate parity")
    args = ap.parse_args()

    problems = check_workflow(args.workflow)
    if problems:
        for p in problems:
            print(f"  {p}")
        print(f"FAIL — {len(problems)} problem(s)", file=sys.stderr)
        return 1
    print(f"OK — {len(REQUIRED_JOBS)} required jobs present, all gate on pull_request")
    return 0


if __name__ == "__main__":
    sys.exit(main())
