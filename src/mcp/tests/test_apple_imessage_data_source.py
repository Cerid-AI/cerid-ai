# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for AppleIMessageDataSource — Phase 4.2.

The ``ceridimessage`` Swift helper (a ``chat.db`` reader) isn't built or invoked
in the test env (CI may not be on macOS). All subprocess calls are stubbed; we
verify JSON parsing, configuration, error handling, and the **sensitive-domain
retrieval opt-in** gate (Task 1.2e — decoupled from private_mode level).
CeridIMessage signals a Full-Disk-Access TCC denial with exit code **77**.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.apple_imessage import data_source as apple_imessage_ds
from plugins.apple_imessage.data_source import AppleIMessageDataSource


@pytest.fixture(autouse=True)
def _sensitive_domain_retrieval_opted_in():
    """Default the instance to opted-in so the happy-path tests exercise
    parsing. The gate test overrides this."""
    with patch(
        "utils.domain_privacy.sensitive_domains_opted_in", return_value=True
    ):
        yield


@pytest.fixture
def helper_path(tmp_path):
    p = tmp_path / "ceridimessage"
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
            {"text": "running late, 10 min", "sender": "+15551234567",
             "date": "2026-06-01T18:00:00Z", "chat": "Alex"},
            {"text": "dinner thursday?", "sender": "friend@icloud.com",
             "date": "2026-06-02T11:00:00Z", "chat": "Sam"},
        ],
    }
).encode("utf-8")


class TestConfiguration:
    def test_is_configured_requires_darwin(self, helper_path):
        with patch("platform.system", return_value="Linux"):
            assert AppleIMessageDataSource(helper_path=helper_path).is_configured() is False

    def test_is_configured_requires_helper_present(self, unresolvable_swift_helper):
        # helper_path=None resolves through _resolve_helper_path(), whose last
        # fallback is the developer's swift/build/ — see the fixture's docstring.
        unresolvable_swift_helper(apple_imessage_ds)
        with patch("platform.system", return_value="Darwin"):
            assert AppleIMessageDataSource(helper_path=None).is_configured() is False

    def test_is_configured_when_both_present(self, helper_path):
        with patch("platform.system", return_value="Darwin"):
            assert AppleIMessageDataSource(helper_path=helper_path).is_configured() is True

    def test_env_var_override_takes_precedence(self, helper_path, monkeypatch):
        from plugins.apple_imessage import data_source as mod
        monkeypatch.setenv("CERID_HELPER_CERIDIMESSAGE", helper_path)
        with patch.object(mod.shutil, "which", side_effect=AssertionError("should not call which")):
            assert mod._resolve_helper_path() == helper_path


class TestQuery:
    @pytest.mark.asyncio
    async def test_scan_parses_helper_json(self, helper_path):
        ds = AppleIMessageDataSource(helper_path=helper_path)
        with patch("asyncio.create_subprocess_exec", _make_proc_mock(_SCAN_OK)):
            results = await ds.query("any")
        assert len(results) == 2
        assert all(r.source_name == "iMessage" for r in results)
        assert any("running late" in r.content for r in results)

    @pytest.mark.asyncio
    async def test_query_result_shape(self, helper_path):
        ds = AppleIMessageDataSource(helper_path=helper_path)
        with patch("asyncio.create_subprocess_exec", _make_proc_mock(_SCAN_OK)):
            r = (await ds.query("any"))[0]
        assert r.title and r.content and r.source_url and r.source_name
        assert isinstance(r.confidence, float)

    @pytest.mark.asyncio
    async def test_tcc_denial_exit_77_returns_empty(self, helper_path):
        ds = AppleIMessageDataSource(helper_path=helper_path)
        proc = _make_proc_mock(b"", returncode=77, stderr_bytes=b"full disk access denied")
        with patch("asyncio.create_subprocess_exec", proc):
            assert await ds.query("any") == []

    @pytest.mark.asyncio
    async def test_scan_not_ok_returns_empty(self, helper_path):
        ds = AppleIMessageDataSource(helper_path=helper_path)
        bad = json.dumps({"ok": False, "error": "no chat.db"}).encode("utf-8")
        with patch("asyncio.create_subprocess_exec", _make_proc_mock(bad)):
            assert await ds.query("any") == []

    @pytest.mark.asyncio
    async def test_no_helper_path_returns_empty(self, unresolvable_swift_helper):
        unresolvable_swift_helper(apple_imessage_ds)
        assert await AppleIMessageDataSource(helper_path=None).query("any") == []


class TestSensitiveDomainRetrievalGate:
    @pytest.mark.asyncio
    async def test_blocked_when_opted_out(self, helper_path):
        """When the opt-in is off, the connector returns nothing AND never
        spawns the helper (no chat.db access at all)."""
        ds = AppleIMessageDataSource(helper_path=helper_path)
        spawn = _make_proc_mock(_SCAN_OK)
        with patch("utils.domain_privacy.sensitive_domains_opted_in", return_value=False), \
             patch("asyncio.create_subprocess_exec", spawn):
            assert await ds.query("any") == []
        spawn.assert_not_called()

    @pytest.mark.asyncio
    async def test_allowed_when_opted_in(self, helper_path):
        ds = AppleIMessageDataSource(helper_path=helper_path)
        with patch("utils.domain_privacy.sensitive_domains_opted_in", return_value=True), \
             patch("asyncio.create_subprocess_exec", _make_proc_mock(_SCAN_OK)):
            assert len(await ds.query("any")) == 2
