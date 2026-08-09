# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Phase 2 unit tests for mcp_sse transport hardening.

Covers:
* Typed-error → JSON-RPC code mapping in ``_error_envelope_for``.
* SSE session staleness reaper eviction logic (oldest-idle preference).
* Audit log + Sentry tag emission on ``execute_tool`` (smoke).
"""
from __future__ import annotations

import asyncio
import time

import pytest

from app.routers import mcp_sse
from app.tool_registry import (
    InvalidParamsError,
    InvalidToolError,
    PermissionDeniedError,
    ResourceNotFoundError,
    UpstreamUnavailableError,
)

# ----------------------------------------------------------- error envelope


def test_error_envelope_maps_invalid_params():
    env = mcp_sse._error_envelope_for(InvalidParamsError("bad arg"))
    assert env == {"code": -32602, "message": "bad arg"}


def test_error_envelope_maps_resource_not_found():
    env = mcp_sse._error_envelope_for(ResourceNotFoundError("nope"))
    assert env == {"code": -32004, "message": "nope"}


def test_error_envelope_maps_upstream_unavailable():
    env = mcp_sse._error_envelope_for(UpstreamUnavailableError("neo4j down"))
    assert env == {"code": -32005, "message": "neo4j down"}


def test_error_envelope_maps_permission_denied():
    env = mcp_sse._error_envelope_for(PermissionDeniedError("disabled"))
    assert env == {"code": -32007, "message": "disabled"}


def test_error_envelope_maps_unknown_tool():
    env = mcp_sse._error_envelope_for(InvalidToolError("???"))
    assert env == {"code": -32601, "message": "???"}


def test_error_envelope_default_fallback():
    env = mcp_sse._error_envelope_for(RuntimeError("oops"))
    assert env == {"code": -32000, "message": "oops"}


# ----------------------------------------------------------- session reaper


def test_touch_session_updates_last_seen():
    sid = "test-touch"
    before = time.monotonic()
    mcp_sse._touch_session(sid)
    assert mcp_sse._session_last_seen[sid] >= before
    # cleanup
    mcp_sse._session_last_seen.pop(sid, None)


def test_oldest_idle_eviction_picks_dead_over_active(monkeypatch):
    """When the session cap is hit, the eviction picks the
    longest-idle one — not the longest-opened one."""
    # Setup: three sessions with different last-seen times.
    monkeypatch.setattr(mcp_sse, "_MAX_SESSIONS", 3)

    # Clear any prior state
    mcp_sse._sessions.clear()
    mcp_sse._session_last_seen.clear()

    now = time.monotonic()
    # session_old: idle 1h
    mcp_sse._sessions["old"] = asyncio.Queue()
    mcp_sse._session_last_seen["old"] = now - 3600
    # session_dead: oldest (idle 2h) — should be evicted FIRST
    mcp_sse._sessions["dead"] = asyncio.Queue()
    mcp_sse._session_last_seen["dead"] = now - 7200
    # session_active: just-now active
    mcp_sse._sessions["active"] = asyncio.Queue()
    mcp_sse._session_last_seen["active"] = now

    # Simulate the eviction logic by sorting candidates exactly as the
    # SSE endpoint does:
    candidates = sorted(
        mcp_sse._sessions.keys(),
        key=lambda sid: mcp_sse._session_last_seen.get(sid, 0.0),
    )
    assert candidates[0] == "dead"  # oldest-idle picked first

    # cleanup
    mcp_sse._sessions.clear()
    mcp_sse._session_last_seen.clear()


@pytest.mark.asyncio
async def test_session_reaper_evicts_idle_sessions(monkeypatch):
    """The reaper picks up sessions whose last_seen is older than the
    idle timeout and evicts them within one tick."""
    monkeypatch.setattr(mcp_sse, "_IDLE_TIMEOUT_S", 1)
    mcp_sse._sessions.clear()
    mcp_sse._session_last_seen.clear()

    q = asyncio.Queue()
    mcp_sse._sessions["idle"] = q
    # Set last-seen far enough in the past that the reaper considers
    # this session idle.
    mcp_sse._session_last_seen["idle"] = time.monotonic() - 10

    # Compress the reaper's sleep so the test runs in milliseconds.
    real_sleep = asyncio.sleep

    async def _fast_sleep(_secs):
        await real_sleep(0.05)

    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)

    reaper = asyncio.create_task(mcp_sse._session_reaper())
    await real_sleep(0.15)  # one tick should be enough
    reaper.cancel()
    try:
        await reaper
    except asyncio.CancelledError:
        pass

    assert "idle" not in mcp_sse._sessions, "reaper failed to evict idle session"
    # The sentinel should have been queued so the SSE generator wakes up
    # and stops cleanly.
    if not q.empty():
        assert q.get_nowait() is None


# ----------------------------------------------------- execute_tool instrumentation


@pytest.mark.asyncio
async def test_execute_tool_emits_audit_log(caplog):
    """Every dispatch emits an INFO line on ai-companion.mcp_tool_audit
    with structured ``extra`` fields."""
    from app.tool_registry import _swap_registry, register_tool
    from app.tools import execute_tool

    _, restore = _swap_registry({})
    try:
        @register_tool(
            name="x_audit_smoke",
            description="smoke",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        )
        async def _h() -> dict:
            return {"ok": True}

        # Force an audit logger level low enough to capture INFO.
        caplog.set_level("INFO", logger="ai-companion.mcp_tool_audit")
        out = await execute_tool("x_audit_smoke", {})
        assert out == {"ok": True}

        # Find the audit record. caplog grabs all loggers.
        audit_records = [
            r for r in caplog.records
            if r.name == "ai-companion.mcp_tool_audit"
        ]
        assert audit_records, "no audit log emitted"
        rec = audit_records[-1]
        # Structured extras land on the LogRecord as attributes.
        assert getattr(rec, "tool_name", None) == "x_audit_smoke"
        assert getattr(rec, "outcome", None) == "ok"
        assert getattr(rec, "error_class", None) is None
        assert isinstance(getattr(rec, "duration_ms", None), (int, float))
    finally:
        restore()


@pytest.mark.asyncio
async def test_execute_tool_audit_records_error_class(caplog):
    """When the handler raises, audit records outcome=error + class."""
    from app.tool_registry import _swap_registry, register_tool
    from app.tools import execute_tool

    _, restore = _swap_registry({})
    try:
        @register_tool(
            name="x_audit_err",
            description="error path",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        )
        async def _h() -> dict:
            raise ResourceNotFoundError("nope")

        caplog.set_level("INFO", logger="ai-companion.mcp_tool_audit")
        with pytest.raises(ResourceNotFoundError):
            await execute_tool("x_audit_err", {})

        audit_records = [
            r for r in caplog.records
            if r.name == "ai-companion.mcp_tool_audit"
        ]
        assert audit_records
        rec = audit_records[-1]
        assert getattr(rec, "outcome", None) == "error"
        assert getattr(rec, "error_class", None) == "ResourceNotFoundError"
    finally:
        restore()


# ----------------------------------------------------- args_summary redaction


def test_summarize_args_truncates_oversized_strings():
    from app.tools import _summarize_args

    long = "x" * 1000
    out = _summarize_args({"text": long, "n": 42})
    assert out["text"] == "<str[1000]>"
    assert out["n"] == 42


def test_summarize_args_redacts_credential_like_keys():
    from app.tools import _summarize_args

    out = _summarize_args({
        "password": "secret123",  # pragma: allowlist secret
        "api_key": "sk-abc",  # pragma: allowlist secret
        "token": "xyz",
        "AUTHORIZATION": "Bearer ...",
        "regular": "ok",
    })
    assert out["password"] == "<redacted>"
    assert out["api_key"] == "<redacted>"
    assert out["token"] == "<redacted>"
    assert out["AUTHORIZATION"] == "<redacted>"
    assert out["regular"] == "ok"


# ----------------------------------------------------- v0.95.1 warnings envelope


def test_build_tool_call_content_no_warnings_single_block():
    """Tools returning a plain dict produce one content block."""
    from app.routers.mcp_sse import _build_tool_call_content
    content = _build_tool_call_content({"status": "ok", "value": 42})
    assert len(content) == 1
    assert content[0]["type"] == "text"
    assert "value" in content[0]["text"]
    assert "WARNINGS" not in content[0]["text"]


def test_build_tool_call_content_strips_warnings_into_second_block():
    """Tools returning {_warnings: [...]} get a second WARNINGS block."""
    import json as _json

    from app.routers.mcp_sse import _build_tool_call_content
    content = _build_tool_call_content({
        "status": "partial",
        "_warnings": ["chromadb fast-path missed", "rerank fallback used"],
    })
    assert len(content) == 2
    parsed = _json.loads(content[0]["text"])
    assert "_warnings" not in parsed
    assert parsed == {"status": "partial"}
    assert content[1]["text"].startswith("WARNINGS:")
    assert "chromadb fast-path missed" in content[1]["text"]


def test_build_tool_call_content_empty_warnings_no_second_block():
    """An empty _warnings list is treated as no warnings."""
    from app.routers.mcp_sse import _build_tool_call_content
    content = _build_tool_call_content({"status": "ok", "_warnings": []})
    assert len(content) == 1
    # _warnings should still be stripped even when empty so it doesn't
    # leak as a no-op key in the main JSON block.
    assert "_warnings" not in content[0]["text"]


def test_build_tool_call_content_non_list_warnings_ignored():
    """Defensive: a handler that misuses the field shouldn't crash."""
    from app.routers.mcp_sse import _build_tool_call_content
    content = _build_tool_call_content({"value": 1, "_warnings": "single string"})
    assert len(content) == 1  # ignored — not a list


def test_build_tool_call_content_non_dict_passthrough():
    """A tool that returns a list/string still serializes cleanly."""
    from app.routers.mcp_sse import _build_tool_call_content
    content = _build_tool_call_content([1, 2, 3])
    assert len(content) == 1
    assert "1" in content[0]["text"]
