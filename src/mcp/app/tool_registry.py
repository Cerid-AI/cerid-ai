# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Decorator-based MCP tool registry.

Replaces the legacy ``MCP_TOOLS`` list + ``execute_tool`` if/elif chain
in ``app/tools.py`` with a unified registration surface that colocates
each tool's schema + handler + metadata in one place.

Migration strategy
------------------

Both patterns coexist during Phase 1 of the v0.95 overhaul:

* ``MCP_TOOLS`` (legacy) — list-of-dicts in ``tools.py``, dispatched
  via ``execute_tool``'s if/elif chain.
* ``TOOL_REGISTRY`` (this module) — ``ToolDef`` instances registered
  via ``@register_tool``, dispatched via ``execute_registered_tool``.

``get_all_tools()`` in ``tools.py`` returns the union; ``execute_tool``
checks the registry first then falls through to the legacy chain.
New tools land via the decorator; existing tools migrate opportunistically
as their schemas are touched.

Why both: a wholesale rewrite of 29 tools' if/elif → decorator would
churn 900 LOC in one PR. Coexistence lets each migration ship as its
own commit with a focused diff.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

logger = logging.getLogger("ai-companion.tool_registry")


# ----------------------------------------------------------------- error types

class ToolError(Exception):
    """Base for tool-call errors that map onto JSON-RPC error codes.

    Each subclass exposes ``json_rpc_code`` matching the MCP spec /
    JSON-RPC error semantics. The SSE transport layer reads this when
    building the error response so clients can distinguish validation
    failures from upstream outages.
    """
    json_rpc_code: int = -32603  # internal error (default)


class InvalidToolError(ToolError):
    """Tool name not registered."""
    json_rpc_code = -32601  # method not found


class InvalidParamsError(ToolError):
    """Tool received invalid or missing required params."""
    json_rpc_code = -32602


class ResourceNotFoundError(ToolError):
    """Referenced artifact / pack / memory / etc. doesn't exist."""
    json_rpc_code = -32004  # MCP custom: not found


class UpstreamUnavailableError(ToolError):
    """A required upstream (neo4j / chroma / redis / openrouter) is down."""
    json_rpc_code = -32005  # MCP custom: upstream unreachable


class QuotaExceededError(ToolError):
    """Rate limit / quota exhausted."""
    json_rpc_code = -32006


class PermissionDeniedError(ToolError):
    """Tool is disabled by config (MCP_DISABLED_TOOLS) or feature flag."""
    json_rpc_code = -32007


# --------------------------------------------------------------- tool dataclass

CostClass = Literal["low", "medium", "high"]
"""Coarse handler-cost hint surfaced in schemas.

- ``low``    — pure-local read; expected p95 < 200ms (e.g. ``pkb_health``)
- ``medium`` — single LLM/embed call or modest neo4j traversal; p95 < 2s
- ``high``   — chained LLM + retrieval + reranking; p95 up to 8s
"""

# Authoritative p95 budget mapping. Mirrors the CostClass docstring so the
# numbers above and the values clients/operators see are the same source.
# Used by the contract test in tests/contract/test_latency_budget.py and
# surfaced via the SDK / tool registry. Drift here is a CI failure.
COST_CLASS_P95_BUDGET_MS: dict[str, int] = {
    "low": 200,
    "medium": 2_000,
    "high": 8_000,
}


