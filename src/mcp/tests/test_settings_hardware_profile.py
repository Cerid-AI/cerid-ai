# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""CERID_HARDWARE_PROFILE resolution (F172).

``config.settings.CERID_HARDWARE_PROFILE`` is what the model-compatibility
gate (``core.routing.model_compat``, via ``app/routers/models.py``) keys on,
and ``is_incompatible`` is fail-open on an empty profile. Nothing in the repo
ever exported ``CERID_HARDWARE_PROFILE``: ``scripts/detect-gpu.sh`` exports
``CERID_GPU_TYPE`` and ``scripts/start-cerid.sh`` re-exports it as
``HOST_GPU_TYPE`` (the same ``nvidia | amd | amd-mac | metal | cpu``
vocabulary), so the profile read empty on every install and the amd-mac
denylist — the one hardware class it was written for — never fired.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_SRC_MCP = Path(__file__).resolve().parents[1]
_PROBE = "import config.settings as s; print(repr(s.CERID_HARDWARE_PROFILE))"


def _resolved_profile(**env_overrides: str | None) -> str:
    env = {
        k: v for k, v in os.environ.items()
        if k not in ("CERID_HARDWARE_PROFILE", "HOST_GPU_TYPE")
    }
    for key, value in env_overrides.items():
        if value is not None:
            env[key] = value
    out = subprocess.run(
        [sys.executable, "-c", _PROBE],
        env=env, cwd=str(_SRC_MCP), capture_output=True, text=True, check=True,
    )
    return out.stdout.strip().splitlines()[-1]


def test_profile_falls_back_to_the_variable_the_launcher_exports():
    assert _resolved_profile(HOST_GPU_TYPE="amd-mac") == "'amd-mac'"


def test_explicit_profile_still_wins_over_the_launcher_variable():
    assert _resolved_profile(
        CERID_HARDWARE_PROFILE="metal", HOST_GPU_TYPE="amd-mac",
    ) == "'metal'"


def test_profile_stays_empty_when_neither_is_set():
    assert _resolved_profile() == "''"


def test_amd_mac_denylist_fires_on_a_launcher_provided_profile():
    """End of the chain: the gate the profile exists to drive."""
    env = {k: v for k, v in os.environ.items() if k != "CERID_HARDWARE_PROFILE"}
    env["HOST_GPU_TYPE"] = "amd-mac"
    probe = (
        "import config.settings as s;"
        "from core.routing.model_compat import is_incompatible;"
        "print(is_incompatible('meta-llama/llama-3.2-3b-instruct', s.CERID_HARDWARE_PROFILE))"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe],
        env=env, cwd=str(_SRC_MCP), capture_output=True, text=True, check=True,
    )
    assert out.stdout.strip().splitlines()[-1] == "True"
