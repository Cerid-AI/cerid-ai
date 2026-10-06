# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""The beta harness's eval tier fails when EITHER half fails.

tests/beta/run.sh runs entity-recall first and the pytest eval suite second,
and until 2026-10-05 the pytest status overwrote the recall result, so a
recall failure next to a green pytest run reported the tier as passed.

The tier block is lifted out of run.sh (the live stack and docker it needs
are not here) and run under bash with a stub `docker` whose exit status is
chosen per call shape, so the assertions are about how the tier combines the
two results into OVERALL_EXIT.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
RUN_SH = REPO / "tests" / "beta" / "run.sh"

DOCKER_STUB = """#!/bin/bash
case "$1" in
  exec) [[ -n "${FAKE_RECALL_OUTPUT:-}" ]] && echo "$FAKE_RECALL_OUTPUT"; exit "${FAKE_RECALL_EXIT:-0}" ;;
  run)  exit "${FAKE_PYTEST_EXIT:-0}" ;;
esac
exit 0
"""


def _tier_block() -> str:
    lines = RUN_SH.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("# TIER 7: EVALUATION"))
    end = next(i for i in range(start, len(lines)) if lines[i] == "fi")
    block = "\n".join(lines[start : end + 1])
    assert "entity_recall_runner" in block and "OVERALL_EXIT=1" in block
    return block


def _run(tmp_path: Path, recall_exit: int, pytest_exit: int, recall_output: str = "") -> str:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(DOCKER_STUB)
    docker.chmod(0o755)
    script = tmp_path / "tier.sh"
    script.write_text(
        "set -uo pipefail\n"
        "report_text() { printf '%b\\n' \"$*\"; }\n"
        "report_issue() { :; }\n"
        "report_section() { :; }\n"
        "skip_tier_mcp_unreachable() { :; }\n"
        "mcp_network_or_skip() { echo stubnet; }\n"
        "RUN_EVAL=true\n"
        "BETA_MCP_CONTAINER=stub-mcp\n"
        f'SCRIPT_DIR="{tmp_path / "beta"}"\n'
        f'EVAL_REPORT="{tmp_path / "eval-report.md"}"\n'
        "CERID_API_KEY=stub\n"
        "OVERALL_EXIT=0\n"
        f"{_tier_block()}\n"
        'echo "OVERALL_EXIT=$OVERALL_EXIT"\n'
    )
    env = dict(os.environ)
    env.update(
        PATH=f"{bin_dir}:{env['PATH']}",
        FAKE_RECALL_EXIT=str(recall_exit),
        FAKE_PYTEST_EXIT=str(pytest_exit),
        FAKE_RECALL_OUTPUT=recall_output,
    )
    proc = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True, check=False)
    return proc.stdout + proc.stderr


@pytest.mark.parametrize(
    ("recall_exit", "pytest_exit", "expected"),
    [
        (0, 0, 0),
        (1, 0, 1),  # the overwritten case: recall alone must fail the tier
        (0, 1, 1),
        (1, 1, 1),
    ],
)
def test_tier_exit_combines_recall_and_pytest(tmp_path: Path, recall_exit: int, pytest_exit: int, expected: int) -> None:
    out = _run(tmp_path, recall_exit, pytest_exit)
    assert f"OVERALL_EXIT={expected}" in out, out


def test_recall_skip_on_a_loaded_host_is_reported_not_passed(tmp_path: Path) -> None:
    """A skipped recall run exits 0 but must not read as the floor being met."""
    out = _run(tmp_path, 0, 0, recall_output="recall SKIPPED: inference server loaded: probe 7400ms > 3000ms")
    assert "OVERALL_EXIT=0" in out, out
    assert "NOT MEASURED — inference server loaded: probe 7400ms > 3000ms" in out, out
    assert "at or above the floor" not in out, out


def test_recall_pass_reports_the_floor_met(tmp_path: Path) -> None:
    out = _run(tmp_path, 0, 0, recall_output="recall[x.md] = 1.00 attempts=[1.00, 1.00, 1.00] spread=0.00 forbidden_hits=[] [PASS]")
    assert "OVERALL_EXIT=0" in out and "at or above the floor" in out, out
