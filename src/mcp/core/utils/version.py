# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Single source of truth for the package version.

Reads from ``pyproject.toml`` once at import time and caches the result.
All runtime consumers (``/``, ``/health``, ``FastAPI(version=...)``,
``openapi.json``) must call :func:`get_version`.
"""
from __future__ import annotations

import logging
import tomllib
from functools import cache
from pathlib import Path

logger = logging.getLogger("ai-companion.version")

_DEFAULT = "0.0.0"


@cache
def get_version() -> str:
    """Return the package version string.

    Priority:
    1. VERSION file (injected during Docker build)
    2. pyproject.toml (local development)
    3. Fallback to "0.0.0"

    Walks up from this file until a ``VERSION`` or ``pyproject.toml`` is found.
    Falls back to ``0.0.0`` if nothing is found — preferable to raising at startup.
    """
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        # Check for VERSION file first (Docker build artifact)
        version_file = parent / "VERSION"
        if version_file.is_file():
            try:
                version = version_file.read_text().strip()
                if version:
                    return version
            except Exception as exc:
                logger.warning("Failed to read %s: %s", version_file, exc)

        # Fall back to pyproject.toml
        candidate = parent / "pyproject.toml"
        if candidate.is_file():
            try:
                with candidate.open("rb") as f:
                    data = tomllib.load(f)
                version = (
                    data.get("project", {}).get("version")
                    or data.get("tool", {}).get("poetry", {}).get("version")
                )
                if version:
                    return str(version)
            except Exception as exc:
                logger.warning("Failed to parse %s: %s", candidate, exc)
                break
    return _DEFAULT
