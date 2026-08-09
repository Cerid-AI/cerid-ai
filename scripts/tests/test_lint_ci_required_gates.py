# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Red/green probe for scripts/lint-ci-required-gates.py.

Both halves matter. The green cases pin the shapes the gate must NOT flag —
a `run: |` body that happens to contain a two-space `word:` line, and the
pull_request case where skipping heavy jobs is the intended design. The
detector history in the `cerid-test-integrity` skill is that two TA006 designs
shipped wrong because they were only ever tested against what they should
catch.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO / "scripts" / "lint-ci-required-gates.py"


def _load():
    spec = importlib.util.spec_from_file_location("ci_required_gates", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gates = _load()


COVERED = """\
jobs:
  lint:
    runs-on: ubuntu-latest
  docker:
    needs: [lint]
  ci-ok:
    needs: [lint, docker]
"""


# --------------------------------------------------------------------------
# static mode
# --------------------------------------------------------------------------


def test_fully_covered_workflow_is_clean(tmp_path):
    wf = tmp_path / "ci.yml"
    wf.write_text(COVERED)
    assert gates.check_workflow(wf) == []


def test_job_missing_from_aggregator_is_flagged(tmp_path):
    """The exact regression: a gate exists but nothing aggregates its result."""
    wf = tmp_path / "ci.yml"
    wf.write_text(COVERED.replace("needs: [lint, docker]", "needs: [lint]"))
    problems = gates.check_workflow(wf)
    assert len(problems) == 1
    assert "'docker'" in problems[0]


def test_missing_aggregator_is_flagged(tmp_path):
    wf = tmp_path / "ci.yml"
    wf.write_text("jobs:\n  lint:\n    runs-on: ubuntu-latest\n")
    assert any("ci-ok" in p for p in gates.check_workflow(wf))


def test_aggregator_pointing_at_a_renamed_job_is_flagged(tmp_path):
    wf = tmp_path / "ci.yml"
    wf.write_text(COVERED.replace("needs: [lint, docker]", "needs: [lint, docker, gone]"))
    assert any("'gone'" in p for p in gates.check_workflow(wf))


def test_block_sequence_needs_form_parses(tmp_path):
    wf = tmp_path / "ci.yml"
    wf.write_text(
        "jobs:\n"
        "  lint:\n"
        "    runs-on: ubuntu-latest\n"
        "  docker:\n"
        "    runs-on: ubuntu-latest\n"
        "  ci-ok:\n"
        "    needs:\n"
        "      - lint\n"
        "      - docker\n"
    )
    assert gates.check_workflow(wf) == []


def test_run_block_content_is_not_mistaken_for_a_job(tmp_path):
    """A shell heredoc inside `run: |` must not register as a job name."""
    wf = tmp_path / "ci.yml"
    wf.write_text(
        "jobs:\n"
        "  lint:\n"
        "    steps:\n"
        "      - run: |\n"
        "          cat <<'EOF' > out.yml\n"
        "          notajob:\n"
        "            key: value\n"
        "          EOF\n"
        "  ci-ok:\n"
        "    needs: [lint]\n"
    )
    assert gates.check_workflow(wf) == []


def test_the_real_workflow_is_covered():
    """Standing claim: this repo's ci.yml aggregates every job it defines."""
    assert gates.check_workflow(REPO / ".github" / "workflows" / "ci.yml") == []


# --------------------------------------------------------------------------
# runtime mode
# --------------------------------------------------------------------------


def _needs(**kw) -> str:
    import json

    return json.dumps({k: {"result": v} for k, v in kw.items()})


def test_all_success_passes_under_enforcement():
    assert gates.check_needs(_needs(lint="success", docker="success"), enforce_ran=True) == []


def test_skip_is_allowed_when_not_enforcing():
    """pull_request / schedule skip the heavy jobs by design — must not flag."""
    assert gates.check_needs(_needs(lint="success", docker="skipped"), enforce_ran=False) == []


def test_skip_is_a_failure_on_main():
    """The 2026-08-03 recurrence: docker skipped behind a red security job."""
    problems = gates.check_needs(_needs(lint="success", docker="skipped"), enforce_ran=True)
    assert len(problems) == 1
    assert "'docker'" in problems[0]


@pytest.mark.parametrize("result", ["failure", "cancelled"])
@pytest.mark.parametrize("enforce", [True, False])
def test_failure_and_cancellation_always_flag(result, enforce):
    problems = gates.check_needs(_needs(security=result), enforce_ran=enforce)
    assert len(problems) == 1
    assert "'security'" in problems[0]


def test_every_offending_job_is_named():
    """`join(needs.*.result)` lost the names; that is why an 11-day gap read as noise."""
    problems = gates.check_needs(
        _needs(lint="success", security="failure", docker="skipped"), enforce_ran=True
    )
    assert len(problems) == 2
    assert any("'security'" in p for p in problems)
    assert any("'docker'" in p for p in problems)


def test_empty_payload_is_flagged():
    assert gates.check_needs("{}", enforce_ran=True) != []


def test_malformed_payload_is_flagged():
    assert gates.check_needs("not json", enforce_ran=True) != []
