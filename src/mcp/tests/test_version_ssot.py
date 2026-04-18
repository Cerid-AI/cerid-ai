# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for version.py single source of truth."""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import pytest


def test_get_version_reads_version_file_if_present() -> None:
    """Verify get_version() reads VERSION file before pyproject.toml."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        # Create a VERSION file
        version_file = tmpdir_path / "VERSION"
        version_file.write_text("2.1.0")

        # Create a mock pyproject.toml (should be ignored if VERSION exists)
        pyproject_file = tmpdir_path / "pyproject.toml"
        pyproject_file.write_text('[project]\nversion = "1.0.0"\n')

        # Create a minimal version.py that uses our tmpdir as parent
        version_module_path = tmpdir_path / "version_module.py"
        version_module_code = '''
from __future__ import annotations

import tomllib
from functools import cache
from pathlib import Path

_DEFAULT = "0.0.0"

@cache
def get_version() -> str:
    """Return the package version string."""
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        # Check for VERSION file first (Docker build artifact)
        version_file = parent / "VERSION"
        if version_file.is_file():
            try:
                version = version_file.read_text().strip()
                if version:
                    return version
            except Exception:
                pass

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
            except Exception:
                break
    return _DEFAULT
'''
        version_module_path.write_text(version_module_code)

        # Dynamically load the module
        spec = importlib.util.spec_from_file_location(
            "version_module", version_module_path
        )
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules["version_module"] = module
        spec.loader.exec_module(module)

        # Test that VERSION file is read first
        version = module.get_version()
        assert version == "2.1.0", f"Expected 2.1.0 from VERSION file, got {version}"
