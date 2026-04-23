# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Sprint 1A.2 — MCP client governance + audit.

Verifies:

* Mode resolution honors ``MCP_CLIENT_MODE`` env (default permissive,
  unknown values warn + fall back, never module-level captured).
* Allowlist parsing strips whitespace, drops empty entries, lowercases.
* :func:`enforce_call` permits / denies as expected for all three modes.
* :func:`audit_call` always emits an INFO log with structured fields,
  never raises even when ``sentry_sdk`` is missing or breadcrumbs throw.
* The wrapped ``dispatch_external_mcp_tool`` audits ok / fail / denied
  outcomes and respects governance — the secure-enterprise contract.
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest

from app.services import mcp_client_policy
from app.services.external_mcp_dispatch import dispatch_external_mcp_tool
from app.services.mcp_client_policy import (
    ENV_ALLOWLIST,
    ENV_MODE,
    MCPClientMode,
    MCPPolicyDenied,
    audit_call,
    current_allowlist,
    current_mode,
    enforce_call,
)
from utils.mcp_client import ExternalTool, mcp_client_manager

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fixture — inject one external tool so dispatcher integration tests resolve
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_tool(monkeypatch: pytest.MonkeyPatch):
    """Inject a single discovered external tool into the singleton manager.

    Cleans up on teardown so other tests see an empty external palette.
    Also clears any leftover env vars so each test starts clean.
    """
    monkeypatch.delenv(ENV_MODE, raising=False)
    monkeypatch.delenv(ENV_ALLOWLIST, raising=False)
    tool = ExternalTool(
        server_name="acmeserver",
        tool_name="lookup",
        namespaced_name="ext_acmeserver_lookup",
        description="[acmeserver] lookup",
        input_schema={"type": "object", "properties": {}},
    )
    original = dict(mcp_client_manager._tools)
    mcp_client_manager._tools.clear()
    mcp_client_manager._tools[tool.namespaced_name] = tool
    yield tool
    mcp_client_manager._tools.clear()
    mcp_client_manager._tools.update(original)


# ---------------------------------------------------------------------------
# Mode resolution
# ---------------------------------------------------------------------------


