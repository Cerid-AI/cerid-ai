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


# --- Transient lookup failures retry; findings do not -------------------------
#
# pip-audit exits 1 both for a finding and for a vulnerability-service failure.
# On 2026-10 two PRs went red on a single OSV 503 (an uncaught HTTPError
# traceback) that a rerun cleared. The script retries what reads as a lookup
# failure and fails at once on anything else. These fakes put a `pip-audit`
# on PATH, which the stand-in interpreter execs for `-m pip_audit`, so the
# retry is exercised through the script's real invocation form.

_DELEGATING_PY = """#!/usr/bin/env bash
[ "$1" = "-c" ] && exit 0
if [ "$1" = "-m" ] && [ "$2" = "pip_audit" ]; then shift 2; exec pip-audit "$@"; fi
exit 97
"""

_FAKE_PIP_AUDIT = """#!/usr/bin/env bash
case "$*" in *requirements.lock*) key=lock ;; *) key=env ;; esac
echo "$key $*" >> "$AUDIT_LOG"
n=$(grep -c "^$key " "$AUDIT_LOG")
if [ -n "${VULN:-}" ]; then
  echo "Found 1 known vulnerability in 1 package"
  echo "Name   Version ID             Fix Versions"
  exit 1
fi
if [ "$n" -le "${FAIL_TIMES:-0}" ]; then
  echo "Traceback (most recent call last):" >&2
  echo "requests.exceptions.HTTPError: 503 Server Error: Service Unavailable for url: https://api.osv.dev/v1/querybatch" >&2
  exit 1
fi
echo "No known vulnerabilities found"
"""


def _run_with_fake_pip_audit(tmp_path: Path, **fake_env: str) -> tuple[int, str, dict[str, int]]:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "python").write_text(_DELEGATING_PY)
    (bindir / "pip-audit").write_text(_FAKE_PIP_AUDIT)
    for f in bindir.iterdir():
        f.chmod(0o755)
    log = tmp_path / "calls.log"
    env = {
        **os.environ,
        "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
        "PYTHON": str(bindir / "python"),
        "AUDIT_LOG": str(log),
        "AUDIT_RETRY_BASE_DELAY": "0",
        **fake_env,
    }
    proc = subprocess.run(["bash", str(SCRIPT), "--native"], capture_output=True, text=True, env=env)
    lines = log.read_text().splitlines() if log.exists() else []
    counts = {"lock": sum(l.startswith("lock ") for l in lines), "env": sum(l.startswith("env ") for l in lines)}
    return proc.returncode, proc.stdout + proc.stderr, counts


def test_a_transient_lookup_failure_is_retried_and_then_passes(tmp_path: Path) -> None:
    rc, output, counts = _run_with_fake_pip_audit(tmp_path, FAIL_TIMES="2")
    assert rc == 0, output
    assert counts == {"lock": 3, "env": 3}
    assert "retrying" in output


def test_a_real_finding_fails_without_retrying(tmp_path: Path) -> None:
    rc, output, counts = _run_with_fake_pip_audit(tmp_path, VULN="1")
    assert rc != 0
    assert counts == {"lock": 1, "env": 1}, output
    assert "Found 1 known vulnerability" in output


def test_a_persistent_lookup_failure_stops_after_three_attempts(tmp_path: Path) -> None:
    rc, output, counts = _run_with_fake_pip_audit(tmp_path, FAIL_TIMES="99")
    assert rc != 0
    assert counts == {"lock": 3, "env": 3}, output
