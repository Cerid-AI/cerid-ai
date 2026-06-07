# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for UrlWatchConnector.fetch_since — content-hash change detection."""
from __future__ import annotations

from typing import Any

import pytest

from core.ingest.sources import ingest_sink
from core.ingest.sources.connectors import url_watch
from core.ingest.sources.connectors.url_watch import UrlWatchConnector, _content_hash


class _Resp:
    def __init__(self, text: str) -> None:
        self.text = text
        self.status_code = 200

    def raise_for_status(self) -> None:
        return None


@pytest.fixture
def _sink():
    calls: list[dict[str, Any]] = []

    async def _fn(content, *, domain, metadata):  # noqa: ANN001
        calls.append({"content": content, "domain": domain, "metadata": metadata})
        return "artifact-1"

    ingest_sink.set_source_ingest_fn(_fn)
    yield calls
    ingest_sink._ingest_fn = None  # reset


async def _collect(gen):
    return [e async for e in gen]


@pytest.mark.asyncio
async def test_changed_content_yields_event_and_ingests(monkeypatch, _sink):
    async def _fake_get(url, **kw):  # noqa: ANN001
        return _Resp("brand new content")

    monkeypatch.setattr(url_watch, "guarded_get", _fake_get)
    conn = UrlWatchConnector()
    events = await _collect(
        conn.fetch_since("s1", {"last_hash": "stale"}, {"url": "http://x/y", "domain": "docs"})
    )
    assert len(events) == 1
    assert events[0].cursor_after["last_hash"] == _content_hash("brand new content")
    assert len(_sink) == 1
    assert _sink[0]["metadata"]["source_type"] == "url_watch"
    assert _sink[0]["domain"] == "docs"


@pytest.mark.asyncio
async def test_unchanged_content_yields_nothing(monkeypatch, _sink):
    body = "unchanged page"

    async def _fake_get(url, **kw):  # noqa: ANN001
        return _Resp(body)

    monkeypatch.setattr(url_watch, "guarded_get", _fake_get)
    conn = UrlWatchConnector()
    events = await _collect(
        conn.fetch_since("s1", {"last_hash": _content_hash(body)}, {"url": "http://x/y"})
    )
    assert events == []
    assert _sink == []


@pytest.mark.asyncio
async def test_no_sink_is_safe_noop(monkeypatch):
    ingest_sink._ingest_fn = None

    async def _fake_get(url, **kw):  # noqa: ANN001
        return _Resp("content")

    monkeypatch.setattr(url_watch, "guarded_get", _fake_get)
    conn = UrlWatchConnector()
    events = await _collect(conn.fetch_since("s1", {}, {"url": "http://x/y"}))
    assert events == []
