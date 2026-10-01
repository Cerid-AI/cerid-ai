# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""The Makefile hands the operator's key to live-stack targets and to nothing else.

``unexport CERID_API_KEY`` keeps the key away from unit tests, which is why it
must stay; on 2026-09-30 it also took the key from ``preservation-check``, and
47 preservation tests answered 401 through make while the same pytest command
run by hand passed.

These run the real Makefile with real ``make`` in a scratch copy of the tree.
Every tool a recipe calls is a stub that records the environment it was given,
so the assertions are about what each recipe actually receives.

The same harness checks that a preservation run leaves the committed baseline
alone: a 401 run once wrote "passed 29, failed 47" over it.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
ENV_KEY = "placeholder-env-key"  # pragma: allowlist secret
DOTENV_KEY = "placeholder-dotenv-key"  # pragma: allowlist secret
DOTENV_NEO4J = "placeholder-dotenv-neo4j"  # pragma: allowlist secret

STUB = """#!/bin/sh
printf '%s\\t%s\\t%s\\t%s\\n' "$(basename "$0")" "${CERID_API_KEY-<unset>}" \\
  "${NEO4J_PASSWORD-<unset>}" "$*" >> "$STUB_LOG"
exit 0
"""

pytestmark = pytest.mark.skipif(shutil.which("make") is None, reason="make not installed")


BASELINE = Path("src/mcp/tests/eval/baselines/preservation.json")
WRITER = Path("scripts/write-preservation-baseline.py")


def _tree(tmp_path: Path, dotenv: str | None = None, stub_python3: bool = True) -> Path:
    root = tmp_path / "tree"
    (root / "src" / "mcp").mkdir(parents=True)
    shutil.copy(REPO / "Makefile", root / "Makefile")
    venv_bin = root / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    stub_bin = tmp_path / "stub-bin"
    stub_bin.mkdir()
    for directory, names in (
        (venv_bin, ("python", "ruff", "mypy", "lint-imports", "pytest")),
        (stub_bin, ("python3", "python") if stub_python3 else ("python",)),
    ):
        for name in names:
            stub = directory / name
            stub.write_text(STUB)
            stub.chmod(0o755)
    if dotenv is not None:
        (root / ".env").write_text(dotenv)
    # A fixture, not the repo's baseline: the eval baselines are internal-only,
    # so the public tree has no file to copy.
    (root / BASELINE).parent.mkdir(parents=True)
    (root / BASELINE).write_text(json.dumps({
        "passed": 245, "failed": 3, "skipped": 6, "total": 254,
        "last_run_at": "2026-07-23T22:31:12+00:00", "git_sha": "g0000000000",
        "source": "local",
    }, indent=2) + "\n")
    (root / WRITER).parent.mkdir(parents=True)
    shutil.copy(REPO / WRITER, root / WRITER)
    return root


def _make(root: Path, *args: str, **env: str) -> tuple[subprocess.CompletedProcess, list[list[str]]]:
    log = root.parent / "stub.log"
    log.write_text("")
    proc = subprocess.run(
        ["make", "--no-print-directory", *args],
        cwd=root,
        env={
            "PATH": f"{root.parent / 'stub-bin'}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(root.parent),
            "STUB_LOG": str(log),
            **env,
        },
        capture_output=True,
        text=True,
        timeout=60,
    )
    calls = [line.split("\t") for line in log.read_text().splitlines()]
    # The Makefile probes `.venv/bin/python -c 'import tomllib'` while it is
    # parsed; that is not a recipe.
    return proc, [c for c in calls if "import tomllib" not in c[3]]


def _call(calls: list[list[str]], marker: str) -> list[str]:
    matches = [c for c in calls if marker in c[3]]
    assert len(matches) == 1, calls
    return matches[0]


def test_preservation_check_gets_the_exported_key(tmp_path):
    root = _tree(tmp_path)
    proc, calls = _make(root, "preservation-check", CERID_API_KEY=ENV_KEY)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _call(calls, "tests/integration/")[1] == ENV_KEY


def test_preservation_check_reads_the_key_and_neo4j_password_from_dotenv(tmp_path):
    root = _tree(
        tmp_path,
        dotenv=f"OTHER=x\nCERID_API_KEY={DOTENV_KEY}\nNEO4J_PASSWORD={DOTENV_NEO4J}\n",
    )
    proc, calls = _make(root, "preservation-check")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    call = _call(calls, "tests/integration/")
    assert call[1] == DOTENV_KEY
    assert call[2] == DOTENV_NEO4J


def test_the_exported_key_wins_over_dotenv(tmp_path):
    root = _tree(tmp_path, dotenv=f"CERID_API_KEY={DOTENV_KEY}\n")
    proc, calls = _make(root, "preservation-check", CERID_API_KEY=ENV_KEY)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _call(calls, "tests/integration/")[1] == ENV_KEY


