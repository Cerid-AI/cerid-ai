# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Ollama local LLM proxy — chat, model listing, and model pull endpoints.

Proxies requests to a local Ollama server for air-gapped deployments.
All httpx calls use the ``ollama`` circuit breaker for graceful degradation.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from core.utils.circuit_breaker import CircuitOpenError, get_breaker

router = APIRouter(prefix="/ollama", tags=["ollama"])
logger = logging.getLogger("ai-companion.ollama")

_TIMEOUT = 120.0  # Ollama inference can be slow on first load
_CONNECT_TIMEOUT = 10.0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ollama_base_url() -> str:
    """Return the configured Ollama base URL."""
    return os.getenv("OLLAMA_URL", "http://localhost:11434")


def _ollama_enabled() -> bool:
    """Check if Ollama integration is enabled."""
    return os.getenv("OLLAMA_ENABLED", "false").lower() in ("true", "1", "yes")


def _require_enabled() -> None:
    """Raise 503 if Ollama is not enabled."""
    if not _ollama_enabled():
        raise HTTPException(
            status_code=503,
            detail="Ollama integration is disabled. Set OLLAMA_ENABLED=true to enable.",
        )


def _client_kwargs() -> dict[str, Any]:
    """Shared httpx.AsyncClient keyword arguments."""
    return {
        "timeout": httpx.Timeout(_TIMEOUT, connect=_CONNECT_TIMEOUT),
    }


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str = Field(..., description="Ollama model name (e.g. 'llama3.2')")
    messages: list[ChatMessage]
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None


class ChatResponse(BaseModel):
    model: str
    message: ChatMessage
    done: bool = True
    total_duration: int | None = None
    eval_count: int | None = None


class OllamaModel(BaseModel):
    name: str
    size: int | None = None
    digest: str | None = None
    modified_at: str | None = None


class ModelsResponse(BaseModel):
    models: list[OllamaModel]


class PullRequest(BaseModel):
    model: str = Field(..., description="Model name to pull (e.g. 'llama3.2')")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/models", response_model=ModelsResponse)
async def list_ollama_models():
    """List models installed on the local Ollama server."""
    _require_enabled()
    breaker = get_breaker("ollama")
    base_url = _ollama_base_url()

    async def _fetch_models() -> dict:
        async with httpx.AsyncClient(**_client_kwargs()) as client:
            resp = await client.get(f"{base_url}/api/tags")
            resp.raise_for_status()
            return resp.json()

    try:
        data = await breaker.call(_fetch_models)
    except CircuitOpenError:
        raise HTTPException(
            status_code=503,
            detail="Ollama is temporarily unavailable (circuit breaker open). "
            "Is Ollama running? Try: ollama serve",
        )
    except httpx.ConnectError:
        raise HTTPException(
            status_code=503,
            detail=f"Cannot connect to Ollama at {base_url}. "
            "Is Ollama running? Try: ollama serve",
        )
    except httpx.TimeoutException:
        raise HTTPException(
            status_code=504,
            detail=f"Ollama request timed out ({_CONNECT_TIMEOUT}s connect).",
        )
    except Exception as exc:
        logger.error("Ollama model list failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Ollama error: {exc}")

    raw_models = data.get("models", [])
    models = [
        OllamaModel(
            name=m.get("name", ""),
            size=m.get("size"),
            digest=m.get("digest"),
            modified_at=m.get("modified_at"),
        )
        for m in raw_models
    ]
    return ModelsResponse(models=models)


@router.post("/chat", response_model=None)
async def chat_completion(req: ChatRequest):
    """Proxy a chat completion request to local Ollama.

    Supports both streaming (SSE) and non-streaming responses.
    Accepts the same message format as OpenRouter/Bifrost for compatibility.
    """
    _require_enabled()
    breaker = get_breaker("ollama")
    base_url = _ollama_base_url()

    # Translate to Ollama /api/chat format
    ollama_payload: dict[str, Any] = {
        "model": req.model,
        "messages": [{"role": m.role, "content": m.content} for m in req.messages],
        "stream": req.stream,
    }
    if req.temperature is not None:
        ollama_payload["options"] = ollama_payload.get("options", {})
        ollama_payload["options"]["temperature"] = req.temperature
    if req.max_tokens is not None:
        ollama_payload["options"] = ollama_payload.get("options", {})
        ollama_payload["options"]["num_predict"] = req.max_tokens

    if req.stream:
        return await _stream_chat(base_url, ollama_payload, breaker)
    else:
        return await _sync_chat(base_url, ollama_payload, breaker)


