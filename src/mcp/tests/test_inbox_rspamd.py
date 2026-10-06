# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""The rspamd client: which hosts it will talk to, and what a failed scan does.

The scanner is the ``rspamd`` service on the stack network. Inside the API
container a loopback URL is the container's own loopback (refused), so the
client accepts the service name as well. The HTTP cases run against a real
local server; the once-per-run log runs the real triage pass with an
injected scan.
"""
from __future__ import annotations

import json
import logging
import socket
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import AsyncMock, patch

import pytest

import core.agents.inbox_rspamd as rspamd
from core.agents.inbox_rspamd import RspamdScanError, _endpoint, rspamd_url, scan_if_configured
from core.agents.inbox_triage import _categorize_thread, set_inbox_rspamd, triage_inboxes

pytestmark = pytest.mark.asyncio


class TestRspamdUrl:
    def test_the_stack_service_name_is_accepted(self, monkeypatch):
        monkeypatch.setenv("CERID_RSPAMD_URL", "http://rspamd:11333")
        assert rspamd_url() == "http://rspamd:11333"
        assert _endpoint("http://rspamd:11333") == ("rspamd", 11333, "/checkv2")

    def test_loopback_is_still_accepted(self, monkeypatch):
        monkeypatch.setenv("CERID_RSPAMD_URL", "http://127.0.0.1:11333")
        assert rspamd_url() == "http://127.0.0.1:11333"

    @pytest.mark.parametrize("url", [
        "http://rspamd.example.com:11333",
        "http://host.docker.internal:11333",
        "http://cerid-rspamd:11333",
        "https://rspamd:11333",
    ])
    def test_any_other_host_is_refused(self, monkeypatch, url):
        monkeypatch.setenv("CERID_RSPAMD_URL", url)
        assert rspamd_url() == ""


# ── a local server ───────────────────────────────────────────────────

_REPLY = {"action": "reject", "score": 20.0, "required_score": 15.0, "symbols": {}}


class _Handler(BaseHTTPRequestHandler):
    mode = "ok"

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        if self.mode == "slow":
            time.sleep(1.0)
        status = 500 if self.mode == "error" else 200
        body = json.dumps(_REPLY).encode() if status == 200 else b"boom"
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return None


@pytest.fixture
def server() -> Iterator[HTTPServer]:
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class TestScan:
    async def test_a_200_reply_is_the_parsed_payload(self, monkeypatch, server):
        _Handler.mode = "ok"
        monkeypatch.setenv("CERID_RSPAMD_URL", f"http://127.0.0.1:{server.server_port}")
        assert await scan_if_configured(b"From: a@b\r\n\r\nhi") == _REPLY

    async def test_a_non_200_reply_raises_with_the_status(self, monkeypatch, server):
        _Handler.mode = "error"
        monkeypatch.setenv("CERID_RSPAMD_URL", f"http://127.0.0.1:{server.server_port}")
        with pytest.raises(RspamdScanError, match="500"):
            await scan_if_configured(b"From: a@b\r\n\r\nhi")

    async def test_a_refused_connection_raises(self, monkeypatch):
        monkeypatch.setenv("CERID_RSPAMD_URL", f"http://127.0.0.1:{_free_port()}")
        with pytest.raises(OSError):
            await scan_if_configured(b"From: a@b\r\n\r\nhi")

    async def test_a_timeout_raises(self, monkeypatch, server):
        _Handler.mode = "slow"
        monkeypatch.setattr(rspamd, "SCAN_TIMEOUT_S", 0.2)
        monkeypatch.setenv("CERID_RSPAMD_URL", f"http://127.0.0.1:{server.server_port}")
        with pytest.raises(OSError):
            await scan_if_configured(b"From: a@b\r\n\r\nhi")


# ── once per run ─────────────────────────────────────────────────────

class _Msg:
    def __init__(self, title: str, thread_id: str) -> None:
        self.title = title
        self.content = "please look at this"
        self.source_name = "gmail"
        self.confidence = 0.5
        self.metadata = {
            "provider_thread_id": thread_id,
            "provider_message_id": f"{thread_id}-m",
            "from": f"{thread_id}@example.com",
        }


class _Source:
    name = "gmail"

    def __init__(self, messages: list[_Msg]) -> None:
        self._messages = messages

    def is_configured(self) -> bool:
        return True

    async def query(self, _query: str, max_results: int = 50):
        return self._messages[:max_results]


def _swallowed(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == "ai-companion.swallowed" and "inbox_triage.rspamd" in r.getMessage()]


class TestFailureLog:
    @pytest.fixture(autouse=True)
    def _wire_inbox_di(self):
        import core.agents.inbox_triage as triage
        from app.agents_di import wire_inbox_triage_di

        wire_inbox_triage_di()
        # The wired account lookup reads the real ledger, which has no accounts
        # here; unwired triage does not gate on accounts.
        triage.set_inbox_accounts(None)
        yield
        triage._registry = None
        triage._rag_route = None

    async def test_a_run_logs_failed_scans_once_with_the_count(self, caplog):
        async def scan(_raw: bytes) -> dict[str, object]:
            raise ConnectionRefusedError(111, "Connection refused")

        source = _Source([_Msg("One", "t1"), _Msg("Two", "t2"), _Msg("Three", "t3")])
        set_inbox_rspamd(scan)
        caplog.set_level(logging.WARNING, logger="ai-companion.swallowed")
        try:
            with (
                patch("config.features.is_feature_enabled", return_value=True),
                patch("app.data_sources.base.registry.get", side_effect=lambda name: source if name == "gmail" else None),
                patch(
                    "core.utils.internal_llm.call_internal_llm",
                    new_callable=AsyncMock,
                    return_value='{"category":"personal","summary":"s","action":"keep","utility":"none","confidence":0.95}',
                ),
            ):
                result = await triage_inboxes(persist=False)
        finally:
            set_inbox_rspamd(None)

        assert len(result.threads) == 3
        records = _swallowed(caplog)
        assert len(records) == 1
        assert records[0].failed_scans == 3
        assert "ConnectionRefusedError" in records[0].getMessage()

    async def test_a_run_with_working_scans_logs_nothing(self, caplog):
        async def scan(_raw: bytes) -> dict[str, object]:
            return {"action": "no action", "score": 0.1, "required_score": 15.0}

        source = _Source([_Msg("One", "t1")])
        set_inbox_rspamd(scan)
        caplog.set_level(logging.WARNING, logger="ai-companion.swallowed")
        try:
            with (
                patch("config.features.is_feature_enabled", return_value=True),
                patch("app.data_sources.base.registry.get", side_effect=lambda name: source if name == "gmail" else None),
                patch(
                    "core.utils.internal_llm.call_internal_llm",
                    new_callable=AsyncMock,
                    return_value='{"category":"personal","summary":"s","action":"keep","utility":"none","confidence":0.95}',
                ),
            ):
                await triage_inboxes(persist=False)
        finally:
            set_inbox_rspamd(None)
        assert _swallowed(caplog) == []

    async def test_outside_a_run_each_failure_is_logged(self, caplog):
        async def scan(_raw: bytes) -> dict[str, object]:
            raise RspamdScanError("rspamd replied HTTP 500")

        set_inbox_rspamd(scan)
        caplog.set_level(logging.WARNING, logger="ai-companion.swallowed")
        try:
            with patch(
                "core.utils.internal_llm.call_internal_llm",
                new_callable=AsyncMock,
                return_value='{"category":"personal","summary":"s","action":"keep","utility":"none","confidence":0.95}',
            ):
                await _categorize_thread("t1", [_Msg("One", "t1")])
        finally:
            set_inbox_rspamd(None)
        assert len(_swallowed(caplog)) == 1
