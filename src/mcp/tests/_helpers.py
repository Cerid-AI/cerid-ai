# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared test helpers — deliberately small, stable surface.

This module is the home for utilities that must be robust across
every environment we run tests in: host macOS, host Linux, the CI
runner, and the ``ai-companion-mcp`` container where ``/app`` is a
shallow mount of ``src/mcp``. Depth-counting path walks
(``Path(__file__).parents[3]``) break in the shallow-mount case;
``repo_root()`` works everywhere by walking upward until it finds
an anchor file.
"""
from __future__ import annotations

from pathlib import Path

_ANCHORS: tuple[str, ...] = ("pyproject.toml", ".git")


def repo_root() -> Path | None:
    """Return the repository root by walking upward from this file.

    Stops at the first directory that contains ``pyproject.toml`` or
    ``.git``. Returns ``None`` when neither is found — callers should
    treat this as "we are running outside a checkout" (e.g. the
    ``ai-companion-mcp`` container where only ``src/mcp`` is bind-mounted
    at ``/app``) and skip tests that need repo-root resources.

    Prefer this over ``Path(__file__).resolve().parents[N]`` — hardcoded
    depth N is CI-runner-specific and silently breaks in shallower
    mounts.
    """
    here = Path(__file__).resolve()
    for candidate in (here, *here.parents):
        for anchor in _ANCHORS:
            if (candidate / anchor).exists():
                return candidate
    return None


def scripts_dir() -> Path | None:
    """Return ``<repo_root>/scripts`` if it exists, else None.

    Shorthand for tests that need to invoke repo-root scripts
    (``gen_env_example.py``, ``lint-no-silent-catch.py``, ...).
    """
    root = repo_root()
    if root is None:
        return None
    d = root / "scripts"
    return d if d.exists() else None
