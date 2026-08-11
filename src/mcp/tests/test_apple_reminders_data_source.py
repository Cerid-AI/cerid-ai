# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for AppleRemindersDataSource — Phase 4.3.

The ``ceridreminders`` Swift helper isn't built or invoked in the test env
(CI may not be on macOS). All subprocess calls are stubbed; we verify JSON
parsing, configuration, and error handling. Note: CeridReminders signals TCC
denial with exit code **77** (not 3 like CeridEventKit/CeridPhotos).
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.apple_reminders import data_source as apple_reminders_ds
from plugins.apple_reminders.data_source import AppleRemindersDataSource


@pytest.fixture
def helper_path(tmp_path):
    p = tmp_path / "ceridreminders"
    p.write_text("#!/bin/sh\nexit 0\n")
    p.chmod(0o755)
    return str(p)


def _make_proc_mock(stdout_bytes: bytes, returncode: int = 0, stderr_bytes: bytes = b""):
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout_bytes, stderr_bytes))
    return AsyncMock(return_value=proc)


_SCAN_OK = json.dumps(
    {"ok": True, "list_count": 3, "list_names": ["Inbox", "Work", "Personal"]}
).encode("utf-8")


class TestConfiguration:
    def test_is_configured_requires_darwin(self, helper_path):
        with patch("platform.system", return_value="Linux"):
            assert AppleRemindersDataSource(helper_path=helper_path).is_configured() is False

    def test_is_configured_requires_helper_present(self, unresolvable_swift_helper):
        # helper_path=None resolves through _resolve_helper_path(), whose last
        # fallback is the developer's swift/build/ — see the fixture's docstring.
        unresolvable_swift_helper(apple_reminders_ds)
        with patch("platform.system", return_value="Darwin"):
            assert AppleRemindersDataSource(helper_path=None).is_configured() is False

    def test_is_configured_when_both_present(self, helper_path):
        with patch("platform.system", return_value="Darwin"):
            assert AppleRemindersDataSource(helper_path=helper_path).is_configured() is True

    def test_env_var_override_takes_precedence(self, helper_path, monkeypatch):
        from plugins.apple_reminders import data_source as mod
        monkeypatch.setenv("CERID_HELPER_CERIDREMINDERS", helper_path)
        # shutil.which must NOT be consulted when the env override resolves.
        with patch.object(mod.shutil, "which", side_effect=AssertionError("should not call which")):
            assert mod._resolve_helper_path() == helper_path


class TestQuery:
    @pytest.mark.asyncio
    async def test_scan_parses_helper_json(self, helper_path):
        ds = AppleRemindersDataSource(helper_path=helper_path)
        with patch("asyncio.create_subprocess_exec", _make_proc_mock(_SCAN_OK)):
            results = await ds.query("any")
        assert len(results) == 3
        assert all(r.source_name == "Apple Reminders" for r in results)
        assert any("Work" in r.title for r in results)

    @pytest.mark.asyncio
    async def test_query_result_shape(self, helper_path):
        ds = AppleRemindersDataSource(helper_path=helper_path)
        with patch("asyncio.create_subprocess_exec", _make_proc_mock(_SCAN_OK)):
            r = (await ds.query("any"))[0]
        assert r.title and r.content and r.source_url and r.source_name
        assert isinstance(r.confidence, float)

    @pytest.mark.asyncio
    async def test_tcc_denial_exit_77_returns_empty(self, helper_path):
        ds = AppleRemindersDataSource(helper_path=helper_path)
        proc = _make_proc_mock(b"", returncode=77, stderr_bytes=b"reminders access denied")
        with patch("asyncio.create_subprocess_exec", proc):
            assert await ds.query("any") == []

    @pytest.mark.asyncio
    async def test_helper_non_zero_exit_returns_empty(self, helper_path):
        ds = AppleRemindersDataSource(helper_path=helper_path)
        with patch("asyncio.create_subprocess_exec", _make_proc_mock(b"", returncode=1, stderr_bytes=b"crash")):
            assert await ds.query("any") == []

    @pytest.mark.asyncio
    async def test_scan_not_ok_returns_empty(self, helper_path):
        ds = AppleRemindersDataSource(helper_path=helper_path)
        bad = json.dumps({"ok": False, "error": "no store"}).encode("utf-8")
        with patch("asyncio.create_subprocess_exec", _make_proc_mock(bad)):
            assert await ds.query("any") == []

    @pytest.mark.asyncio
    async def test_no_helper_path_returns_empty(self, unresolvable_swift_helper):
        unresolvable_swift_helper(apple_reminders_ds)
        assert await AppleRemindersDataSource(helper_path=None).query("any") == []
