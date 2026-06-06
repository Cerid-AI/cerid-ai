# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for AppleMailDataSource — Phase 4.1.

The ``ceridmail`` Swift helper (an ``.emlx`` walker) isn't built or invoked in
the test env (CI may not be on macOS). All subprocess calls are stubbed; we
verify JSON parsing, configuration, and error handling. CeridMail signals a
Full-Disk-Access TCC denial with exit code **77** (parity with CeridReminders).
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.apple_mail.data_source import AppleMailDataSource


@pytest.fixture
def helper_path(tmp_path):
    p = tmp_path / "ceridmail"
    p.write_text("#!/bin/sh\nexit 0\n")
    p.chmod(0o755)
    return str(p)


def _make_proc_mock(stdout_bytes: bytes, returncode: int = 0, stderr_bytes: bytes = b""):
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout_bytes, stderr_bytes))
    return AsyncMock(return_value=proc)


_SCAN_OK = json.dumps(
    {
        "ok": True,
        "message_count": 2,
        "messages": [
            {"subject": "Q3 budget review", "sender": "cfo@example.com",
             "date": "2026-06-01T09:00:00Z", "mailbox": "Work"},
            {"subject": "Lunch?", "sender": "friend@example.com",
             "date": "2026-06-02T12:00:00Z", "mailbox": "Personal"},
        ],
    }
).encode("utf-8")


class TestConfiguration:
    def test_is_configured_requires_darwin(self, helper_path):
        with patch("platform.system", return_value="Linux"):
            assert AppleMailDataSource(helper_path=helper_path).is_configured() is False

    def test_is_configured_requires_helper_present(self):
        with patch("platform.system", return_value="Darwin"):
            assert AppleMailDataSource(helper_path=None).is_configured() is False

    def test_is_configured_when_both_present(self, helper_path):
        with patch("platform.system", return_value="Darwin"):
            assert AppleMailDataSource(helper_path=helper_path).is_configured() is True

    def test_env_var_override_takes_precedence(self, helper_path, monkeypatch):
        from plugins.apple_mail import data_source as mod
        monkeypatch.setenv("CERID_HELPER_CERIDMAIL", helper_path)
        with patch.object(mod.shutil, "which", side_effect=AssertionError("should not call which")):
            assert mod._resolve_helper_path() == helper_path


class TestQuery:
    @pytest.mark.asyncio
    async def test_scan_parses_helper_json(self, helper_path):
        ds = AppleMailDataSource(helper_path=helper_path)
        with patch("asyncio.create_subprocess_exec", _make_proc_mock(_SCAN_OK)):
            results = await ds.query("any")
        assert len(results) == 2
        assert all(r.source_name == "Apple Mail" for r in results)
        assert any("Q3 budget review" in r.title for r in results)

    @pytest.mark.asyncio
    async def test_query_result_shape(self, helper_path):
        ds = AppleMailDataSource(helper_path=helper_path)
        with patch("asyncio.create_subprocess_exec", _make_proc_mock(_SCAN_OK)):
            r = (await ds.query("any"))[0]
        assert r.title and r.content and r.source_url and r.source_name
        assert isinstance(r.confidence, float)
        # sender + mailbox surfaced in the content
        assert "cfo@example.com" in r.content

    @pytest.mark.asyncio
    async def test_max_results_caps_output(self, helper_path):
        ds = AppleMailDataSource(helper_path=helper_path)
        with patch("asyncio.create_subprocess_exec", _make_proc_mock(_SCAN_OK)):
            results = await ds.query("any", max_results=1)
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_tcc_denial_exit_77_returns_empty(self, helper_path):
        ds = AppleMailDataSource(helper_path=helper_path)
        proc = _make_proc_mock(b"", returncode=77, stderr_bytes=b"full disk access denied")
        with patch("asyncio.create_subprocess_exec", proc):
            assert await ds.query("any") == []

    @pytest.mark.asyncio
    async def test_helper_non_zero_exit_returns_empty(self, helper_path):
        ds = AppleMailDataSource(helper_path=helper_path)
        with patch("asyncio.create_subprocess_exec", _make_proc_mock(b"", returncode=1, stderr_bytes=b"crash")):
            assert await ds.query("any") == []

    @pytest.mark.asyncio
    async def test_scan_not_ok_returns_empty(self, helper_path):
        ds = AppleMailDataSource(helper_path=helper_path)
        bad = json.dumps({"ok": False, "error": "no mail store"}).encode("utf-8")
        with patch("asyncio.create_subprocess_exec", _make_proc_mock(bad)):
            assert await ds.query("any") == []

    @pytest.mark.asyncio
    async def test_no_helper_path_returns_empty(self):
        assert await AppleMailDataSource(helper_path=None).query("any") == []
