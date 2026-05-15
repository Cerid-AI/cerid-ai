# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""MCP SSE transport — thin protocol layer.

Tool schemas and execute_tool() dispatcher live in tools.py.
This module handles only SSE streaming, session management, and JSON-RPC framing.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse

from app.tool_registry import ToolError
from app.tools import execute_tool, get_all_tools

router = APIRouter()
logger = logging.getLogger("ai-companion")

# Session bookkeeping. Each entry is the asyncio.Queue used to push
# JSON-RPC responses back over the SSE stream. ``_session_last_seen``
# tracks the wall-clock time of the most recent activity on that
# session (initial open or last received POST) so the reaper can
# evict idle ones in preference to live ones.
_sessions: dict[str, asyncio.Queue] = {}
_session_last_seen: dict[str, float] = {}
_MAX_SESSIONS = 100
_IDLE_TIMEOUT_S = 5 * 60  # 5 minutes — Claude Code reconnects faster than this


def _touch_session(session_id: str) -> None:
    """Mark a session as just-active so the reaper preserves it."""
    if session_id:
        _session_last_seen[session_id] = time.monotonic()


def clear_sessions():
    """Called from main.py lifespan on shutdown."""
    _sessions.clear()
    _session_last_seen.clear()
    logger.info("MCP sessions cleared on shutdown")


async def _session_reaper() -> None:
    """Periodically evict sessions idle longer than ``_IDLE_TIMEOUT_S``.

    Wakes every 60 seconds. A session is idle if no POST has touched
    it in the last 5 minutes. The SSE pings (every 24s) don't count
    because they don't traverse ``_touch_session`` — they're server-
    initiated and don't reset the activity clock. That's correct: a
    dead client that's holding a TCP connection open but not making
    any RPC requests should still be reaped.

    Started from the FastAPI lifespan; cancelled on shutdown. Never
    raises out so a stray exception can't kill the background task.
    """
    while True:
        try:
            await asyncio.sleep(60)
            now = time.monotonic()
            stale = [
                sid for sid, last in _session_last_seen.items()
                if now - last > _IDLE_TIMEOUT_S
            ]
            for sid in stale:
                q = _sessions.pop(sid, None)
                _session_last_seen.pop(sid, None)
                if q is not None:
                    try:
                        q.put_nowait(None)  # sentinel — closes the SSE generator
                    except asyncio.QueueFull:
                        pass
                logger.info(
                    "MCP session reaper: evicted idle session %s (idle=%ds)",
                    sid, int(now - _session_last_seen.get(sid, now)),
                )
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.warning("MCP session reaper error (continuing): %s", exc)


# Map ToolError subclasses → JSON-RPC error envelopes. Used by the
# tools/call handler so callers can distinguish "you sent bad args"
# (-32602) from "the artifact doesn't exist" (-32004) from "ChromaDB
# is unreachable" (-32005). The default -32000 stays as a fallback
# for unexpected exceptions.
def _error_envelope_for(exc: Exception) -> dict:
    if isinstance(exc, ToolError):
        return {"code": exc.json_rpc_code, "message": str(exc)}
    return {"code": -32000, "message": str(exc)}


# ── JSON-RPC dispatcher ──────────────────────────────────────────────────────

async def build_response(msg_id, method: str, params: dict) -> dict:
    if method == "initialize":
        client_version = params.get("protocolVersion", "2024-11-05")
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": client_version,
                "capabilities": {"tools": {"listChanged": True}},
                "serverInfo": {"name": "cerid-ai-companion", "version": "1.0.0"},
            },
        }
    elif method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": get_all_tools()}}
    elif method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})
        try:
            result = await execute_tool(tool_name, tool_args)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]},
            }
        except ToolError as e:
            # Typed errors map to specific JSON-RPC codes so clients
            # can distinguish validation (-32602) from upstream-down
            # (-32005) from not-found (-32004) etc. See
            # app.tool_registry for the catalog.
            logger.warning(
                "Tool call typed error: name=%s class=%s code=%d msg=%s",
                tool_name, type(e).__name__, e.json_rpc_code, e,
            )
            return {"jsonrpc": "2.0", "id": msg_id, "error": _error_envelope_for(e)}
        except Exception as e:
            logger.error(f"Tool call error {tool_name}: {e}")
            return {"jsonrpc": "2.0", "id": msg_id, "error": _error_envelope_for(e)}
    elif method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    else:
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32601, "message": f"Unknown: {method}"},
        }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.head("/mcp/sse")
