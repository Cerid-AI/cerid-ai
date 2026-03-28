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
import uuid

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse

from tools import MCP_TOOLS, execute_tool

router = APIRouter()
logger = logging.getLogger("ai-companion")

# Session message queues (shared between GET /mcp/sse and POST /mcp/messages)
_sessions: dict[str, asyncio.Queue] = {}
_MAX_SESSIONS = 100


def clear_sessions():
    """Called from main.py lifespan on shutdown."""
    _sessions.clear()
    logger.info("MCP sessions cleared on shutdown")


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
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": MCP_TOOLS}}
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
        except Exception as e:
            logger.error(f"Tool call error {tool_name}: {e}")
            return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32000, "message": str(e)}}
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
    # Evict oldest session if at capacity
    if len(_sessions) >= _MAX_SESSIONS:
        oldest_key = next(iter(_sessions))
        evicted_queue = _sessions.pop(oldest_key, None)
        if evicted_queue is not None:
            try:
                evicted_queue.put_nowait(None)  # Sentinel signals event_stream to stop
            except asyncio.QueueFull:
                logger.warning("MCP SSE queue full, eviction sentinel dropped for session %s", oldest_key)
        logger.warning(f"[MCP] Evicted oldest session {oldest_key} (cap={_MAX_SESSIONS})")
    _sessions[session_id] = queue
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
