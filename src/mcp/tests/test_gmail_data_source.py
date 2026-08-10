# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for GmailDataSource (Phase F Day 3).

**Rewritten 2026-08-09.** The previous version stubbed ``_call_mcp`` with
fabricated structured data — ``[{"id": "m1", "snippet": "snip1"}]`` — a shape
the google-workspace-mcp server has never emitted. It returns prose. So these
tests passed for the life of the connector while every real query returned
zero results, and they are the reason the defect survived: the fake encoded an
API that did not exist, and the suite then defended it.

Every stub below is the server's real output. Reply-format parsing is covered
separately in ``test_gmail_datasource_parsing.py``; this file covers the
query orchestration on top of it.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from plugins.gmail.data_source import GmailDataSource


def _search_reply(*ids: str) -> str:
    """The real ``search_gmail_messages`` reply shape."""
    blocks = "\n\n".join(
        f"  {i}. Message ID: {mid}\n"
        f"     Web Link: https://mail.google.com/mail/u/0/#all/{mid}\n"
        f"     Thread ID: {mid}\n"
        f"     Thread Link: https://mail.google.com/mail/u/0/#all/{mid}"
        for i, mid in enumerate(ids, start=1)
    )
    return f"Found {len(ids)} messages matching 'hello':\n\n📧 MESSAGES:\n{blocks}"


def _detail_reply(mid: str) -> str:
    """The real ``get_gmail_message_content`` reply shape."""
    return (
        f"Subject: Subject {mid}\n"
        f"From: sender-{mid}@example.com\n"
        f"Date: Mon, 10 Aug 2026 01:02:35 GMT\n"
        f"To: me@example.com\n"
        f"\n--- BODY ---\n"
        f"Body of {mid}"
    )


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
                return _search_reply("m1", "m2", "m3")
            if tool == "get_gmail_message_content":
                return _detail_reply(args["message_id"])
            return None

        with patch.object(ds, "_call_mcp", AsyncMock(side_effect=_stub)):
            results = await ds.query("hello")

        # Top 2 hydrated, m3 cited by id only
        assert len(results) == 3
        assert results[0].title == "Subject m1"
        assert "Body of m1" in results[0].content
        assert "sender-m1@example.com" in results[0].content
        assert results[0].confidence == 0.75
        assert results[2].confidence == 0.55
        assert "m3" in results[2].title

    @pytest.mark.asyncio
    async def test_query_sends_page_size_not_max_results(self, monkeypatch):
        """The server rejects unknown keywords via pydantic and returns the
        error as a RESULT, so a wrong name is silent. Pin the name."""
        ds = GmailDataSource()
        seen: dict[str, dict] = {}

        async def _stub(tool, args):
            seen[tool] = args
            return _search_reply("m1") if tool == "search_gmail_messages" else _detail_reply("m1")

        with patch.object(ds, "_call_mcp", AsyncMock(side_effect=_stub)):
            await ds.query("hello", max_results=7)

        assert seen["search_gmail_messages"]["page_size"] == 7
        assert "max_results" not in seen["search_gmail_messages"]

    @pytest.mark.asyncio
    async def test_query_returns_empty_on_search_failure(self):
        ds = GmailDataSource()
        with patch.object(ds, "_call_mcp", AsyncMock(side_effect=RuntimeError("breaker"))):
            results = await ds.query("hello")
        assert results == []

    @pytest.mark.asyncio
    async def test_a_validation_error_returned_as_a_result_yields_nothing(self):
        """How #12 hid: an argument error arrives as a normal tool result whose
        content is the error text, so `except` never fires. It must produce no
        results rather than a bogus one."""
        ds = GmailDataSource()
        err = (
            "1 validation error for call[search_gmail_messages]\n"
            "max_results\n  Unexpected keyword argument"
        )
        with patch.object(ds, "_call_mcp", AsyncMock(return_value=err)):
            assert await ds.query("hello") == []

    @pytest.mark.asyncio
    async def test_query_skips_failed_content_fetches(self, monkeypatch):
        ds = GmailDataSource()
        monkeypatch.setenv("GMAIL_MAX_FULL_FETCH", "2")
        call_count = {"n": 0}

        async def _stub(tool, args):
            if tool == "search_gmail_messages":
                return _search_reply("m1", "m2")
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("content fetch failed")
            return _detail_reply("m2")

        with patch.object(ds, "_call_mcp", AsyncMock(side_effect=_stub)):
            results = await ds.query("hello")

        # First content fetch failed → skipped; second succeeded
        assert len(results) == 1
        assert results[0].title == "Subject m2"