async def mcp_sse_head():
    return Response(status_code=200, headers={"Content-Type": "text/event-stream"})


@router.get("/mcp/sse")
async def mcp_sse_endpoint(request: Request):
    """SSE endpoint — responses to POSTs come through here."""
    session_id = str(uuid.uuid4())
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    # Evict oldest-IDLE session if at capacity. Prior versions used
    # ``next(iter(_sessions))`` which evicts oldest-opened — a long-
    # lived but active client got booted in favor of dead sessions
    # accumulating from crashed/restarted Claude Code instances.
    # Sorting by ``_session_last_seen`` picks the dead ones first.
    if len(_sessions) >= _MAX_SESSIONS:
        candidates = sorted(
            _sessions.keys(),
            key=lambda sid: _session_last_seen.get(sid, 0.0),
        )
        if candidates:
            oldest_key = candidates[0]
            evicted_queue = _sessions.pop(oldest_key, None)
            _session_last_seen.pop(oldest_key, None)
            if evicted_queue is not None:
                try:
                    evicted_queue.put_nowait(None)  # Sentinel — closes event_stream
                except asyncio.QueueFull:
                    pass
            logger.warning(f"[MCP] Evicted oldest-idle session {oldest_key} (cap={_MAX_SESSIONS})")
    _sessions[session_id] = queue
    _touch_session(session_id)
    logger.info(f"[MCP] SSE opened: {session_id}")

    async def event_stream():
        try:
            mcp_host = os.getenv("MCP_EXTERNAL_HOST", "ai-companion-mcp:8888")
            endpoint_url = f"http://{mcp_host}/mcp/messages?sessionId={session_id}"
            yield f"event: endpoint\ndata: {endpoint_url}\n\n"
            logger.info(f"[MCP] Sent endpoint: {endpoint_url}")
            count = 0
            while True:
                if await request.is_disconnected():
                    break
                if count % 3 == 0:
                    ping = {
                        "jsonrpc": "2.0",
                        "method": "ping",
                        "params": {},
                        "id": f"server-ping-{count}",
                    }
                    await queue.put(ping)
                    logger.debug(f"[MCP] Sent keep-alive ping: {session_id}")
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=8.0)
                    if msg is None:
                        logger.info(f"[MCP] Session evicted, closing SSE: {session_id}")
                        break
                    data = json.dumps(msg)
                    yield f"event: message\ndata: {data}\n\n"
                    logger.info(f"[MCP] Sent via SSE: {msg.get('id', 'notification')}")
                except TimeoutError:
                    yield ": keepalive\n\n"
                count += 1
        finally:
            _sessions.pop(session_id, None)
            _session_last_seen.pop(session_id, None)
            logger.info(f"[MCP] SSE closed: {session_id}")

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Accept, Cache-Control, Content-Type",
            "Transfer-Encoding": "chunked",
        },
    )


@router.post("/mcp/sse")
async def mcp_sse_post(request: Request):
    """Handle probes to /mcp/sse."""
    return Response(status_code=200, content="", media_type="text/plain")


@router.post("/mcp/messages")
async def mcp_messages(request: Request):
    """Receive JSON-RPC, send response via SSE stream."""
    session_id = request.query_params.get("sessionId")
    try:
        body = await request.body()
        body_text = body.decode("utf-8").strip()
        if not body_text or body_text == "{}":
            return Response(status_code=202)
        msg = json.loads(body_text)
    except Exception as e:
        logger.error(f"[MCP] Parse error: {e}")
        return Response(status_code=400, content=str(e))

    method = msg.get("method", "")
    params = msg.get("params", {})
    msg_id = msg.get("id")
    _touch_session(session_id)  # reset idle clock on any inbound POST
    logger.info(f"[MCP] Received: {method} (id={msg_id}, session={session_id})")

    if method in ("initialized", "notifications/initialized"):
        logger.info("[MCP] Client initialized")
        return Response(status_code=202)

    response = await build_response(msg_id, method, params)

    if session_id and session_id in _sessions:
        await _sessions[session_id].put(response)
        logger.info(f"[MCP] Queued response for SSE: {method}")
        return Response(status_code=202)
    else:
        logger.warning(f"[MCP] No session, returning directly: {method}")
        return Response(
            status_code=200,
            content=json.dumps(response),
            media_type="application/json",
        )
