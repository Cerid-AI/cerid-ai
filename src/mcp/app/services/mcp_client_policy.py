# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Governance + audit for external MCP tool calls (Sprint 1A.2).

Sprint 1A.1 wired ``MCPClientManager`` into the agent tool-dispatch
path. That alone is unsafe in regulated deployments: any configured
external server's tools become callable by the LLM with no allowlist
and no audit trail. This module is the policy layer that wraps the
dispatch.

Three modes (env var ``MCP_CLIENT_MODE``):

* ``permissive`` — default, preserves Sprint 1A.1 behavior. Every
  configured server's tools are callable. Suitable for personal /
  developer use.
* ``allowlist`` — only servers whose name appears in
  ``MCP_CLIENT_ALLOWLIST`` (comma-separated) are callable. Calls to
  other servers raise :class:`MCPPolicyDenied`. Suitable for most
  enterprise deployments — operators curate the allowlist and reject
  unknown servers.
* ``disabled`` — every external MCP call is denied. Configured servers
  may still appear in ``GET /mcp-servers`` (informational) but the
  agent runtime can never invoke them. Suitable for the most
  compliance-sensitive deployments where the consumed-tools surface
  must be empty.

Both env vars are read **per call** (not at module import) per the
``module-level os.getenv capture`` lesson — operators flipping
``MCP_CLIENT_MODE`` must not have to restart the process.

Audit: every external MCP call (success / failure / denied) emits
a structured INFO log + a Sentry breadcrumb. The audit hook itself
must never raise — observability mirrors the
``log_swallowed_error`` pattern.
"""
from __future__ import annotations

import logging
import os
from enum import Enum
from typing import Any

logger = logging.getLogger("ai-companion.mcp_client_policy")

# Env var names — single source of truth for the governance surface.
ENV_MODE = "MCP_CLIENT_MODE"
ENV_ALLOWLIST = "MCP_CLIENT_ALLOWLIST"


class MCPClientMode(str, Enum):
    """External MCP client governance mode.

    ``str`` mixin so values compare cleanly with raw strings in tests
    and config-loader code.
    """

    PERMISSIVE = "permissive"
    ALLOWLIST = "allowlist"
    DISABLED = "disabled"


class MCPPolicyDenied(PermissionError):
    """Raised when policy denies an external MCP tool call.

    Subclass of ``PermissionError`` so existing FastAPI / asyncio
    error-handling paths classify it as an authorization failure
    rather than a generic exception.
    """

    def __init__(self, server_name: str, mode: str, reason: str) -> None:
        self.server_name = server_name
        self.mode = mode
        self.reason = reason
        super().__init__(
            f"external MCP call to '{server_name}' denied (mode={mode}): {reason}"
        )


# ---------------------------------------------------------------------------
# Mode + allowlist resolution — read at request time, never at import time
# ---------------------------------------------------------------------------


def current_mode() -> MCPClientMode:
    """Return the active governance mode.

    Unknown values fall back to ``PERMISSIVE`` and are logged at
    WARNING — failing closed (DISABLED) on a typo would silently
    break a working deployment, which is worse than the default.
    Operators get a one-time warning, not silent reconfiguration.
    """
    raw = os.getenv(ENV_MODE, MCPClientMode.PERMISSIVE.value).strip().lower()
    try:
        return MCPClientMode(raw)
    except ValueError:
        logger.warning(
            "Unknown %s=%r; falling back to %s",
            ENV_MODE, raw, MCPClientMode.PERMISSIVE.value,
        )
        return MCPClientMode.PERMISSIVE


def current_allowlist() -> set[str]:
    """Return the set of allowlisted server names (lowercased, stripped).

    Empty when ``MCP_CLIENT_ALLOWLIST`` is unset or all-whitespace.
    Combined with ``ALLOWLIST`` mode, an empty allowlist denies
    everything — the explicit "lock down without flipping to
    DISABLED" posture.
    """
    raw = os.getenv(ENV_ALLOWLIST, "")
    return {name.strip().lower() for name in raw.split(",") if name.strip()}


# ---------------------------------------------------------------------------
# Enforcement
# ---------------------------------------------------------------------------


def enforce_call(server_name: str) -> None:
    """Raise :class:`MCPPolicyDenied` if the active policy disallows the call.

    Returns silently on permit. Caller is the dispatcher
    (``app.services.external_mcp_dispatch``); a denial here aborts
    the call before it hits the wire.
    """
    mode = current_mode()
    if mode is MCPClientMode.DISABLED:
        raise MCPPolicyDenied(
            server_name, mode.value,
            "external MCP client is disabled (MCP_CLIENT_MODE=disabled)",
        )
    if mode is MCPClientMode.ALLOWLIST:
        allowed = current_allowlist()
        if server_name.lower() not in allowed:
            allowed_str = ",".join(sorted(allowed)) if allowed else "<empty>"
            raise MCPPolicyDenied(
                server_name, mode.value,
                f"server not in MCP_CLIENT_ALLOWLIST (allowed: {allowed_str})",
            )
    # PERMISSIVE: no-op


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


def audit_call(
    *,
    tool_name: str,
    server_name: str,
    status: str,  # "ok" | "fail" | "denied"
    elapsed_s: float,
    error: str | None = None,
) -> None:
    """Emit a structured audit record for one external MCP call.

    Always logs at INFO and (when sentry_sdk is installed) adds a
    breadcrumb. Never raises — observability errors must not break
    the call path. Mirrors ``core.utils.swallowed.log_swallowed_error``.
    """
    logger.info(
        "external-mcp call: %s.%s status=%s elapsed=%.3fs%s",
        server_name, tool_name, status, elapsed_s,
        f" error={error}" if error else "",
        extra={
            "external_mcp_tool": tool_name,
            "external_mcp_server": server_name,
            "external_mcp_status": status,
            "external_mcp_elapsed_s": elapsed_s,
        },
    )
    try:
        import sentry_sdk  # type: ignore[import-not-found]
        sentry_sdk.add_breadcrumb(
            category="mcp_client",
            message=f"{server_name}.{tool_name} {status}",
            level="warning" if status != "ok" else "info",
            data=_breadcrumb_data(tool_name, server_name, status, elapsed_s, error),
        )
    except ImportError:
        pass
    except Exception:  # noqa: BLE001 — observability must never raise
        pass


def _breadcrumb_data(
    tool_name: str, server_name: str, status: str, elapsed_s: float, error: str | None,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "tool": tool_name,
        "server": server_name,
        "status": status,
        "elapsed_s": round(elapsed_s, 4),
    }
    if error:
        data["error"] = error
    return data
