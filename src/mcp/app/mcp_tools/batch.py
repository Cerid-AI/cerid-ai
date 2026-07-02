# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Phase 7 batch + external-ingest tools.

* ``pkb_batch`` — atomic multi-step orchestration with output references.
* ``pkb_ingest_url`` — HTTP fetch + ingest. Non-JS-rendering for now;
  upgrade path via Playwright/Chrome-DevTools MCPs documented in
  the function docstring.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from app.tool_registry import (
    InvalidParamsError,
    register_tool,
)

logger = logging.getLogger("ai-companion.mcp_tools.batch")


# ============================================================ pkb_batch


# Pattern for `${<op_id>.result.field}` references inside argument
# values. ``op_id`` is any identifier-shaped token so users can name
# ops descriptively (``step1``, ``ingest``, ``fetch_url``) — the
# decorator-set default is ``op_<index>`` but custom names are allowed.
# Supports nested fields via dotted paths and array indexing via [N].
_REF_RE = re.compile(  # noqa: DUO138 — bounded internal template refs, no untrusted input
    r"\$\{([a-zA-Z_][a-zA-Z_0-9]*)\.result((?:\.[a-zA-Z_][a-zA-Z_0-9]*|\[\d+\])*)\}"
)


def _resolve_path(value: Any, path: str) -> Any:
    """Navigate ``path`` into ``value`` for reference substitution.

    Path examples:
    *  ``""``                         → value (root)
    *  ``".artifact.id"``             → value["artifact"]["id"]
    *  ``".chunks[0].text"``          → value["chunks"][0]["text"]

    Returns the resolved value or ``None`` if any hop misses.
    """
    if not path:
        return value
    cursor = value
    # Tokenise: split on `.` and `[N]` while preserving them
    tokens = re.findall(r"\.([a-zA-Z_][a-zA-Z_0-9]*)|\[(\d+)\]", path)
    for key, idx in tokens:
        if key:
            if not isinstance(cursor, dict) or key not in cursor:
                return None
            cursor = cursor[key]
        elif idx:
            i = int(idx)
            if not isinstance(cursor, list) or i >= len(cursor):
                return None
            cursor = cursor[i]
    return cursor


def _substitute_refs(value: Any, results: dict[str, Any]) -> Any:
    """Walk ``value`` recursively, substituting ``${op_X.result...}``.

    Each reference must resolve to a previously-completed op's result.
    If the reference points at an op that hasn't run yet (DAG cycle or
    bad ordering) ``InvalidParamsError`` propagates so the batch
    fails fast rather than substituting ``None`` silently.
    """
    if isinstance(value, str):
        # Whole-string match → return the referenced object as-is so
        # the caller can pass through e.g. dicts and lists.
        m = _REF_RE.fullmatch(value)
        if m:
            op_id, path = m.group(1), m.group(2)
            if op_id not in results:
                raise InvalidParamsError(
                    f"reference {value!r} points at op {op_id!r} "
                    "which has not (yet) completed"
                )
            resolved = _resolve_path(results[op_id], path)
            return resolved

        # Substring match → string-interpolate
        def _replace(m: re.Match) -> str:
            op_id, path = m.group(1), m.group(2)
            if op_id not in results:
                raise InvalidParamsError(
                    f"reference {m.group(0)!r} points at op {op_id!r} "
                    "which has not (yet) completed"
                )
            resolved = _resolve_path(results[op_id], path)
            return "" if resolved is None else str(resolved)

        return _REF_RE.sub(_replace, value)

    if isinstance(value, dict):
        return {k: _substitute_refs(v, results) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_refs(v, results) for v in value]
    return value


def _topo_sort(ops: list[dict[str, Any]]) -> list[int]:
    """Return execution order over ``ops`` honoring depends_on edges.

    Raises ``InvalidParamsError`` on a cycle or a depends_on pointing
    at a non-existent op id.
    """
    ids = [o.get("op_id") or f"op_{i}" for i, o in enumerate(ops)]
    if len(set(ids)) != len(ids):
        raise InvalidParamsError("duplicate op_id in batch operations")
    id_to_idx = {i: n for n, i in enumerate(ids)}

    deps: dict[int, list[int]] = {n: [] for n in range(len(ops))}
    for n, op in enumerate(ops):
        for dep_id in op.get("depends_on") or []:
            if dep_id not in id_to_idx:
                raise InvalidParamsError(
                    f"op {ids[n]!r} depends_on missing op_id {dep_id!r}"
                )
            deps[n].append(id_to_idx[dep_id])

    # Kahn's algorithm
    in_degree = {n: len(deps[n]) for n in deps}
    ready = [n for n, d in in_degree.items() if d == 0]
    ordered: list[int] = []
    while ready:
        # Stable order — pick smallest index for determinism
        ready.sort()
        n = ready.pop(0)
        ordered.append(n)
        for m, m_deps in deps.items():
            if n in m_deps:
                m_deps.remove(n)
                in_degree[m] -= 1
                if in_degree[m] == 0:
                    ready.append(m)
    if len(ordered) != len(ops):
        raise InvalidParamsError("dependency cycle detected in batch")
    return ordered


