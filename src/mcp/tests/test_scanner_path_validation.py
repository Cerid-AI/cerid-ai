# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the scan-path allowlist guard in app.routers.scanner.

Regression: ``_validate_scan_path`` used a raw string ``startswith`` on the
resolved path, so a sibling-prefix directory (``/archive-secrets``) passed the
``/archive`` allowlist. The fix uses path-boundary containment.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

import config
from app.routers.scanner import _validate_scan_path


def test_rejects_sibling_prefix_directory(tmp_path, monkeypatch):
    allowed = tmp_path / "archive"
    allowed.mkdir()
    sibling = tmp_path / "archive-secrets"  # shares the '/archive' string prefix
    sibling.mkdir()
    monkeypatch.setattr(config, "SCAN_PATHS", str(allowed), raising=False)

    with pytest.raises(HTTPException) as exc:
        _validate_scan_path(str(sibling))
    assert exc.value.status_code == 403


def test_allows_root_and_subdirectory(tmp_path, monkeypatch):
    allowed = tmp_path / "archive"
    sub = allowed / "2026" / "q2"
    sub.mkdir(parents=True)
    monkeypatch.setattr(config, "SCAN_PATHS", str(allowed), raising=False)

    # Exact root and a true descendant both pass.
    _validate_scan_path(str(allowed))
    _validate_scan_path(str(sub))


def test_rejects_unrelated_directory(tmp_path, monkeypatch):
    allowed = tmp_path / "archive"
    allowed.mkdir()
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.setattr(config, "SCAN_PATHS", str(allowed), raising=False)

    with pytest.raises(HTTPException) as exc:
        _validate_scan_path(str(other))
    assert exc.value.status_code == 403
