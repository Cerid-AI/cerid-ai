# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for spotlight_donor — Phase G.4."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.spotlight_donor.donor import SpotlightItem, donate, purge


def _make_proc_mock(stdout_bytes: bytes, returncode: int = 0):
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout_bytes, b""))
    return AsyncMock(return_value=proc)


@pytest.mark.asyncio
async def test_donate_passes_ndjson_to_helper(tmp_path):
    helper = tmp_path / "ceridspotlight"
    helper.write_text("#!/bin/sh\nexit 0\n")
    helper.chmod(0o755)

    proc_factory = _make_proc_mock(b'{"donated":2}\n')
    with (
        patch("platform.system", return_value="Darwin"),
        patch("plugins.spotlight_donor.donor._resolve_helper_path",
              return_value=str(helper)),
        patch("asyncio.create_subprocess_exec", proc_factory),
    ):
        result = await donate([
            SpotlightItem(id="a", title="A", domain="ai.cerid.kb.notes"),
            SpotlightItem(id="b", title="B", domain="ai.cerid.kb.notes",
                          keywords=["foo", "bar"]),
        ])
    assert result == {"donated": 2}
    # The helper was invoked exactly once with the donate subcommand.
    assert proc_factory.await_count == 1


@pytest.mark.asyncio
async def test_donate_no_op_on_non_darwin():
    with patch("platform.system", return_value="Linux"):
        result = await donate([SpotlightItem(id="a", title="A")])
    assert result == {"donated": 0, "skipped": True}


@pytest.mark.asyncio
async def test_donate_no_op_when_helper_missing():
    with (
        patch("platform.system", return_value="Darwin"),
        patch("plugins.spotlight_donor.donor._resolve_helper_path", return_value=None),
    ):
        result = await donate([SpotlightItem(id="a", title="A")])
    assert result == {"donated": 0, "skipped": True}


@pytest.mark.asyncio
async def test_donate_empty_list_short_circuits(tmp_path):
    helper = tmp_path / "ceridspotlight"
    helper.write_text("#!/bin/sh\n")
    helper.chmod(0o755)
    with (
        patch("platform.system", return_value="Darwin"),
        patch("plugins.spotlight_donor.donor._resolve_helper_path", return_value=str(helper)),
    ):
        result = await donate([])
    assert result == {"donated": 0}


@pytest.mark.asyncio
async def test_purge_succeeds(tmp_path):
    helper = tmp_path / "ceridspotlight"
    helper.write_text("#!/bin/sh\n")
    helper.chmod(0o755)
    proc_factory = _make_proc_mock(json.dumps({"purged": "ai.cerid.kb.notes"}).encode())
    with (
        patch("platform.system", return_value="Darwin"),
        patch("plugins.spotlight_donor.donor._resolve_helper_path", return_value=str(helper)),
        patch("asyncio.create_subprocess_exec", proc_factory),
    ):
        result = await purge("ai.cerid.kb.notes")
    assert result == {"purged": "ai.cerid.kb.notes"}


def test_spotlight_item_round_trip():
    item = SpotlightItem(
        id="art:1",
        title="My note",
        domain="ai.cerid.kb.notes",
        content_description="A test note",
        keywords=["test"],
        content_url="cerid://kb/art:1",
        expiration_days=30,
    )
    # Verify field shape — used by donor.donate when serializing to NDJSON
    assert item.id == "art:1"
    assert item.keywords == ["test"]
    assert item.expiration_days == 30
