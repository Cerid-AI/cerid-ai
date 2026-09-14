# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Contract test for the Trivy cache path in ``scripts/ci/docker-gate.sh``.

Trivy takes an EXCLUSIVE lock on its cache directory. The gate mounted ONE
directory under ``$HOME``, and the two self-hosted runners share a ``$HOME`` — so
two ``docker`` jobs at once raced for one lock and the loser died with::

    ERROR  Failed to acquire cache or database lock
    FATAL  unable to initialize fs cache: cache may be in use by another process: timeout

Observed 2026-09-13 while a dependabot backlog drained across both runners. A
rerun clears it, which is the shape that gets rerun forever instead of fixed.

WHAT IS ASSERTED IS THE KEY, NOT THE LOCK. A runner executes one job at a time,
so a per-runner path cannot collide by construction. That is the property worth
pinning: the race itself is timing-dependent and does not reproduce on demand
(fifteen concurrent shared-cache scans locally produced zero failures after one
early crash), so a test that tried to provoke it would pass while broken.

This is a pytest module, not the sibling ``.sh``, because CI runs
``pytest scripts/tests/`` — a shell file in this directory is never collected and
never runs. ``test_docker_gate_prune.sh`` is in exactly that position.

Needs no daemon and no Trivy: the assignment is lifted from the gate and
evaluated by bash against different ``RUNNER_NAME`` values.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path, PurePosixPath

import pytest

ROOT = Path(__file__).resolve().parents[2]
GATE = ROOT / "scripts" / "ci" / "docker-gate.sh"
SHARED = "/h/.cache/cerid-ci/trivy"


def _assignment() -> str:
    for line in GATE.read_text().splitlines():
        if line.startswith("TRIVY_CACHE_DIR="):
            return line
    raise AssertionError(f"no TRIVY_CACHE_DIR assignment in {GATE}")


def path_for(runner_name: str | None) -> str:
    """Evaluate the gate's own expression with a fixed HOME."""
    prelude = "unset RUNNER_NAME" if runner_name is None else ""
    script = f'{prelude}\n{_assignment()}\nprintf "%s" "$TRIVY_CACHE_DIR"'
    env = {"HOME": "/h"}
    if runner_name is not None:
        env["RUNNER_NAME"] = runner_name
    out = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env=env, check=True
    )
    return out.stdout


def test_the_two_self_hosted_runners_cannot_share_a_lock() -> None:
    assert path_for("mac-pro-1") != path_for("mac-pro-2")


def test_the_same_runner_is_stable_or_the_db_is_never_warm() -> None:
    # A per-RUN key would also remove the contention — and the whole point of the
    # mount with it, since every run would re-download the vulnerability DB.
    assert path_for("mac-pro-1") == path_for("mac-pro-1")
    assert path_for("mac-pro-1") != SHARED


@pytest.mark.parametrize(
    "runner_name",
    ["GitHub Actions 12", "weird/../../etc", "a b\tc", "..", "with'quote"],
)
def test_a_runner_name_yields_one_contained_path_segment(runner_name: str) -> None:
    """Hosted runner names contain spaces; nothing may escape the cache root.

    Asserted on the real final PATH COMPONENT, not on the name with the prefix
    stripped off. Those differ exactly where it matters: ``RUNNER_NAME=".."``
    gives ``trivy-..``, which is an ordinary directory name — a traversal needs a
    component that IS ``..``, and the constant ``trivy-`` prefix means one never
    can be. Checking the stripped suffix instead reads ``..`` and fails a path
    that is fine, which is what the first cut of this test did.
    """
    path = path_for(runner_name)
    assert path.startswith(SHARED + "-"), path
    component = PurePosixPath(path).name
    assert re.fullmatch(r"[A-Za-z0-9_.-]+", component), f"unsanitised: {component!r}"
    assert component not in (".", ".."), component
    # One component: the sanitiser turned every separator into an underscore.
    assert PurePosixPath(path).parent == PurePosixPath(SHARED).parent


def test_unset_runner_name_falls_back_to_the_original_shared_path() -> None:
    # Correct anywhere that is not a multi-runner host, and keeps a local run
    # using the cache it already warmed.
    assert path_for(None) == SHARED


def test_the_mount_actually_uses_the_variable() -> None:
    # Computing the path and then mounting the old one would pass every test
    # above while changing nothing.
    assert '-v "$TRIVY_CACHE_DIR":/root/.cache/trivy' in GATE.read_text()