@dataclass
class ToolDef:
    """One registered MCP tool — schema + handler + metadata."""

    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    handler: Callable[..., Awaitable[Any]]

    cost_class: CostClass = "low"

    # Lifecycle metadata. ``deprecated_since`` triggers a one-call-per-
    # session log warning + surfaces in tools/list as ``_deprecated_*``
    # extension fields so the LLM can route away from the deprecated
    # name.
    deprecated_since: str | None = None
    deprecated_replaced_by: str | None = None

    # ``feature_flag`` = env var name. When set, the tool only loads
    # when the env var is truthy ("1"/"true"/"yes"/"on"). Used for
    # destructive tools (pkb_artifact_delete) that operators may want
    # to gate, or experimental tools that aren't ready for default-on.
    feature_flag: str | None = None

    # Populated by ``_resolve_enabled``; never set by callers directly.
    enabled: bool = field(default=True, init=False)

    def to_mcp_schema(self) -> dict[str, Any]:
        """Return the MCP ``tools/list`` shape for this tool.

        MCP 2024-11-05+ requires ``inputSchema``; ``outputSchema`` is
        optional but every cerid-kb tool ships one. The ``_deprecated_*``
        + ``_cost_class`` fields are MCP-spec extensions — clients that
        don't recognize them ignore them; clients that do (Claude Code's
        loader after v0.95) can use them for routing decisions.
        """
        d: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
            "outputSchema": self.output_schema,
            "_cost_class": self.cost_class,
        }
        if self.deprecated_since:
            d["_deprecated_since"] = self.deprecated_since
        if self.deprecated_replaced_by:
            d["_deprecated_replaced_by"] = self.deprecated_replaced_by
        return d


# --------------------------------------------------------------- registry state

TOOL_REGISTRY: dict[str, ToolDef] = {}
"""Global, mutable, populated at import-time by ``@register_tool`` decorators.

Module-level mutable state mirrors the legacy ``MCP_TOOLS`` list; both
get filled as ``app.tools`` and its tool-source modules import. Tests
that need a clean registry use ``_swap_registry`` (below) to substitute
a scoped registry.
"""


_deprecation_warned: set[str] = set()
"""Sessions × deprecated-tool names already warned once. We warn one
time per process per tool, not per call, to keep the log signal-rich."""


# ------------------------------------------------------------------ decorator