class TestCurrentMode:
    async def test_default_is_permissive(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv(ENV_MODE, raising=False)
        assert current_mode() is MCPClientMode.PERMISSIVE

    async def test_disabled_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENV_MODE, "disabled")
        assert current_mode() is MCPClientMode.DISABLED

    async def test_allowlist_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENV_MODE, "allowlist")
        assert current_mode() is MCPClientMode.ALLOWLIST

    async def test_uppercase_value_normalized(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(ENV_MODE, "DISABLED")
        assert current_mode() is MCPClientMode.DISABLED

    async def test_unknown_value_warns_and_defaults_permissive(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Failing closed (DISABLED) on a typo silently breaks deployments;
        warn + permissive is the lesser evil. Operators see the warning."""
        monkeypatch.setenv(ENV_MODE, "strict")  # not a real mode
        with caplog.at_level(logging.WARNING, logger="ai-companion.mcp_client_policy"):
            assert current_mode() is MCPClientMode.PERMISSIVE
        assert any("Unknown" in r.message for r in caplog.records)

    async def test_mode_read_per_call_not_at_import(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Per-call read — operator can flip the env var without restart.
        Regression guard against the lessons.md module-level capture bug."""
        monkeypatch.setenv(ENV_MODE, "permissive")
        assert current_mode() is MCPClientMode.PERMISSIVE
        monkeypatch.setenv(ENV_MODE, "disabled")
        assert current_mode() is MCPClientMode.DISABLED


# ---------------------------------------------------------------------------
# Allowlist parsing
# ---------------------------------------------------------------------------


class TestCurrentAllowlist:
    async def test_empty_when_unset(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv(ENV_ALLOWLIST, raising=False)
        assert current_allowlist() == set()

    async def test_single_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENV_ALLOWLIST, "slack")
        assert current_allowlist() == {"slack"}

    async def test_comma_separated(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(ENV_ALLOWLIST, "slack,drive,confluence")
        assert current_allowlist() == {"slack", "drive", "confluence"}

    async def test_whitespace_stripped(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(ENV_ALLOWLIST, "  slack ,  drive  ,confluence")
        assert current_allowlist() == {"slack", "drive", "confluence"}

    async def test_empty_entries_skipped(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(ENV_ALLOWLIST, "slack,,,drive,")
        assert current_allowlist() == {"slack", "drive"}

    async def test_lowercased(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENV_ALLOWLIST, "Slack,DRIVE")
        assert current_allowlist() == {"slack", "drive"}


# ---------------------------------------------------------------------------
# Enforcement — the security-critical core
# ---------------------------------------------------------------------------


class TestEnforceCall:
    async def test_permissive_allows_anything(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(ENV_MODE, "permissive")
        # Any server name passes — no exception
        enforce_call("anyserver")
        enforce_call("evil-corp")

    async def test_disabled_denies_everything(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(ENV_MODE, "disabled")
        with pytest.raises(MCPPolicyDenied) as ei:
            enforce_call("trusted")
        assert ei.value.server_name == "trusted"
        assert ei.value.mode == "disabled"
        assert "disabled" in str(ei.value).lower()

    async def test_allowlist_permits_listed(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(ENV_MODE, "allowlist")
        monkeypatch.setenv(ENV_ALLOWLIST, "slack,drive")
        enforce_call("slack")
        enforce_call("drive")

    async def test_allowlist_denies_unlisted(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(ENV_MODE, "allowlist")
        monkeypatch.setenv(ENV_ALLOWLIST, "slack")
        with pytest.raises(MCPPolicyDenied) as ei:
            enforce_call("unauthorized")
        assert ei.value.mode == "allowlist"
        assert "MCP_CLIENT_ALLOWLIST" in str(ei.value)

    async def test_allowlist_empty_denies_all(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """ALLOWLIST mode + empty allowlist is the explicit lockdown
        posture — distinct from DISABLED so operators can stage the
        flip (set MODE first, then populate the allowlist)."""
        monkeypatch.setenv(ENV_MODE, "allowlist")
        monkeypatch.delenv(ENV_ALLOWLIST, raising=False)
        with pytest.raises(MCPPolicyDenied) as ei:
            enforce_call("any")
        assert "<empty>" in str(ei.value)

    async def test_allowlist_case_insensitive(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(ENV_MODE, "allowlist")
        monkeypatch.setenv(ENV_ALLOWLIST, "Slack")
        enforce_call("SLACK")  # should not raise

    async def test_denied_is_permission_error_subclass(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Subclass of PermissionError so existing FastAPI / asyncio
        error-handling paths classify it correctly."""
        monkeypatch.setenv(ENV_MODE, "disabled")
        with pytest.raises(PermissionError):
            enforce_call("x")


# ---------------------------------------------------------------------------
# Audit — must never raise, must always log
# ---------------------------------------------------------------------------


class TestAuditCall:
    async def test_logs_at_info_with_structured_fields(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level(logging.INFO, logger="ai-companion.mcp_client_policy"):
            audit_call(
                tool_name="lookup", server_name="acme",
                status="ok", elapsed_s=0.123,
            )
        record = next(
            r for r in caplog.records if "external-mcp call" in r.message
        )
        assert record.levelno == logging.INFO
        assert getattr(record, "external_mcp_tool", None) == "lookup"
        assert getattr(record, "external_mcp_server", None) == "acme"
        assert getattr(record, "external_mcp_status", None) == "ok"

    async def test_failure_status_includes_error_in_message(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level(logging.INFO, logger="ai-companion.mcp_client_policy"):
            audit_call(
                tool_name="lookup", server_name="acme",
                status="fail", elapsed_s=0.05, error="boom",
            )
        msg = next(r.message for r in caplog.records if "external-mcp call" in r.message)
        assert "boom" in msg

    async def test_never_raises_on_breadcrumb_failure(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Sentry breadcrumb errors must be swallowed — observability
        must never break the call path."""
        import sentry_sdk
        monkeypatch.setattr(
            sentry_sdk, "add_breadcrumb",
            lambda **_kw: (_ for _ in ()).throw(RuntimeError("sentry down")),
        )
        # Must not raise
        audit_call(
            tool_name="t", server_name="s", status="ok", elapsed_s=0.0,
        )


# ---------------------------------------------------------------------------
# Dispatcher integration — the secure-enterprise acceptance contract
# ---------------------------------------------------------------------------


class TestDispatcherWithGovernance:
    async def test_permissive_calls_manager_and_audits_ok(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_tool: ExternalTool,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv(ENV_MODE, "permissive")
        monkeypatch.setattr(
            mcp_client_manager, "call_tool",
            AsyncMock(return_value="ok-result"),
        )
        with caplog.at_level(logging.INFO, logger="ai-companion.mcp_client_policy"):
            result = await dispatch_external_mcp_tool(
                fake_tool.namespaced_name, {},
            )
        assert result == "ok-result"
        statuses = [
            getattr(r, "external_mcp_status", None) for r in caplog.records
        ]
        assert "ok" in statuses

    async def test_disabled_denies_and_audits_denied(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_tool: ExternalTool,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """DISABLED mode rejects the call BEFORE hitting the wire and
        records a 'denied' audit row — this is the regulated-deployment
        kill switch."""
        monkeypatch.setenv(ENV_MODE, "disabled")
        called = AsyncMock()
        monkeypatch.setattr(mcp_client_manager, "call_tool", called)

        with (
            caplog.at_level(logging.INFO, logger="ai-companion.mcp_client_policy"),
            pytest.raises(MCPPolicyDenied),
        ):
            await dispatch_external_mcp_tool(fake_tool.namespaced_name, {})

        called.assert_not_awaited()
        statuses = [
            getattr(r, "external_mcp_status", None) for r in caplog.records
        ]
        assert "denied" in statuses

    async def test_allowlist_permits_listed_server(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_tool: ExternalTool,
    ) -> None:
        monkeypatch.setenv(ENV_MODE, "allowlist")
        monkeypatch.setenv(ENV_ALLOWLIST, "acmeserver")
        monkeypatch.setattr(
            mcp_client_manager, "call_tool",
            AsyncMock(return_value="ok"),
        )
        result = await dispatch_external_mcp_tool(
            fake_tool.namespaced_name, {},
        )
        assert result == "ok"

    async def test_allowlist_denies_unlisted_server(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_tool: ExternalTool,
    ) -> None:
        monkeypatch.setenv(ENV_MODE, "allowlist")
        monkeypatch.setenv(ENV_ALLOWLIST, "someoneelse")
        called = AsyncMock()
        monkeypatch.setattr(mcp_client_manager, "call_tool", called)

        with pytest.raises(MCPPolicyDenied):
            await dispatch_external_mcp_tool(fake_tool.namespaced_name, {})
        called.assert_not_awaited()

    async def test_manager_failure_audits_fail(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_tool: ExternalTool,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv(ENV_MODE, "permissive")
        monkeypatch.setattr(
            mcp_client_manager, "call_tool",
            AsyncMock(side_effect=RuntimeError("server crashed")),
        )

        with (
            caplog.at_level(logging.INFO, logger="ai-companion.mcp_client_policy"),
            pytest.raises(RuntimeError, match="server crashed"),
        ):
            await dispatch_external_mcp_tool(fake_tool.namespaced_name, {})

        statuses = [
            getattr(r, "external_mcp_status", None) for r in caplog.records
        ]
        assert "fail" in statuses

    async def test_unknown_tool_passes_through_to_manager(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_tool: ExternalTool,
    ) -> None:
        """A name not in the metadata table delegates to the manager so
        its 'Unknown external tool' ValueError surfaces unchanged
        (preserves Sprint 1A.1 error contract)."""
        monkeypatch.setenv(ENV_MODE, "permissive")
        # Manager raises ValueError per its line 249 contract
        monkeypatch.setattr(
            mcp_client_manager, "call_tool",
            AsyncMock(side_effect=ValueError("Unknown external tool: ext_unknown")),
        )
        with pytest.raises(ValueError, match="Unknown external tool"):
            await dispatch_external_mcp_tool("ext_unknown_thing", {})

    async def test_non_ext_name_short_circuits(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Names without ext_ return None without consulting policy or
        manager — non-external dispatch fast path."""
        monkeypatch.setenv(ENV_MODE, "disabled")  # would deny if consulted
        called = AsyncMock()
        monkeypatch.setattr(mcp_client_manager, "call_tool", called)

        assert await dispatch_external_mcp_tool("pkb_query", {}) is None
        called.assert_not_awaited()


# ---------------------------------------------------------------------------
# Module surface — single-source-of-truth env var names
# ---------------------------------------------------------------------------


async def test_env_var_constants_are_canonical() -> None:
    """Hardcoded env var names — protect against accidental rename
    that would silently disable governance."""
    assert mcp_client_policy.ENV_MODE == "MCP_CLIENT_MODE"
    assert mcp_client_policy.ENV_ALLOWLIST == "MCP_CLIENT_ALLOWLIST"
