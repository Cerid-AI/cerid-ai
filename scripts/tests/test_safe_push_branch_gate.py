# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""safe-push runs the FULL prepush for a direct push to main, the short set otherwise.

Ruling D1-A (2026-10-05): `make push` must not be able to land on main what
remote CI rejects. The short set (`pre-push --validate-only` = ci-local +
drift-check) is what every other branch keeps, because a PR still meets the
full CI before merge; main has no such second gate.

The real scripts/safe-push.sh runs against a fake toplevel: `git`, `make` and
the pre-push hook are stubs on PATH that log every invocation, so the
assertions are about which validation the script chose and whether the
validated-commit record was written. Nothing is pushed.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SHA = "a" * 40

# Answers every git question safe-push asks; `push` and `ls-remote` are
# answered without a network. The current branch comes from FAKE_BRANCH.
GIT_STUB = """#!/bin/bash
printf 'git %s\\n' "$*" >> "$CALL_LOG"
case "$1 ${2:-}" in
  "rev-parse --show-toplevel") echo "$FAKE_TOPLEVEL" ;;
  "rev-parse --git-dir") echo "$FAKE_TOPLEVEL/.git" ;;
  "rev-parse --abbrev-ref") echo "$FAKE_BRANCH" ;;
  "rev-parse "*) echo "@SHA@" ;;
  "diff "*|"ls-files "*|"tag "*|"status "*) exit 0 ;;
  "push "*) exit 0 ;;
  "ls-remote "*) printf '@SHA@\\trefs/heads/%s\\n' "$3" ;;
  *) echo "git stub: unexpected: $*" >&2; exit 97 ;;
esac
""".replace("@SHA@", SHA)

MAKE_STUB = """#!/bin/bash
printf 'make %s\\n' "$*" >> "$CALL_LOG"
exit "${FAKE_MAKE_EXIT:-0}"
"""

HOOK_STUB = """#!/bin/bash
printf 'hook %s\\n' "$*" >> "$CALL_LOG"
exit "${FAKE_HOOK_EXIT:-0}"
"""


def _stub(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(0o755)


def _run(tmp_path: Path, *args: str, branch: str, **env: str) -> tuple[subprocess.CompletedProcess, list[str], Path]:
    toplevel = tmp_path / "repo"
    (toplevel / ".git").mkdir(parents=True)
    (toplevel / "scripts" / "hooks").mkdir(parents=True)
    _stub(toplevel / "scripts" / "hooks" / "pre-push", HOOK_STUB)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub(bin_dir / "git", GIT_STUB)
    _stub(bin_dir / "make", MAKE_STUB)
    log = tmp_path / "calls.log"
    log.touch()
    run_env = {
        k: v for k, v in os.environ.items() if not k.startswith("GIT_") and k not in ("SAFE_PUSH_ALLOW_UNTRACKED",)
    }
    run_env.update(
        PATH=f"{bin_dir}:{os.environ['PATH']}",
        CALL_LOG=str(log),
        FAKE_TOPLEVEL=str(toplevel),
        FAKE_BRANCH=branch,
        **env,
    )
    proc = subprocess.run(
        ["bash", str(REPO / "scripts" / "safe-push.sh"), *args],
        cwd=tmp_path,
        env=run_env,
        capture_output=True,
        text=True,
        check=False,
    )
    calls = log.read_text().splitlines()
    return proc, calls, toplevel / ".git" / "prepush-validated"


def test_current_branch_main_runs_full_prepush(tmp_path: Path) -> None:
    proc, calls, stamp = _run(tmp_path, branch="main")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "make prepush" in calls
    assert "hook --validate-only" not in calls
    assert stamp.read_text().splitlines() == [SHA]
    assert "git push origin main" in calls
    assert "prepush" in proc.stdout


def test_other_branch_keeps_short_set(tmp_path: Path) -> None:
    proc, calls, stamp = _run(tmp_path, branch="work/feature")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "hook --validate-only" in calls
    assert not any(c.startswith("make ") for c in calls)
    assert stamp.read_text().splitlines() == [SHA]
    assert "git push origin work/feature" in calls


def test_explicit_main_ref_from_another_branch_runs_full_prepush(tmp_path: Path) -> None:
    proc, calls, _ = _run(tmp_path, "origin", "main", "--force-with-lease", branch="work/feature")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "make prepush" in calls
    assert "hook --validate-only" not in calls


def test_refspec_onto_main_runs_full_prepush(tmp_path: Path) -> None:
    proc, calls, _ = _run(tmp_path, "origin", "HEAD:main", branch="work/feature")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "make prepush" in calls


def test_other_ref_while_on_main_keeps_short_set(tmp_path: Path) -> None:
    proc, calls, _ = _run(tmp_path, "origin", "work/feature", branch="main")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "hook --validate-only" in calls
    assert "make prepush" not in calls


def test_failed_prepush_writes_no_record_and_pushes_nothing(tmp_path: Path) -> None:
    proc, calls, stamp = _run(tmp_path, branch="main", FAKE_MAKE_EXIT="1")
    assert proc.returncode == 1
    assert "make prepush" in calls
    assert not stamp.exists()
    assert not any(c.startswith("git push") for c in calls)
