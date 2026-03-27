# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""WebSocket sync endpoint for real-time collaborative memory.

Provides a ``/ws/sync`` route that handles CRDT delta exchange,
presence updates, and broadcast to connected clients.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger("ai-companion.ws_sync")

router = APIRouter()

# In-memory connected clients: ws -> {"user_id": str, "queue": asyncio.Queue}
_connections: dict[WebSocket, dict[str, Any]] = {}


async def _authenticate(ws: WebSocket) -> dict | None:
    """Validate JWT token from query param. Returns payload or None."""
    token = ws.query_params.get("token")
    if not token:
        return None
    try:
        from app.middleware.jwt_auth import decode_access_token

        return decode_access_token(token)
    except Exception:
        return None


async def _send_loop(ws: WebSocket, queue: asyncio.Queue) -> None:
    """Drain the outbound queue and send messages to the client."""
    try:
        while True:
            msg = await queue.get()
            await ws.send_text(json.dumps(msg))
    except (WebSocketDisconnect, RuntimeError):
        pass


async def _broadcast(msg: dict, *, exclude: WebSocket | None = None) -> None:
    """Enqueue *msg* to all connected clients except *exclude*."""
    for ws, info in _connections.items():
        if ws is exclude:
            continue
        try:
            info["queue"].put_nowait(msg)
        except asyncio.QueueFull:
            logger.warning("Dropping message for slow client %s", info.get("user_id"))


@router.websocket("/ws/sync")
async def ws_sync(ws: WebSocket) -> None:
    """WebSocket sync endpoint.

    JSON protocol message types: ``delta``, ``presence``, ``ack``, ``error``.
    """
    from config.settings import WS_MAX_CONNECTIONS, WS_SYNC_ENABLED

    # Gate: reject if sync is disabled
    if not WS_SYNC_ENABLED:
        await ws.close(code=4001, reason="WebSocket sync is disabled")
        return

    # Authenticate via query param token
    payload = await _authenticate(ws)
    if payload is None:
        await ws.accept()
        await ws.close(code=4003, reason="Authentication failed")
        return

    user_id: str = payload.get("sub", "anonymous")

    # Enforce max connections
    if len(_connections) >= WS_MAX_CONNECTIONS:
        await ws.accept()
        await ws.close(code=4002, reason="Max connections reached")
        return

    await ws.accept()

    queue: asyncio.Queue = asyncio.Queue(maxsize=256)
    _connections[ws] = {"user_id": user_id, "queue": queue}

    # Start outbound send loop
    send_task = asyncio.create_task(_send_loop(ws, queue))

    # Lazy-init presence manager
    presence = None

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                queue.put_nowait({"type": "error", "payload": {"message": "Invalid JSON"}})
                continue

            msg_type = msg.get("type")
            msg_payload = msg.get("payload", {})

            if msg_type == "delta":
                # Broadcast delta to all other clients
                await _broadcast(
                    {"type": "delta", "payload": msg_payload},
                    exclude=ws,
                )
                # Acknowledge receipt
                queue.put_nowait({"type": "ack", "payload": {"status": "ok"}})

            elif msg_type == "presence":
                if presence is None:
                    from app.sync.presence import PresenceManager

                    presence = PresenceManager()
                presence.update(user_id, msg_payload)
                # Broadcast presence to all clients (including sender for consistency)
                all_users = presence.get_all()
                await _broadcast(
                    {"type": "presence", "payload": {"type": "update", "users": all_users}},
                )

            else:
                queue.put_nowait({
                    "type": "error",
                    "payload": {"message": f"Unknown message type: {msg_type}"},
                })

    except WebSocketDisconnect:
        logger.info("Client %s disconnected", user_id)
    except Exception:
        logger.exception("WebSocket error for client %s", user_id)
    finally:
        send_task.cancel()
        _connections.pop(ws, None)
        # Clean up presence
        if presence is not None:
            try:
                presence.remove(user_id)
                all_users = presence.get_all()
                await _broadcast(
                    {"type": "presence", "payload": {"type": "update", "users": all_users}},
                )
            except Exception:
                logger.exception("Failed to clean up presence for %s", user_id)
