# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for ApplePhotosDataSource — Phase G.4."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.apple_photos import data_source as apple_photos_ds
from plugins.apple_photos.data_source import ApplePhotosDataSource


@pytest.fixture
def helper_path(tmp_path):
    p = tmp_path / "ceridphotos"
    p.write_text("#!/bin/sh\nexit 0\n")
    p.chmod(0o755)
    return str(p)


def _make_proc_mock(stdout_bytes: bytes, returncode: int = 0):
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout_bytes, b""))
    return AsyncMock(return_value=proc)


@pytest.mark.asyncio
async def test_list_assets_parses_helper_json(helper_path):
    ds = ApplePhotosDataSource(helper_path=helper_path)
    payload = [
        {
            "id": "photo-1",
            "media_type": "image",
            "creation_date": "2026-05-21T12:00:00Z",
            "location_lat": 40.7,
            "location_lon": -74.0,
            "pixel_width": 4032,
            "pixel_height": 3024,
            "is_favorite": True,
            "media_subtypes": ["live"],
        },
    ]
    proc_factory = _make_proc_mock(json.dumps(payload).encode("utf-8"))
    with patch("asyncio.create_subprocess_exec", proc_factory):
        assets = await ds.list_assets(limit=10)
    assert len(assets) == 1
    assert assets[0]["id"] == "photo-1"
    assert assets[0]["media_subtypes"] == ["live"]


@pytest.mark.asyncio
async def test_tcc_denial_returns_empty(helper_path):
    ds = ApplePhotosDataSource(helper_path=helper_path)
    proc_factory = _make_proc_mock(b"", returncode=3)
    with patch("asyncio.create_subprocess_exec", proc_factory):
        assets = await ds.list_assets()
    assert assets == []


@pytest.mark.asyncio
async def test_query_renders_results(helper_path):
    ds = ApplePhotosDataSource(helper_path=helper_path)
    payload = [
        {
            "id": "photo-1",
            "media_type": "image",
            "creation_date": "2026-05-21T12:00:00Z",
            "pixel_width": 4032,
            "pixel_height": 3024,
            "is_favorite": False,
            "media_subtypes": [],
        },
    ]
    proc_factory = _make_proc_mock(json.dumps(payload).encode("utf-8"))
    with patch("asyncio.create_subprocess_exec", proc_factory):
        results = await ds.query("show recent photos")
    assert len(results) == 1
    assert results[0].source_name == "Apple Photos"
    assert "image" in results[0].content.lower()


def test_is_configured_requires_darwin_and_helper(helper_path, unresolvable_swift_helper):
    # helper_path=None resolves through _resolve_helper_path(), whose last
    # fallback is the developer's swift/build/ — see the fixture's docstring.
    unresolvable_swift_helper(apple_photos_ds)
    with patch("platform.system", return_value="Linux"):
        assert ApplePhotosDataSource(helper_path=helper_path).is_configured() is False
    with patch("platform.system", return_value="Darwin"):
        assert ApplePhotosDataSource(helper_path=helper_path).is_configured() is True
        assert ApplePhotosDataSource(helper_path=None).is_configured() is False