async def _sync_chat(
    base_url: str, payload: dict, breaker: Any,
) -> dict:
    """Non-streaming chat: single request/response."""

    async def _do_chat() -> dict:
        async with httpx.AsyncClient(**_client_kwargs()) as client:
            resp = await client.post(f"{base_url}/api/chat", json=payload)
            resp.raise_for_status()
            return resp.json()

    try:
        data = await breaker.call(_do_chat)
    except CircuitOpenError:
        raise HTTPException(
            status_code=503,
            detail="Ollama is temporarily unavailable (circuit breaker open).",
        )
    except httpx.ConnectError:
        raise HTTPException(
            status_code=503,
            detail=f"Cannot connect to Ollama at {base_url}. "
            "Is Ollama running? Try: ollama serve",
        )
    except httpx.TimeoutException:
        raise HTTPException(
            status_code=504,
            detail="Ollama chat request timed out. Model may still be loading.",
        )
    except httpx.HTTPStatusError as exc:
        logger.warning("Ollama chat HTTP error: %s", exc)
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=f"Ollama returned error: {exc.response.text[:500]}",
        )
    except Exception as exc:
        logger.error("Ollama chat failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Ollama error: {exc}")

    return data


async def _stream_chat(
    base_url: str, payload: dict, breaker: Any,
) -> StreamingResponse:
    """Streaming chat: SSE response from Ollama's NDJSON stream."""

    async def _event_generator():
        try:
            async with httpx.AsyncClient(**_client_kwargs()) as client:
                async with client.stream(
                    "POST", f"{base_url}/api/chat", json=payload,
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        # Ollama streams NDJSON — convert to SSE
                        yield f"data: {line}\n\n"
        except httpx.ConnectError:
            error_payload = json.dumps(
                {"error": f"Cannot connect to Ollama at {base_url}"}
            )
            yield f"data: {error_payload}\n\n"
        except httpx.TimeoutException:
            error_payload = json.dumps({"error": "Ollama stream timed out"})
            yield f"data: {error_payload}\n\n"
        except Exception as exc:
            logger.error("Ollama stream error: %s", exc)
            error_payload = json.dumps({"error": f"Ollama stream error: {exc}"})
            yield f"data: {error_payload}\n\n"

    # Check circuit breaker before starting stream
    current_state = breaker.state
    from core.utils.circuit_breaker import CircuitState

    if current_state == CircuitState.OPEN:
        raise HTTPException(
            status_code=503,
            detail="Ollama is temporarily unavailable (circuit breaker open).",
        )

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/recommendations")
async def get_recommendations():
    """Return recommended Ollama models per pipeline stage."""
    _require_enabled()
    from utils.ollama_models import get_recommended_models
    return {"recommendations": get_recommended_models()}


@router.post("/pull")
async def pull_model(req: PullRequest):
    """Pull (download) a model to the local Ollama server.

    Returns a streaming response with download progress.
    """
    _require_enabled()
    base_url = _ollama_base_url()

    async def _progress_generator():
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(600.0, connect=_CONNECT_TIMEOUT),
            ) as client:
                async with client.stream(
                    "POST",
                    f"{base_url}/api/pull",
                    json={"name": req.model, "stream": True},
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        yield f"data: {line}\n\n"
        except httpx.ConnectError:
            error_payload = json.dumps(
                {"error": f"Cannot connect to Ollama at {base_url}"}
            )
            yield f"data: {error_payload}\n\n"
        except Exception as exc:
            logger.error("Ollama pull failed: %s", exc)
            error_payload = json.dumps({"error": f"Ollama pull error: {exc}"})
            yield f"data: {error_payload}\n\n"

    return StreamingResponse(
        _progress_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
