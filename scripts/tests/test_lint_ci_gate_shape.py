# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Plant-fault probe for scripts/lint-ci-gate-shape.py.

Every mutation below is a real revert of the PR-time-gating work, and each one
passed `lint-ci-required-gates.py`, `actionlint` and the whole `scripts/tests/`
suite before this gate existed. The two the audit demonstrated by hand are
`test_deleting_sdk_contract_and_packages_is_caught` and
`test_excluding_pull_request_from_test_is_caught`.

The mutations are applied to the PARSED ci.yml, not to a fixture, so the probe
degrades into an obvious failure if the workflow is restructured rather than
quietly checking a document the repository no longer ships.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO / "scripts" / "lint-ci-gate-shape.py"
CI_YML = REPO / ".github" / "workflows" / "ci.yml"


def _load():
    spec = importlib.util.spec_from_file_location("ci_gate_shape", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


shape = _load()


@pytest.fixture
def doc():
    return yaml.safe_load(CI_YML.read_text(encoding="utf-8"))


def _flagged(document) -> list[str]:
    problems = shape.check_document(document)
    assert problems, "mutation was not caught — the gate is vacuous"
    return problems


# --------------------------------------------------------------------------
# green: the shipped workflow
# --------------------------------------------------------------------------


def test_the_real_workflow_has_the_required_shape():
    assert shape.check_workflow(CI_YML) == []


# --------------------------------------------------------------------------
# red: the mutations the audit performed by hand
# --------------------------------------------------------------------------


def test_deleting_sdk_contract_and_packages_is_caught(doc):
    """Delete both jobs AND their ci-ok.needs entries — the revert that leaves
    lint-ci-required-gates.py reporting OK, because what remains is still
    internally consistent."""
    for job in ("sdk-contract", "packages"):
        del doc["jobs"][job]
        doc["jobs"]["ci-ok"]["needs"].remove(job)
    problems = _flagged(doc)
    assert any("'sdk-contract' is missing" in p for p in problems)
    assert any("'packages' is missing" in p for p in problems)


def test_excluding_pull_request_from_test_is_caught(doc):
    """Restore `&& github.event_name != 'pull_request'` on `test`: the whole
    suite goes back to running only after the merge."""
    doc["jobs"]["test"]["if"] = (
        f"({doc['jobs']['test']['if']}) && github.event_name != 'pull_request'"
    )
    assert any(
        "job 'test': does not run on a human pull_request" in p for p in _flagged(doc)
    )


@pytest.mark.parametrize("job", shape.PRE_MERGE_GATES)
def test_moving_any_pre_merge_gate_behind_the_merge_is_caught(doc, job):
    doc["jobs"][job]["if"] = "github.event_name != 'pull_request'"
    assert any(f"job {job!r}: does not run on a human pull_request" in p for p in _flagged(doc))


@pytest.mark.parametrize("job", shape.PRE_MERGE_GATES)
def test_making_a_pre_merge_gate_pr_only_is_caught(doc, job):
    """push:main takes direct mirror-sync pushes; a PR-only gate misses them."""
    doc["jobs"][job]["if"] = "github.event_name == 'pull_request'"
    assert any(f"job {job!r}: does not run on push:main" in p for p in _flagged(doc))


def test_scheduling_a_gate_onto_merge_group_alone_is_caught(doc):
    """The H007 shape: a gate whose only trigger is an event that never fires."""
    doc["jobs"]["docker"]["if"] = "github.event_name == 'merge_group'"
    assert any("reachable only through merge_group" in p for p in _flagged(doc))


def test_the_pre_rewrite_docker_guard_is_caught(doc):
    """Exactly what `docker` carried before this branch: merge_group or push:main."""
    doc["jobs"]["docker"]["if"] = (
        "github.event_name == 'merge_group'"
        " || (github.event_name == 'push' && github.ref == 'refs/heads/main')"
    )
    assert any("job 'docker': does not run on a human pull_request" in p for p in _flagged(doc))


def test_dropping_a_job_from_the_aggregator_is_caught(doc):
    doc["jobs"]["ci-ok"]["needs"].remove("security")
    assert any("does not cover 'security'" in p for p in _flagged(doc))


def test_removing_the_dependabot_filter_is_caught(doc):
    doc["jobs"]["test"]["if"] = "needs.changes.outputs.code == 'true'"
    assert any("no effective rule-6 dependabot filter" in p for p in _flagged(doc))


def test_a_dependabot_filter_with_no_opt_out_label_is_caught(doc):
    doc["jobs"]["test"]["if"] = (
        "needs.changes.outputs.code == 'true' && github.actor != 'dependabot[bot]'"
    )
    assert any("does not opt back in" in p for p in _flagged(doc))


def test_removing_an_area_path_filter_is_caught(doc):
    for step in doc["jobs"]["frontend"]["steps"]:
        step.pop("if", None)
    assert any("no rule-3 filter on needs.changes.outputs.web" in p for p in _flagged(doc))


def test_deleting_the_widget_assertion_is_caught(doc):
    steps = doc["jobs"]["docker"]["steps"]
    doc["jobs"]["docker"]["steps"] = [
        s for s in steps if shape.WIDGET_COMMAND not in str(s.get("run") or "")
    ]
    assert any("never runs" in p for p in _flagged(doc))


def test_a_widget_assertion_that_only_appears_in_a_comment_is_caught(doc):
    """The substring trap the audit named: `'/app/static/cerid-widget.js' in
    text` passes when the literal survives only as a comment."""
    for step in doc["jobs"]["docker"]["steps"]:
        run = str(step.get("run") or "")
        if shape.WIDGET_COMMAND in run:
            step["run"] = "\n".join(f"# {line}" for line in run.splitlines())
    assert any("never runs" in p for p in _flagged(doc))


def test_dropping_the_pull_request_trigger_is_caught(doc):
    doc[True].pop("pull_request")
    assert any("no `on: pull_request` trigger" in p for p in _flagged(doc))


def test_dropping_concurrency_cancellation_is_caught(doc):
    doc["concurrency"]["cancel-in-progress"] = False
    assert any("cancel-in-progress is not true" in p for p in _flagged(doc))


def test_an_unmodelled_context_path_is_caught(doc):
    """A guard this gate cannot evaluate must fail, not read as "runs"."""
    doc["jobs"]["test"]["if"] = "vars.SOMETHING == '1' && env.NOPE == 'x'"
    assert any("unmodelled context path" in p for p in _flagged(doc))


def test_an_unmodelled_changes_output_is_caught(doc):
    doc["jobs"]["test"]["if"] = "needs.changes.outputs.invented == 'true'"
    assert any("does not model" in p for p in _flagged(doc))


# --------------------------------------------------------------------------
# the expression evaluator itself
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        ("github.event_name == 'pull_request'", True),
        ("github.event_name != 'pull_request'", False),
        ("!(github.event_name == 'pull_request')", False),
        ("false || github.event_name == 'pull_request'", True),
        ("always()", True),
        ("github.event.pull_request.draft == false", True),
        ("contains(github.event.pull_request.labels.*.name, 'dependabot-full-ci')", False),
        ("needs.changes.outputs.code == 'true' && needs.changes.outputs.web == 'true'", True),
        # An unset repo variable: the escape hatch must read as "gate runs".
        ("vars.HEAVY_GATES_PRE_MERGE != '1'", True),
    ],
)
def test_expression_evaluation(expr, expected):
    assert shape.evaluate(expr, shape.PR_HUMAN) is expected


def test_dependabot_label_opts_back_in():
    expr = "contains(github.event.pull_request.labels.*.name, 'dependabot-full-ci')"
    assert shape.evaluate(expr, shape.PR_DEPENDABOT_OPTED_IN) is True


def test_an_uninterpretable_expression_raises():
    with pytest.raises(shape.ExprError):
        shape.evaluate("${{ github.event_name }}-suffix", shape.PR_HUMAN)
