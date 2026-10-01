# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Contract test for what ``scripts/audit-python-deps.sh --native`` audits.

The image installs ``src/mcp/requirements.lock`` with ``--require-hashes``, but
the audit only ever looked at a fresh latest-in-range resolution of
requirements.txt. On 2026-10-01 the lock pinned urllib3 2.7.0, pypdf 6.15.0 and
oauthlib 3.3.1, each with a fixed advisory, and the security job stayed green
because the fresh tree had already moved past all three.

The real script runs against a stand-in interpreter (``PYTHON=``) that records
each pip-audit invocation, so no network, pip-audit or Docker is needed and the
assertions are about the commands the gate actually issues.
"""
from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "audit-python-deps.sh"

_FAKE_PY = """#!/usr/bin/env bash
[ "$1" = "-c" ] && exit 0
echo "$*" >> "$AUDIT_LOG"
case "$*" in
  *requirements.lock*) exit "${LOCK_RC:-0}" ;;
  *) exit "${ENV_RC:-0}" ;;
esac
"""


def _run(tmp_path: Path, lock_rc: int = 0, env_rc: int = 0) -> tuple[int, list[list[str]]]:
    fake = tmp_path / "python"
    fake.write_text(_FAKE_PY)
    fake.chmod(0o755)
    log = tmp_path / "calls.log"
    env = {
        **os.environ,
        "PYTHON": str(fake),
        "AUDIT_LOG": str(log),
        "LOCK_RC": str(lock_rc),
        "ENV_RC": str(env_rc),
    }
    proc = subprocess.run(
        ["bash", str(SCRIPT), "--native"], capture_output=True, text=True, env=env
    )
    calls = [shlex.split(line) for line in log.read_text().splitlines()] if log.exists() else []
    return proc.returncode, calls


def _ignores(argv: list[str]) -> set[str]:
    return {argv[i + 1] for i, a in enumerate(argv) if a == "--ignore-vuln"}


def _lock_call(calls: list[list[str]]) -> list[str]:
    hits = [c for c in calls if "src/mcp/requirements.lock" in c]
    assert len(hits) == 1, f"expected one lock audit, got {calls}"
    return hits[0]


def _env_call(calls: list[list[str]]) -> list[str]:
    hits = [c for c in calls if "-r" not in c]
    assert len(hits) == 1, f"expected one environment audit, got {calls}"
    return hits[0]


def test_the_lock_that_ships_is_audited_by_its_pins(tmp_path: Path) -> None:
    rc, calls = _run(tmp_path)
    assert rc == 0
    lock = _lock_call(calls)
    assert lock[:2] == ["-m", "pip_audit"]
    i = lock.index("-r")
    assert lock[i + 1] == "src/mcp/requirements.lock"
    # Pins only: no install and no resolution, so the linux-resolved lock audits
    # the same on any host.
    assert "--disable-pip" in lock
    assert "--require-hashes" in lock


def test_the_fresh_environment_is_still_audited(tmp_path: Path) -> None:
    rc, calls = _run(tmp_path)
    assert rc == 0
    assert _env_call(calls)[:2] == ["-m", "pip_audit"]


def test_both_audits_get_the_same_ignore_list(tmp_path: Path) -> None:
    _, calls = _run(tmp_path)
    lock, fresh = _ignores(_lock_call(calls)), _ignores(_env_call(calls))
    assert lock and lock == fresh
    assert "CVE-2026-45829" in lock


def test_a_lock_finding_fails_and_does_not_hide_the_fresh_audit(tmp_path: Path) -> None:
    rc, calls = _run(tmp_path, lock_rc=1)
    assert rc != 0
    _env_call(calls)


def test_a_fresh_tree_finding_still_fails(tmp_path: Path) -> None:
    rc, calls = _run(tmp_path, env_rc=1)
    assert rc != 0
    _lock_call(calls)
