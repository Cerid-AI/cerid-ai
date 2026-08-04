#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""A skipped gate must not read as a passing gate.

GitHub renders a skipped job as a grey check, not a red one, and a job that
declares ``needs:`` is skipped whenever any dependency fails. So a blocking gate
can stop executing entirely and the only visible signal is the *other* job's
failure. Nothing says the gate did not run.

`docker` declares ``needs:`` on nine jobs. On 2026-07-29 an audit found it had
not executed since 07-24 for exactly this reason, with Trivy advisory drift
accumulating behind a job nobody could see. It recurred on 2026-08-03: the
`security` job began failing on three new `cryptography` advisories, and
`docker` was skipped on eight consecutive main runs.

Two modes enforce one invariant — every job in ci.yml is accounted for, and on
push:main every one of them actually ran:

``--workflow`` (static; runs in ``make drift-check`` and the ``lint`` job)
    Every job defined in ci.yml appears in the aggregator's ``needs:``. Without
    this, a new job is simply absent from the runtime check and covered by
    nothing — the population has to be enumerated from the workflow, not from
    the list someone remembered to update.

``--needs`` (runtime; runs inside the aggregator job)
    ``failure``/``cancelled`` always fail. ``skipped`` fails too under
    ``--enforce-ran``, which the workflow passes only for push:main — on
    pull_request and schedule the heavy jobs are skipped by design.

Failures name the offending job. The public mirror's aggregator collapsed its
inputs with ``join(needs.*.result)``, so its error read "a required CI job
failed" with no name attached — and its step was titled "Require no failures
(skipped is OK)", writing the hole into the gate itself.

Usage:
    python scripts/lint-ci-required-gates.py --workflow .github/workflows/ci.yml
    python scripts/lint-ci-required-gates.py --needs "$NEEDS_JSON" --enforce-ran
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

#: The job that aggregates every other job's result.
AGGREGATOR = "ci-ok"

#: Jobs deliberately outside the aggregator. Empty on purpose: a job that
#: cannot be covered has to be named here in a commit someone reviews, rather
#: than quietly falling out of the population.
EXEMPT: frozenset[str] = frozenset()

_JOB_KEY = re.compile(r"^  ([A-Za-z0-9_-]+):[ \t]*$")


def _parse_jobs(text: str) -> list[str]:
    """Job names = keys at exactly two-space indent under the top-level ``jobs:``.

    Parsed structurally rather than with PyYAML because this runs in the ``lint``
    job before any dependency install (the precedent is
    ``lint-ci-compose-namespacing.py``). Bodies of ``run: |`` blocks sit at eight
    spaces or deeper, so they cannot collide with a two-space key.
    """
    jobs: list[str] = []
    in_jobs = False
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if re.match(r"^jobs:[ \t]*$", line):
            in_jobs = True
            continue
        if not in_jobs:
            continue
        if re.match(r"^\S", line):  # dedent back to a top-level key
            break
        m = _JOB_KEY.match(line)
        if m:
            jobs.append(m.group(1))
    return jobs


def _job_block(text: str, job: str) -> str:
    m = re.search(rf"^  {re.escape(job)}:[ \t]*$", text, re.M)
    if not m:
        return ""
    rest = text[m.end() :]
    nxt = _JOB_KEY.search(rest)
    return rest[: nxt.start()] if nxt else rest


def _parse_needs(text: str, job: str) -> list[str]:
    """Read a job's ``needs:``, accepting both the flow and block sequence forms."""
    block = _job_block(text, job)
    flow = re.search(r"^    needs:[ \t]*\[", block, re.M)
    if flow:
        seg = block[flow.end() :]
        if "]" not in seg:
            return []
        return [s.strip() for s in seg[: seg.index("]")].split(",") if s.strip()]
    blk = re.search(r"^    needs:[ \t]*$", block, re.M)
    if blk:
        items: list[str] = []
        for line in block[blk.end() :].splitlines():
            if not line.strip():
                continue
            m = re.match(r"^      - ([A-Za-z0-9_-]+)[ \t]*$", line)
            if not m:
                break
            items.append(m.group(1))
        return items
    return []


def check_workflow(path: Path) -> list[str]:
    """Return violations: jobs the aggregator does not cover."""
    text = path.read_text(encoding="utf-8")
    jobs = _parse_jobs(text)
    if AGGREGATOR not in jobs:
        return [
            f"{path}: no {AGGREGATOR!r} job — every job's result must be "
            f"aggregated somewhere, or a skipped gate is invisible"
        ]
    covered = set(_parse_needs(text, AGGREGATOR))
    expected = set(jobs) - {AGGREGATOR} - EXEMPT
    problems = [
        f"{path}: job {name!r} is not in {AGGREGATOR}.needs — it would be "
        f"unmonitored; add it there (or to EXEMPT with a reason)"
        for name in sorted(expected - covered)
    ]
    problems += [
        f"{path}: {AGGREGATOR}.needs lists {name!r}, which is not a job in this "
        f"workflow — a rename left the aggregator pointing at nothing"
        for name in sorted(covered - set(jobs))
    ]
    return problems


def check_needs(raw: str, *, enforce_ran: bool) -> list[str]:
    """Return violations for a GitHub ``needs`` context payload."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return [f"needs payload is not valid JSON: {exc}"]
    if not isinstance(data, dict) or not data:
        return ["needs payload is empty — the aggregator guards nothing"]

    problems: list[str] = []
    for name in sorted(data):
        entry = data[name]
        result = entry.get("result") if isinstance(entry, dict) else entry
        if result in ("failure", "cancelled"):
            problems.append(f"job {name!r}: {result}")
        elif result == "skipped":
            if enforce_ran:
                problems.append(
                    f"job {name!r}: skipped — on main every gate must actually "
                    f"run; a skip here means a dependency failed and this gate "
                    f"never executed (its last real result is stale)"
                )
        elif result != "success":
            problems.append(f"job {name!r}: unexpected result {result!r}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workflow", type=Path, help="ci.yml to check statically")
    ap.add_argument("--needs", help="JSON of the GitHub `needs` context")
    ap.add_argument(
        "--enforce-ran",
        action="store_true",
        help="treat `skipped` as a failure (push:main only)",
    )
    ap.add_argument("--check", action="store_true", help="accepted for gate parity")
    args = ap.parse_args()

    if not args.workflow and args.needs is None:
        args.workflow = REPO / ".github" / "workflows" / "ci.yml"

    problems: list[str] = []
    if args.workflow:
        problems += check_workflow(args.workflow)
    if args.needs is not None:
        problems += check_needs(args.needs, enforce_ran=args.enforce_ran)

    if problems:
        for p in problems:
            print(f"::error::{p}" if args.needs is not None else f"  {p}")
        print(f"FAIL — {len(problems)} problem(s)", file=sys.stderr)
        return 1
    print("OK — every CI job is aggregated and accounted for")
    return 0


if __name__ == "__main__":
    sys.exit(main())
