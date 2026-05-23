# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for GmailDataSource (Phase F Day 3)."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from plugins.gmail.data_source import (
    GmailDataSource,
    _coerce_message_detail,
    _coerce_message_list,
)


class TestCoerce:
    def test_message_list_from_array(self):
        msgs = _coerce_message_list([{"id": "1"}, {"id": "2"}])
        assert len(msgs) == 2

    def test_message_list_from_dict_with_messages_key(self):
        msgs = _coerce_message_list({"messages": [{"id": "1"}]})
        assert msgs == [{"id": "1"}]

    def test_message_list_empty(self):
        assert _coerce_message_list(None) == []
        assert _coerce_message_list({}) == []
        assert _coerce_message_list("not a list") == []

    def test_message_detail_dict(self):
        assert _coerce_message_detail({"subject": "X"}) == {"subject": "X"}

    def test_message_detail_list(self):
        assert _coerce_message_detail([{"subject": "X"}]) == {"subject": "X"}

    def test_message_detail_invalid(self):
        assert _coerce_message_detail(None) is None
        assert _coerce_message_detail("x") is None


class TestGmailDataSource:
    def test_is_configured_requires_both(self, monkeypatch):
        ds = GmailDataSource()
        monkeypatch.delenv("CERID_CONNECTORS_BEARER", raising=False)
        monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
        assert ds.is_configured() is False
        monkeypatch.setenv("CERID_CONNECTORS_BEARER", "tok")
        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "id")
        assert ds.is_configured() is True

    @pytest.mark.asyncio
    async def test_query_search_then_hydrate(self, monkeypatch):
        ds = GmailDataSource()
        monkeypatch.setenv("GMAIL_MAX_FULL_FETCH", "2")

        async def _stub(tool, args):
            if tool == "search_gmail_messages":
                return [
                    {"id": "m1", "snippet": "snip1"},
                    {"id": "m2", "snippet": "snip2"},
                    {"id": "m3", "snippet": "snip3"},
                ]
            if tool == "get_gmail_message_content":
                mid = args["message_id"]
                return {
                    "subject": f"Subject {mid}",
                    "from": f"sender-{mid}@example.com",
                    "body": f"Body of {mid}",
                }
            return None

        with patch.object(ds, "_call_mcp", AsyncMock(side_effect=_stub)):
            results = await ds.query("hello")

        # Top 2 hydrated, m3 stays as snippet
        assert len(results) == 3
        assert results[0].title == "Subject m1"
        assert "Body of m1" in results[0].content
        assert results[0].confidence == 0.75
        # m3 is snippet-only with lower confidence
        assert results[2].confidence == 0.55

    @pytest.mark.asyncio
    async def test_query_returns_empty_on_search_failure(self):
        ds = GmailDataSource()
        with patch.object(ds, "_call_mcp", AsyncMock(side_effect=RuntimeError("breaker"))):
            results = await ds.query("hello")
        assert results == []

    @pytest.mark.asyncio
    async def test_query_skips_failed_content_fetches(self, monkeypatch):
        ds = GmailDataSource()
        monkeypatch.setenv("GMAIL_MAX_FULL_FETCH", "2")
        call_count = {"n": 0}

        async def _stub(tool, args):
            if tool == "search_gmail_messages":
                return [{"id": "m1", "snippet": "s"}, {"id": "m2", "snippet": "s"}]
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("content fetch failed")
            return {"subject": "ok", "body": "body"}

        with patch.object(ds, "_call_mcp", AsyncMock(side_effect=_stub)):
            results = await ds.query("hello")

        # First content fetch failed → skipped; second succeeded
        assert len(results) == 1
        assert results[0].title == "ok"