@pytest.mark.parametrize(
    ("target", "marker"),
    [
        ("validate-pro", "tests/integration/test_preservation_apple_connectors.py"),
        ("eval-live-retrieval", "tests.eval.live_retrieval_eval"),
        ("eval-chat-faithfulness", "tests.eval.chat_faithfulness_eval"),
        ("eval-verdict", "tests.eval.verification_verdict_eval"),
        ("slo", "tests/test_latency_slo.py"),
        ("smoke", "tests/load/smoke.py"),
        ("pro-feature-health", "scripts/lint-pro-feature-health.py"),
    ],
)
def test_every_live_stack_target_gets_the_key(tmp_path, target, marker):
    root = _tree(tmp_path)
    proc, calls = _make(root, target, CERID_API_KEY=ENV_KEY)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _call(calls, marker)[1] == ENV_KEY


@pytest.mark.parametrize("source", ["environment", "dotenv"])
def test_ci_local_never_sees_the_key(tmp_path, source):
    root = _tree(tmp_path, dotenv=f"CERID_API_KEY={DOTENV_KEY}\n" if source == "dotenv" else None)
    env = {"CERID_API_KEY": ENV_KEY} if source == "environment" else {}
    # ci-local stops at the frontend step (no src/web here); every backend
    # tool, the unit-test pytest included, has run by then.
    _, calls = _make(root, "ci-local", **env)
    unit_tests = _call(calls, "not benchmark_slo and not preservation")
    assert unit_tests[1] == "<unset>"
    assert [c[0] for c in calls if c[1] != "<unset>"] == []
    assert {"ruff", "mypy", "lint-imports", "pytest", "python"} <= {c[0] for c in calls}


def test_unit_test_target_never_sees_the_key(tmp_path):
    root = _tree(tmp_path, dotenv=f"CERID_API_KEY={DOTENV_KEY}\n")
    proc, calls = _make(root, "test", CERID_API_KEY=ENV_KEY)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _call(calls, "-m pytest tests/")[1] == "<unset>"


def test_dry_run_does_not_print_the_key(tmp_path):
    root = _tree(tmp_path, dotenv=f"CERID_API_KEY={DOTENV_KEY}\n")
    proc, _ = _make(root, "-n", "preservation-check", "validate-pro", CERID_API_KEY=ENV_KEY)
    assert proc.returncode == 0, proc.stderr
    assert ENV_KEY not in proc.stdout + proc.stderr
    assert DOTENV_KEY not in proc.stdout + proc.stderr


def test_preservation_check_does_not_rewrite_the_committed_baseline(tmp_path):
    root = _tree(tmp_path)
    before = (root / BASELINE).read_bytes()
    proc, calls = _make(root, "preservation-check")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert [c for c in calls if str(WRITER) in c[3]] == []
    assert (root / BASELINE).read_bytes() == before


def _junit(path: Path, tests: int, failures: int, skipped: int) -> Path:
    path.write_text(
        f'<testsuites><testsuite name="pytest" tests="{tests}" errors="0" '
        f'failures="{failures}" skipped="{skipped}"/></testsuites>'
    )
    return path


def test_preservation_baseline_records_the_named_run(tmp_path):
    root = _tree(tmp_path, stub_python3=False)
    junit = _junit(tmp_path / "run.xml", tests=10, failures=1, skipped=2)
    proc, _ = _make(root, "preservation-baseline", f"PRESERVATION_JUNIT={junit}")
    # A run with failures is still recorded; recording it is not a failure.
    assert proc.returncode == 0, proc.stdout + proc.stderr
    written = json.loads((root / BASELINE).read_text())
    assert (written["passed"], written["failed"], written["skipped"], written["total"]) == (7, 1, 2, 10)
    assert written["source"] == "local"


def test_preservation_baseline_without_a_run_fails_and_keeps_the_baseline(tmp_path):
    root = _tree(tmp_path, stub_python3=False)
    before = (root / BASELINE).read_bytes()
    proc, _ = _make(root, "preservation-baseline", f"PRESERVATION_JUNIT={tmp_path / 'missing.xml'}")
    assert proc.returncode != 0
    assert (root / BASELINE).read_bytes() == before


def test_preservation_check_writes_its_junit_where_preservation_baseline_reads_it(tmp_path):
    root = _tree(tmp_path)
    junit = tmp_path / "run.xml"
    _, calls = _make(root, "preservation-check", f"PRESERVATION_JUNIT={junit}")
    assert f"--junit-xml={junit}" in _call(calls, "tests/integration/")[3]
