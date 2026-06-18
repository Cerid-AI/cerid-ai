# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Deterministic tests for the Apple Reminders connector's ``since``-payload
parser and the ``fetch_since`` ingest loop (mirrors the Apple Mail tests)."""
from __future__ import annotations

import json
from typing import Any

import pytest

from core.ingest.sources.connectors import apple_reminders
from core.ingest.sources.connectors.apple_reminders import (
    AppleRemindersConnector,
    _parse_reminders,
)

_OK = json.dumps(
    {
        "ok": True,
        "cursor": "",
        "reminders": [
            {
                "id": "r-1",
                "title": "Buy milk",
                "notes": "2%",
                "due": "2026-01-05T09:00:00Z",
                "completed": False,
                "priority": 1,
                "list": "Errands",
                "modified": "2026-01-01T08:00:00Z",
            },
            {
                "id": "r-2",
                "title": "Call dentist",
                "notes": "",
                "due": None,
                "completed": False,
                "priority": 0,
                "list": "Personal",
                "modified": "2026-01-02T08:00:00Z",
            },
        ],
    }
)


def test_parse_reminders_normalizes_records() -> None:
    rem = _parse_reminders(_OK)
    assert [r["id"] for r in rem] == ["r-1", "r-2"]
    assert rem[0]["title"] == "Buy milk"
    assert rem[0]["list"] == "Errands"
    assert rem[1]["due"] == ""  # null → empty string


def test_parse_reminders_empty_list() -> None:
    assert _parse_reminders(json.dumps({"ok": True, "reminders": []})) == []


def test_parse_reminders_not_ok_raises() -> None:
    with pytest.raises(ValueError):
        _parse_reminders(json.dumps({"ok": False, "error": "TCC denied"}))


def test_parse_reminders_bad_json_raises() -> None:
    with pytest.raises(ValueError):
        _parse_reminders("}{ not json")


def test_parse_reminders_non_list_raises() -> None:
    with pytest.raises(ValueError):
        _parse_reminders(json.dumps({"ok": True, "reminders": 5}))


# ---- fetch_since integration (mocked subprocess + fake ingest sink) ----


class _FakeProc:
    def __init__(self, stdout: bytes, returncode: int = 0) -> None:
        self._stdout = stdout
        self.returncode = returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, b""

    def kill(self) -> None:  # pragma: no cover
        pass


def _wire(monkeypatch: pytest.MonkeyPatch, stdout: bytes, returncode: int = 0) -> list[dict]:
    ingested: list[dict] = []

    async def _fake_ingest(content: str, *, domain: str, metadata: dict[str, Any]) -> str:
        ingested.append({"content": content, "domain": domain, "metadata": metadata})
        return f"art-{len(ingested)}"

    monkeypatch.setattr(apple_reminders, "_helper_path", lambda: "/usr/local/bin/ceridreminders")

    async def _fake_exec(*args: Any, **kwargs: Any) -> _FakeProc:
        return _FakeProc(stdout, returncode)

    monkeypatch.setattr(apple_reminders.asyncio, "create_subprocess_exec", _fake_exec)

    from core.ingest.sources import ingest_sink

    monkeypatch.setattr(ingest_sink, "_ingest_fn", _fake_ingest, raising=False)
    return ingested


@pytest.mark.asyncio
async def test_fetch_since_ingests_and_advances_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    ingested = _wire(monkeypatch, _OK.encode())
    events = [
        ev
        async for ev in AppleRemindersConnector().fetch_since(
            "src-1", {"last_modified_iso": None}, {"domain": "tasks"}
        )
    ]
    assert len(events) == 2
    assert len(ingested) == 2
    assert "Buy milk" in ingested[0]["content"]
    assert ingested[0]["metadata"]["source_type"] == "apple_reminders"
    assert events[0].cursor_after["last_modified_iso"] == "2026-01-01T08:00:00Z"
    assert events[1].cursor_after["last_modified_iso"] == "2026-01-02T08:00:00Z"


@pytest.mark.asyncio
async def test_fetch_since_no_sink_is_safe_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(apple_reminders, "_helper_path", lambda: "/usr/local/bin/ceridreminders")
    from core.ingest.sources import ingest_sink

    monkeypatch.setattr(ingest_sink, "_ingest_fn", None, raising=False)
    events = [ev async for ev in AppleRemindersConnector().fetch_since("s", {}, {})]
    assert events == []


@pytest.mark.asyncio
async def test_fetch_since_no_helper_is_safe_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(apple_reminders, "_helper_path", lambda: None)
    events = [ev async for ev in AppleRemindersConnector().fetch_since("s", {}, {})]
    assert events == []


@pytest.mark.asyncio
async def test_fetch_since_helper_failure_is_safe_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    _wire(monkeypatch, b"denied", returncode=77)
    events = [ev async for ev in AppleRemindersConnector().fetch_since("s", {}, {})]
    assert events == []
