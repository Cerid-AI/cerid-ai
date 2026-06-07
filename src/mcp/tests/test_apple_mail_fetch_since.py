# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Deterministic tests for the Apple Mail connector's ``since``-payload parser
and the ``fetch_since`` ingest loop.

The ``ceridmail since <iso>`` helper emits JSON; ``_parse_messages`` normalizes
it and ``fetch_since`` ingests each message via the DI sink, advancing the cursor
per artifact (crash-safe at-least-once, mirroring the RSS connector).
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from core.ingest.sources.connectors import apple_mail
from core.ingest.sources.connectors.apple_mail import AppleMailConnector, _parse_messages

_OK = json.dumps(
    {
        "ok": True,
        "cursor": "",
        "messages": [
            {
                "id": "msg-1",
                "date": "2026-01-01T10:00:00Z",
                "subject": "First",
                "from": "a@example.com",
                "to": "me@example.com",
                "body": "hello one",
            },
            {
                "id": "msg-2",
                "date": "2026-01-02T10:00:00Z",
                "subject": "Second",
                "from": "b@example.com",
                "to": "me@example.com",
                "body": "hello two",
            },
        ],
    }
)


def test_parse_messages_normalizes_records() -> None:
    msgs = _parse_messages(_OK)
    assert [m["id"] for m in msgs] == ["msg-1", "msg-2"]
    assert msgs[0]["subject"] == "First"
    assert msgs[0]["body"] == "hello one"
    assert msgs[1]["from"] == "b@example.com"


def test_parse_messages_empty_list() -> None:
    assert _parse_messages(json.dumps({"ok": True, "messages": []})) == []


def test_parse_messages_missing_fields_default_to_empty() -> None:
    msgs = _parse_messages(json.dumps({"ok": True, "messages": [{"id": "x"}]}))
    assert msgs[0]["id"] == "x"
    assert msgs[0]["subject"] == ""
    assert msgs[0]["body"] == ""


def test_parse_messages_not_ok_raises() -> None:
    with pytest.raises(ValueError):
        _parse_messages(json.dumps({"ok": False, "error": "TCC denied"}))


def test_parse_messages_bad_json_raises() -> None:
    with pytest.raises(ValueError):
        _parse_messages("not json {")


def test_parse_messages_non_list_messages_raises() -> None:
    with pytest.raises(ValueError):
        _parse_messages(json.dumps({"ok": True, "messages": "nope"}))


# ---- fetch_since integration (mocked subprocess + fake ingest sink) ----


class _FakeProc:
    def __init__(self, stdout: bytes, returncode: int = 0) -> None:
        self._stdout = stdout
        self.returncode = returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, b""

    def kill(self) -> None:  # pragma: no cover - only on timeout path
        pass


def _wire(monkeypatch: pytest.MonkeyPatch, stdout: bytes, returncode: int = 0) -> list[dict]:
    """Wire a fake helper binary + fake ingest sink. Returns the ingested list."""
    ingested: list[dict] = []

    async def _fake_ingest(content: str, *, domain: str, metadata: dict[str, Any]) -> str:
        ingested.append({"content": content, "domain": domain, "metadata": metadata})
        return f"art-{len(ingested)}"

    monkeypatch.setattr(apple_mail, "_helper_path", lambda: "/usr/local/bin/ceridmail")

    async def _fake_exec(*args: Any, **kwargs: Any) -> _FakeProc:
        return _FakeProc(stdout, returncode)

    monkeypatch.setattr(apple_mail.asyncio, "create_subprocess_exec", _fake_exec)

    from core.ingest.sources import ingest_sink

    monkeypatch.setattr(ingest_sink, "_ingest_fn", _fake_ingest, raising=False)
    return ingested


@pytest.mark.asyncio
async def test_fetch_since_ingests_and_advances_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    ingested = _wire(monkeypatch, _OK.encode())
    events = [
        ev
        async for ev in AppleMailConnector().fetch_since(
            "src-1", {"last_message_iso": None}, {"domain": "mail"}
        )
    ]
    assert len(events) == 2
    assert len(ingested) == 2
    # content carries subject + body; metadata tags source + id
    assert "hello one" in ingested[0]["content"]
    assert ingested[0]["metadata"]["source_type"] == "apple_mail"
    assert ingested[0]["metadata"]["source_id"] == "src-1"
    # cursor advances per message to the message date (monotonic, oldest-first)
    assert events[0].cursor_after["last_message_iso"] == "2026-01-01T10:00:00Z"
    assert events[1].cursor_after["last_message_iso"] == "2026-01-02T10:00:00Z"


@pytest.mark.asyncio
async def test_fetch_since_no_sink_is_safe_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(apple_mail, "_helper_path", lambda: "/usr/local/bin/ceridmail")
    from core.ingest.sources import ingest_sink

    monkeypatch.setattr(ingest_sink, "_ingest_fn", None, raising=False)
    events = [
        ev async for ev in AppleMailConnector().fetch_since("s", {}, {})
    ]
    assert events == []


@pytest.mark.asyncio
async def test_fetch_since_no_helper_is_safe_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(apple_mail, "_helper_path", lambda: None)
    events = [ev async for ev in AppleMailConnector().fetch_since("s", {}, {})]
    assert events == []


@pytest.mark.asyncio
async def test_fetch_since_helper_failure_is_safe_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    _wire(monkeypatch, b"boom", returncode=77)  # TCC-denied exit
    events = [ev async for ev in AppleMailConnector().fetch_since("s", {}, {})]
    assert events == []
