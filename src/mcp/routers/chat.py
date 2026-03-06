# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Chat streaming proxy — routes directly to OpenRouter, bypassing Bifrost.

Bifrost uses semantic-intent strategy which silently ignores the client's
model selection.  This proxy gives the frontend authoritative control over
which model handles each request while preserving SSE streaming.

The proxy emits a ``cerid_meta`` SSE event before the upstream chunks so
the frontend can confirm the resolved model.
"""
from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncGenerator

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

logger = logging.getLogger("ai-companion.chat")

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")

router = APIRouter(tags=["chat"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class _ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str
    messages: list[_ChatMessage]
    temperature: float = 0.7
    max_tokens: int | None = None
    stream: bool = True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strip_prefix(model_id: str) -> str:
    """Strip ``openrouter/`` prefix for direct OpenRouter API calls."""
    return model_id.removeprefix("openrouter/")


async def _proxy_stream(req: ChatRequest, request_id: str) -> AsyncGenerator[bytes, None]:
    """Stream chat completion from OpenRouter, prepending a metadata event."""
    bare_model = _strip_prefix(req.model)

    # Emit metadata event so the frontend knows the resolved model
    meta = json.dumps({
        "cerid_meta": {
            "requested_model": req.model,
            "resolved_model": bare_model,
        }
    })
    yield f"data: {meta}\n\n".encode()

    payload: dict = {
        "model": bare_model,
        "messages": [{"role": m.role, "content": m.content} for m in req.messages],
        "temperature": req.temperature,
        "stream": True,
    }
    if req.max_tokens is not None:
        payload["max_tokens"] = req.max_tokens

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://cerid.ai",
        "X-Title": "Cerid AI",
    }
    if request_id:
        headers["X-Request-ID"] = request_id

    timeout = httpx.Timeout(120.0, connect=10.0)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                f"{OPENROUTER_BASE}/chat/completions",
                json=payload,
                headers=headers,
            ) as response:
                if response.status_code != 200:
                    error_body = (await response.aread()).decode(errors="replace")[:500]
                    logger.error(
                        "OpenRouter error %d for model=%s: %s",
                        response.status_code, bare_model, error_body,
                    )
                    err = json.dumps({
                        "error": {
                            "message": f"Upstream error ({response.status_code})",
                            "type": "upstream_error",
                        }
                    })
                    yield f"data: {err}\n\ndata: [DONE]\n\n".encode()
                    return

                actual_model_emitted = False
                async for chunk in response.aiter_bytes():
                    # Parse actual model from the first upstream data event.
                    # OpenRouter may substitute a different model than requested.
                    if not actual_model_emitted:
                        try:
                            text = chunk.decode(errors="replace")
                            for line in text.split("\n"):
                                stripped = line.strip()
                                if stripped.startswith("data: ") and stripped != "data: [DONE]":
                                    payload = json.loads(stripped[6:])
                                    actual = payload.get("model")
                                    if actual and actual != bare_model:
                                        update = json.dumps(
                                            {"cerid_meta_update": {"actual_model": actual}}
                                        )
                                        yield f"data: {update}\n\n".encode()
                                    actual_model_emitted = True
                                    break
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            pass
                    yield chunk
    except httpx.ConnectError as exc:
        logger.error("OpenRouter connection error: %s", exc)
        err = json.dumps({
            "error": {"message": "Failed to connect to OpenRouter", "type": "connection_error"}
        })
        yield f"data: {err}\n\ndata: [DONE]\n\n".encode()
    except httpx.ReadTimeout:
        logger.error("OpenRouter read timeout for model=%s", bare_model)
        err = json.dumps({
            "error": {"message": "OpenRouter read timeout", "type": "timeout"}
        })
        yield f"data: {err}\n\ndata: [DONE]\n\n".encode()


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/chat/stream")
async def chat_stream(req: ChatRequest, request: Request):
    """Stream chat completion directly via OpenRouter."""
    if not OPENROUTER_API_KEY:
        return StreamingResponse(
            iter([
                b'data: {"error":{"message":"OPENROUTER_API_KEY not configured","type":"config_error"}}\n\n'
                b"data: [DONE]\n\n"
            ]),
            media_type="text/event-stream",
            status_code=503,
        )

    request_id = request.headers.get("X-Request-ID", "")
    logger.info("Chat proxy: model=%s request_id=%s", req.model, request_id)

    return StreamingResponse(
        _proxy_stream(req, request_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable nginx buffering
        },
    )
