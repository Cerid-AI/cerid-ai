# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""start-cerid.sh --build removes the dangling images its rebuild leaves behind.

Each rebuild re-tags the images and leaves the previous ones (about 10 GB for
mcp-server) dangling. Nothing removed them, and on 2026-10-01 they filled the
Colima VM and took a sibling stack's Postgres down with ENOSPC.

The whole script runs, from a copy in a temp tree, against stubs of docker,
curl, make, lsof and sleep on PATH; the docker stub logs every invocation, so
the assertions are about which commands the script issues and in what order.
No daemon is touched.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

DOCKER_STUB = """#!/bin/bash
printf '%s\\n' "$*" >> "$DOCKER_LOG"
if [ "$1 $2" = "image prune" ] && [ -n "${PRUNE_FAIL:-}" ]; then
    echo "Error response from daemon: a prune operation is already running" >&2
    exit 1
fi
exit 0
"""

# Succeeds for every URL except one containing $CURL_FAIL_MATCH, so a single
# service can be made to time out.
CURL_STUB = """#!/bin/bash
for a in "$@"; do
    if [ -n "${CURL_FAIL_MATCH:-}" ] && [[ "$a" == *"$CURL_FAIL_MATCH"* ]]; then
        exit 7
    fi
done
exit 0
"""


def _stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(body)
    path.chmod(0o755)


def _run(tmp_path: Path, *args: str, **env: str) -> tuple[subprocess.CompletedProcess, list[str]]:
    root = tmp_path / "cerid"
    (root / "scripts" / "lib").mkdir(parents=True)
    shutil.copy(REPO / "scripts" / "start-cerid.sh", root / "scripts" / "start-cerid.sh")
    shutil.copy(REPO / "scripts" / "lib" / "healthcheck.sh", root / "scripts" / "lib" / "healthcheck.sh")
    (root / "docker-compose.yml").write_text("services: {}\n")
    (root / ".env").write_text("OLLAMA_ENABLED=false\n")
    home = tmp_path / "home"
    home.mkdir()

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub(bin_dir, "docker", DOCKER_STUB)
    _stub(bin_dir, "curl", CURL_STUB)
    for name in ("make", "sleep"):
        _stub(bin_dir, name, "#!/bin/bash\nexit 0\n")
    _stub(bin_dir, "lsof", "#!/bin/bash\nexit 1\n")

    log = tmp_path / "docker.log"
    log.touch()
    proc = subprocess.run(
        ["bash", str(root / "scripts" / "start-cerid.sh"), "--force", *args],
        cwd=root,
        env={
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "HOME": str(home),
            "DOCKER_LOG": str(log),
            # Pre-set so the script skips system_profiler and LAN detection.
            "HOST_MEMORY_GB": "64",
            "HOST_OS": "test",
            "HOST_CPU": "test",
            "HOST_CPU_CORES": "8",
            "HOST_GPU": "none",
            "HOST_GPU_ACCEL": "none",
            "RERANK_ONNX_FILENAME": "onnx/model.onnx",
            "CERID_HOST": "localhost",
            **env,
        },
        capture_output=True,
        text=True,
        timeout=120,
    )
    return proc, log.read_text().splitlines()


def _prunes(calls: list[str]) -> list[str]:
    return [c for c in calls if "prune" in c.split()]


@pytest.mark.parametrize("mode", [(), ("--legacy",)], ids=["unified", "legacy"])
def test_a_healthy_build_prunes_dangling_images_after_compose_up(tmp_path, mode):
    proc, calls = _run(tmp_path, "--build", *mode)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _prunes(calls) == ["image prune -f"]
    last_build = max(i for i, c in enumerate(calls) if c.startswith("compose") and "--build" in c.split())
    assert calls.index("image prune -f") > last_build


def test_a_start_without_build_does_not_prune(tmp_path):
    proc, calls = _run(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert any(c.startswith("compose") and " up " in f" {c} " for c in calls)
    assert _prunes(calls) == []


def test_a_build_whose_services_do_not_come_up_does_not_prune(tmp_path):
    proc, calls = _run(tmp_path, "--build", CURL_FAIL_MATCH="/health/ping")
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert _prunes(calls) == []


def test_a_failing_prune_does_not_fail_the_start(tmp_path):
    proc, calls = _run(tmp_path, "--build", PRUNE_FAIL="1")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _prunes(calls) == ["image prune -f"]
    assert "prune failed" in proc.stdout + proc.stderr


# cerid-sso runs a bind-mounted script on a stock image, so --build alone never
# replaced its process (the 1.0.8 sign-in limiter stayed out of effect).
@pytest.mark.parametrize("mode", [(), ("--legacy",)], ids=["unified", "legacy"])
def test_a_build_restarts_the_sign_in_service_after_compose_up(tmp_path, mode):
    proc, calls = _run(tmp_path, "--build", *mode)
    assert proc.returncode == 0, proc.stderr
    assert "restart cerid-sso" in calls
    last_up = max(i for i, c in enumerate(calls) if c.startswith("compose") and " up " in f" {c} ")
    assert calls.index("restart cerid-sso") > last_up


def test_a_start_without_build_does_not_restart_the_sign_in_service(tmp_path):
    proc, calls = _run(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "restart cerid-sso" not in calls