@register_tool(
    name="pkb_batch",
    description=(
        "Run up to 10 tool calls as one atomic batch with explicit "
        "dependency ordering. Each operation can reference earlier "
        "outputs via `${op_X.result.path}` interpolation (e.g. "
        "`${op_0.result.artifacts[0].id}`). **Use when** the LLM "
        "knows it needs N sequential calls and wants to issue them "
        "as one round-trip — for example: search → recategorize-bulk "
        "→ curate. **Returns** `{results: {op_id: result}, status, "
        "completed, failed, ordering}`. By default a failed op stops "
        "the batch (`continue_on_error=false`); pass `true` to keep "
        "going. Refuses cycles in depends_on. Refuses >10 operations."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "operations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "tool": {
                            "type": "string",
                            "description": "Tool name to invoke",
                        },
                        "arguments": {
                            "type": "object",
                            "description": "Tool arguments, may contain ${op_X.result...} refs",
                        },
                        "op_id": {
                            "type": "string",
                            "description": (
                                "Optional id used by depends_on + refs. "
                                "Defaults to 'op_<index>'."
                            ),
                        },
                        "depends_on": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Op ids that must complete before this one runs",
                        },
                    },
                    "required": ["tool", "arguments"],
                },
                "description": "1-10 operations to run",
            },
            "continue_on_error": {
                "type": "boolean",
                "description": "Keep running remaining ops after one fails",
                "default": False,
            },
        },
        "required": ["operations"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "results": {"type": "object"},
            "status": {
                "type": "string",
                "description": "'ok' if all completed; 'partial' or 'aborted' on failure",
            },
            "completed": {"type": "integer"},
            "failed": {"type": "integer"},
            "ordering": {"type": "array", "items": {"type": "string"}},
            "errors": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "op_id": {"type": "string"},
                        "tool": {"type": "string"},
                        "error_class": {"type": "string"},
                        "message": {"type": "string"},
                    },
                },
            },
        },
    },
    cost_class="high",
)
async def pkb_batch(
    operations: list[dict[str, Any]],
    continue_on_error: bool = False,
) -> dict[str, Any]:
    if not isinstance(operations, list) or not operations:
        raise InvalidParamsError("operations must be a non-empty list")
    if len(operations) > 10:
        raise InvalidParamsError(
            f"refuse batch with {len(operations)} operations (max 10) — "
            "split into multiple calls"
        )
    for i, op in enumerate(operations):
        if "tool" not in op or "arguments" not in op:
            raise InvalidParamsError(
                f"operation #{i} must have 'tool' and 'arguments'"
            )
        if not isinstance(op["arguments"], dict):
            raise InvalidParamsError(f"operation #{i}.arguments must be an object")
        if op["tool"] == "pkb_batch":
            # No nesting — opens the door to misuse + makes debugging
            # painful when a batch fails inside a batch.
            raise InvalidParamsError("pkb_batch cannot invoke itself")

    # Resolve execution order via topological sort
    order = _topo_sort(operations)
    ids = [op.get("op_id") or f"op_{i}" for i, op in enumerate(operations)]

    # Lazy-import the dispatcher to avoid a circular import at module load
    from app.tools import execute_tool

    results: dict[str, Any] = {}
    errors: list[dict[str, str]] = []
    completed = 0
    failed = 0
    status = "ok"

    for idx in order:
        op = operations[idx]
        op_id = ids[idx]
        tool_name = op["tool"]
        try:
            args = _substitute_refs(op["arguments"], results)
        except InvalidParamsError as e:
            errors.append({
                "op_id": op_id, "tool": tool_name,
                "error_class": "InvalidParamsError", "message": str(e),
            })
            failed += 1
            if not continue_on_error:
                status = "aborted"
                break
            continue

        try:
            result = await execute_tool(tool_name, args)
            results[op_id] = result
            completed += 1
        except Exception as e:
            from core.utils.swallowed import log_swallowed_error
            log_swallowed_error('app.mcp_tools.batch', e)
            errors.append({
                "op_id": op_id,
                "tool": tool_name,
                "error_class": type(e).__name__,
                "message": str(e),
            })
            failed += 1
            if not continue_on_error:
                status = "aborted"
                break

    if failed > 0 and status == "ok":
        status = "partial"

    return {
        "results": results,
        "status": status,
        "completed": completed,
        "failed": failed,
        "ordering": [ids[i] for i in order],
        "errors": errors,
    }


# ============================================================ pkb_ingest_url