def register_tool(
    name: str,
    *,
    description: str,
    input_schema: dict[str, Any],
    output_schema: dict[str, Any],
    cost_class: CostClass = "low",
    deprecated_since: str | None = None,
    deprecated_replaced_by: str | None = None,
    feature_flag: str | None = None,
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """Register an async handler as an MCP tool.

    The decorator is the canonical entry point for all new tools post-
    Phase 1.6. Schema and handler stay colocated so a single editor
    can review both together; the schema-fidelity CI gate
    (``tests/test_mcp_tool_schema_fidelity.py``) round-trips each
    registered handler's return shape against ``output_schema``.

    Description style (Phase 1.4 standard):

        "{action verb-phrase}. **Use when** {trigger}. **Returns**
         {result-summary}. {optional caveats}"

    See ``docs/MCP_TOOL_STYLE.md`` for examples + the description
    linter at ``scripts/lint-mcp-descriptions.py``.

    Example::

        @register_tool(
            name="pkb_summarize_artifact",
            description=(
                "Summarize a single artifact. **Use when** the user "
                "wants a quick read of a long doc without paging through "
                "chunks. **Returns** ``{summary, key_points, word_count}``."
            ),
            input_schema={...},
            output_schema={...},
            cost_class="medium",
        )
        async def summarize_artifact(artifact_id: str, length: str = "medium") -> dict:
            ...
    """

    def decorator(
        fn: Callable[..., Awaitable[Any]],
    ) -> Callable[..., Awaitable[Any]]:
        if name in TOOL_REGISTRY:
            # Double-registration is almost always a copy-paste bug. Fail
            # loudly at import so the cause is obvious.
            raise ValueError(
                f"tool_registry: {name!r} already registered "
                f"(handler={TOOL_REGISTRY[name].handler!r})"
            )
        if "type" in output_schema and output_schema["type"] != "object":
            # MCP 2024-11-05+ requires outputSchema.type == "object".
            # Catch the pkb_artifacts bug class at registration time.
            raise ValueError(
                f"tool_registry: {name!r} outputSchema.type must be 'object' "
                f"(got {output_schema['type']!r}). Wrap arrays/strings in an "
                f"object envelope per the MCP spec."
            )
        TOOL_REGISTRY[name] = ToolDef(
            name=name,
            description=description,
            input_schema=input_schema,
            output_schema=output_schema,
            handler=fn,
            cost_class=cost_class,
            deprecated_since=deprecated_since,
            deprecated_replaced_by=deprecated_replaced_by,
            feature_flag=feature_flag,
        )
        return fn

    return decorator


# ---------------------------------------------------------------- enable gating

def _truthy(v: str) -> bool:
    return v.lower() in ("1", "true", "yes", "on")


def resolve_enabled() -> None:
    """Apply ``MCP_DISABLED_TOOLS`` + per-tool ``feature_flag`` gating.

    Called once at app startup (``app/main.py`` lifespan) so the
    enabled state is stable across requests. Operators can disable
    individual tools by listing them in ``MCP_DISABLED_TOOLS=tool_a,tool_b``;
    tools with ``feature_flag`` set load only when their named env var
    is truthy (``"1"``, ``"true"``, ``"yes"``, ``"on"`` — case-insensitive).
    """
    disabled = {s.strip() for s in os.getenv("MCP_DISABLED_TOOLS", "").split(",")}
    disabled.discard("")
    for name, t in TOOL_REGISTRY.items():
        if name in disabled:
            t.enabled = False
            logger.info("tool_registry: %s disabled via MCP_DISABLED_TOOLS", name)
            continue
        if t.feature_flag is not None:
            t.enabled = _truthy(os.getenv(t.feature_flag, ""))
            if not t.enabled:
                logger.info(
                    "tool_registry: %s gated off (feature_flag=%s not set)",
                    name,
                    t.feature_flag,
                )
            continue
        t.enabled = True


# ---------------------------------------------------------------- public API

def get_registered_schemas() -> list[dict[str, Any]]:
    """Return MCP schemas for enabled tools, alphabetised by name.

    Order is stable across calls so the LLM's tool-list cache stays warm.
    """
    return [
        t.to_mcp_schema()
        for t in sorted(TOOL_REGISTRY.values(), key=lambda x: x.name)
        if t.enabled
    ]


async def execute_registered_tool(name: str, arguments: dict[str, Any]) -> Any:
    """Dispatch a registered tool by name.

    Raises typed exceptions (``InvalidToolError``, ``PermissionDeniedError``,
    ``InvalidParamsError``) that the SSE transport layer maps onto
    JSON-RPC error codes.
    """
    t = TOOL_REGISTRY.get(name)
    if t is None:
        raise InvalidToolError(f"Unknown tool: {name!r}")
    if not t.enabled:
        raise PermissionDeniedError(
            f"Tool {name!r} is disabled (config or feature flag)"
        )
    if t.deprecated_since and name not in _deprecation_warned:
        logger.warning(
            "tool_registry: %s deprecated since %s — use %s",
            name,
            t.deprecated_since,
            t.deprecated_replaced_by or "<unspecified>",
        )
        _deprecation_warned.add(name)

    try:
        return await t.handler(**arguments)
    except TypeError as exc:
        # Distinguish "bad caller params" (TypeError from **arguments
        # mismatch) from genuine TypeErrors inside the handler. The
        # latter shouldn't be swallowed into InvalidParamsError.
        msg = str(exc)
        if "unexpected keyword argument" in msg or "missing" in msg and "required" in msg:
            raise InvalidParamsError(str(exc)) from exc
        raise


# ----------------------------------------------------------- test helpers

def _swap_registry(new_registry: dict[str, ToolDef] | None = None):
    """Context-managerish helper for tests that need a clean registry.

    Mutates ``TOOL_REGISTRY`` *in place* (clear + update) so test
    fixtures that imported ``TOOL_REGISTRY`` by name see the cleared
    state — reassigning the module-level binding wouldn't propagate
    through ``from app.tool_registry import TOOL_REGISTRY`` imports.

    Returns a 2-tuple ``(prior_snapshot, restore)``: ``restore`` is a
    zero-arg callable that puts the prior contents back.
    """
    prior = dict(TOOL_REGISTRY)
    TOOL_REGISTRY.clear()
    if new_registry:
        TOOL_REGISTRY.update(new_registry)

    def restore() -> None:
        TOOL_REGISTRY.clear()
        TOOL_REGISTRY.update(prior)

    return prior, restore
