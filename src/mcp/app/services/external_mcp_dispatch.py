# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""External MCP tool dispatcher — bridges MCPClientManager into the agent
tool-call path (Sprint 1A.1) with governance + audit (Sprint 1A.2).

Adapter that lets the agent runtime call tools served by external MCP
servers exactly like built-in ``pkb_*`` tools. ``MCPClientManager``
(``utils.mcp_client``) already handles connection lifecycle, tool
discovery, and per-server routing; this module is the thin glue that
makes it visible to ``app.tools.execute_tool`` via the existing
``_tool_dispatchers`` extension hook.

Two surfaces:

* :func:`dispatch_external_mcp_tool` — a ``(name, arguments) -> result | None``
  callable registered into ``_tool_dispatchers``. Returns ``None`` for
  non-``ext_`` names so other dispatchers can claim them; for ``ext_*``
  names, enforces governance via ``mcp_client_policy.enforce_call``,
  invokes the manager, and audits the outcome (ok / fail / denied).
* :func:`get_external_tool_schemas` — returns the manager's discovered
  tools in ``MCP_TOOLS``-compatible dict form. The SSE ``tools/list``
  response concatenates this with the static ``MCP_TOOLS``.
"""
from __future__ import annotations

import time
from typing import Any

from app.services.mcp_client_policy import (
    MCPPolicyDenied,
    audit_call,
    enforce_call,
)
from utils.mcp_client import mcp_client_manager

# External tools are namespaced by ``MCPClientManager._discover_tools``
# as ``ext_<server_name>_<tool_name>`` to avoid colliding with ``pkb_*``.
EXTERNAL_PREFIX = "ext_"


async def dispatch_external_mcp_tool(
    name: str, arguments: dict[str, Any],
) -> Any | None:
    """Route an external MCP tool call to its configured server.

    Returns ``None`` for names without the ``ext_`` prefix so the next
    dispatcher in the chain can claim them.

    For ``ext_*`` names: looks up the owning server via the manager's
    metadata table (server / tool names can both contain underscores,
    so the namespaced string is ambiguous — the table is authoritative),
    enforces the active governance policy, invokes the manager, and
    audits the outcome. Three exit paths, all audited:

    * **ok** — call succeeded; returns the result.
    * **fail** — manager raised; re-raises after audit.
    * **denied** — policy denied; raises :class:`MCPPolicyDenied`
      after audit. The wire call never happens.

    A name not in the metadata table delegates to the manager so its
    canonical "Unknown external tool" ``ValueError`` propagates
    unchanged (preserves the Sprint 1A.1 error surface).
    """
    if not name.startswith(EXTERNAL_PREFIX):
        return None

    tool = mcp_client_manager.get_tool_metadata(name)
    if tool is None:
        # Unknown name — defer to the manager so its ValueError surfaces
        # exactly as in Sprint 1A.1. No audit record because we don't
        # know which server (if any) it would have hit.
        return await mcp_client_manager.call_tool(name, arguments)

    start = time.monotonic()
    status = "ok"
    error: str | None = None
    try:
        enforce_call(tool.server_name)
        return await mcp_client_manager.call_tool(name, arguments)
    except MCPPolicyDenied as exc:
        status = "denied"
        error = str(exc)
        raise
    except Exception as exc:
        status = "fail"
        error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        audit_call(
            tool_name=tool.tool_name,
            server_name=tool.server_name,
            status=status,
            elapsed_s=time.monotonic() - start,
            error=error,
        )


def get_external_tool_schemas() -> list[dict[str, Any]]:
    """Return discovered external-MCP tools in ``MCP_TOOLS``-compatible format.

    Empty list when no servers are connected — callers concatenate this
    with the static ``MCP_TOOLS`` array. Schemas refresh as servers
    connect/disconnect; callers should re-invoke per ``tools/list``.
    """
    return mcp_client_manager.list_external_tools()