@register_tool(
    name="pkb_external_servers",
    description=(
        "List external MCP servers cerid-kb has discovered + the tools "
        "each is exposing. **Use when** debugging which third-party "
        "MCPs are connected (e.g. Playwright, GitHub, Linear) and how "
        "many of their tools are reachable through the `ext_*` "
        "namespace. **Returns** `{servers: [{name, transport, status, "
        "enabled, tool_count, tools, error?}], total, connected_count}`."
    ),
    input_schema={"type": "object", "properties": {}},
    output_schema={
        "type": "object",
        "properties": {
            "servers": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "transport": {"type": "string"},
                        "status": {"type": "string"},
                        "enabled": {"type": "boolean"},
                        "tool_count": {"type": "integer"},
                        "tools": {"type": "array", "items": {"type": "string"}},
                        "error": {"type": ["string", "null"]},
                    },
                },
            },
            "total": {"type": "integer"},
            "connected_count": {"type": "integer"},
        },
    },
    cost_class="low",
)
async def pkb_external_servers() -> dict[str, Any]:
    """Inspect the MCPClientManager registry. Pure read; never mutates."""
    try:
        from utils.mcp_client import mcp_client_manager
        servers = mcp_client_manager.list_servers()
    except Exception as exc:
        from core.utils.swallowed import log_swallowed_error
        log_swallowed_error('app.mcp_tools.batch', exc)
        # No external manager configured — return empty list, not 503,
        # because the lack of any external server is a valid steady state.
        servers = []
    connected = sum(1 for s in servers if s.get("status") == "connected")
    return {
        "servers": servers,
        "total": len(servers),
        "connected_count": connected,
    }


@register_tool(
    name="pkb_ingest_url",
    description=(
        "Fetch a URL via HTTP and ingest the response body as a new "
        "artifact. Uses plain HTTP fetch (no JavaScript rendering). "
        "**Use when** the user wants a single web page captured into "
        "the KB. For JS-rendered pages, an operator can wire a browser "
        "MCP server (e.g. Playwright or Chrome-DevTools — both "
        "available as global Claude Code plugins) and route via "
        "`ext_*` tools. **Returns** `{status, artifact_id, chunks, "
        "domain, url, bytes_fetched}`. Refuses non-HTTP(S) schemes "
        "and responses >5MB."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "domain": {
                "type": "string",
                "description": "Target KB domain. Empty = use AI auto-categorization.",
                "default": "",
            },
            "tags": {
                "type": "string",
                "description": "Comma-separated tags for the artifact",
                "default": "",
            },
            "max_bytes": {
                "type": "integer",
                "description": "Per-response size cap (default 1MB; max 5MB)",
                "default": 1_000_000,
            },
        },
        "required": ["url"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "status": {"type": "string"},
            "artifact_id": {"type": "string"},
            "chunks": {"type": "integer"},
            "domain": {"type": "string"},
            "url": {"type": "string"},
            "bytes_fetched": {"type": "integer"},
        },
    },
    cost_class="medium",
)
async def pkb_ingest_url(
    url: str,
    domain: str = "",
    tags: str = "",
    max_bytes: int = 1_000_000,
) -> dict[str, Any]:
    if not url.startswith(("http://", "https://")):
        raise InvalidParamsError("url must be http:// or https://")
    max_bytes = max(1, min(int(max_bytes), 5_000_000))

    import httpx

    from app.services.ingestion import ingest_content
    from core.ingest.sources.safe_fetch import guarded_get

    try:
        # SSRF guard: pkb_ingest_url fetches a caller-supplied URL, so resolve
        # + reject internal/private targets and re-validate every redirect hop.
        # A blocked target raises ValueError → surfaced as InvalidParamsError.
        resp = await guarded_get(
            url,
            user_agent="Mozilla/5.0 (compatible; cerid-kb/pkb_ingest_url)",
            timeout=30.0,
            headers={"Accept": "text/html,application/xhtml+xml,text/plain,*/*;q=0.5"},
        )
        resp.raise_for_status()
        body = resp.content[:max_bytes]
    except (httpx.HTTPError, ValueError) as exc:
        raise InvalidParamsError(f"fetch failed: {exc}") from exc

    # Best-effort HTML → text. The parsers module already handles this
    # for ingest_file, but for URL we get just the bytes — strip tags
    # cheaply here and let the ingest pipeline chunk.
    text: str
    content_type = resp.headers.get("content-type", "").lower()
    if "html" in content_type:
        # Simple tag strip — for a production-grade extraction the
        # caller can chain via pkb_batch with an html-cleanup tool.
        text = re.sub(r"<script[^>]*>.*?</script>", "", body.decode("utf-8", errors="replace"), flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
    else:
        text = body.decode("utf-8", errors="replace")

    if not text.strip():
        raise InvalidParamsError("fetched body produced no extractable text")

    # Ingest with URL captured as source metadata
    metadata = {
        "source_url": url,
        "source_type": "web",
        "tags": tags,
    }
    result = await asyncio.to_thread(
        ingest_content,
        text,
        domain or "general",
        metadata=metadata,
    )
    result["url"] = url
    result["bytes_fetched"] = len(body)
    return result
