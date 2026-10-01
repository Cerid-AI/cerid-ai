# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Each self-hosted runner gets its own pip cache.

The four mac-pro runners share one $HOME, so they bind-mounted one pip HTTP
cache. Parallel installs raced on it: an index page's cache entry vanished
between pip's two reads, pip was left with PyPI's bare 304 (no Content-Type),
and the job died with "Skipping page ... Content-Type: Unknown" and
"from versions: none". Seven jobs between 2026-09-27 and 10-01.
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO / "scripts" / "ci-in-docker.sh"


def _run(tmp_path: Path, runner_name: str | None) -> list[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "docker-args"
    stub = bin_dir / "docker"
    stub.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > "{log}"\n')
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "CI_CONTAINER_CACHE": str(tmp_path / "cache"),
    }
    if runner_name is not None:
        env["RUNNER_NAME"] = runner_name
    subprocess.run(["bash", str(SCRIPT), "python:3.12", "true"], env=env, cwd=tmp_path, check=True,
                   capture_output=True)
    return log.read_text().splitlines()


def _pip_mount(args: list[str]) -> str:
    mounts = [args[i + 1] for i, a in enumerate(args[:-1]) if a == "-v"]
    return next(m for m in mounts if m.endswith(":/root/.cache/pip"))


def test_a_named_runner_gets_its_own_pip_cache(tmp_path: Path) -> None:
    mount = _pip_mount(_run(tmp_path, "mac-pro-3"))
    assert mount == f"{tmp_path / 'cache'}/pip-mac-pro-3:/root/.cache/pip"
    assert (tmp_path / "cache" / "pip-mac-pro-3").is_dir()


def test_runner_names_are_made_path_safe(tmp_path: Path) -> None:
    mount = _pip_mount(_run(tmp_path, "mac pro/4"))
    assert mount == f"{tmp_path / 'cache'}/pip-mac_pro_4:/root/.cache/pip"


def test_outside_a_runner_the_shared_cache_is_kept(tmp_path: Path) -> None:
    mount = _pip_mount(_run(tmp_path, None))
    assert mount == f"{tmp_path / 'cache'}/pip:/root/.cache/pip"
    assert os.path.isdir(tmp_path / "cache" / "pip")
