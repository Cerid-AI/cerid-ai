# Copyright (c) 2026 Cerid AI. All rights reserved.
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

import config
from errors import ProviderError

logger = logging.getLogger("ai-companion.chat")

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")

# ---------------------------------------------------------------------------
# Shared connection pool — avoids per-request TCP/TLS handshake overhead
# ---------------------------------------------------------------------------
_chat_client: httpx.AsyncClient | None = None


def _get_chat_client() -> httpx.AsyncClient:
    global _chat_client
    if _chat_client is None or _chat_client.is_closed:
        _chat_client = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=10.0),
            limits=httpx.Limits(max_connections=30, max_keepalive_connections=15),
        )
    return _chat_client


async def close_chat_client() -> None:
    global _chat_client
    if _chat_client and not _chat_client.is_closed:
        await _chat_client.aclose()
        _chat_client = None

router = APIRouter(tags=["chat"])

# Models to try when the primary model fails with a retryable error.
CHAT_FALLBACK_POOL = [
    "openai/gpt-4o-mini",
    "google/gemini-2.5-flash",
    "x-ai/grok-4.1-fast",
    "anthropic/claude-sonnet-4.6",
]

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

# Human-readable error messages for specific upstream status codes
UPSTREAM_ERROR_MESSAGES: dict[int, str] = {
    401: "Invalid API key. Check your OpenRouter key in settings.",
    402: "OpenRouter credits exhausted. Add credits at https://openrouter.ai/settings/credits",
    403: "Access denied by upstream provider. The selected model may require additional permissions.",
}


def _model_family(model_id: str) -> str:
    """Extract provider family: 'openai/gpt-4o-mini' -> 'openai'."""
    return model_id.split("/")[0] if "/" in model_id else model_id


def _pick_fallback(failed_model: str) -> str | None:
    """Pick the first fallback model from a different provider family."""
    failed_family = _model_family(failed_model)
    for candidate in CHAT_FALLBACK_POOL:
        if _model_family(candidate) != failed_family:
            return candidate
    return None


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
    top_p: float | None = None
    max_tokens: int | None = None
    stream: bool = True
    cost_sensitivity: str = "medium"  # "low", "medium", "high"



# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strip_prefix(model_id: str) -> str:
    """Strip ``openrouter/`` prefix for direct OpenRouter API calls."""
    return model_id.removeprefix("openrouter/")


def _resolve_api_key(request: Request) -> str:
    """Resolve the OpenRouter API key — per-user key if available, else global."""
    user_id = getattr(request.state, "user_id", None) if hasattr(request, "state") else None
    if user_id:
        try:
            from db.neo4j.users import get_user_by_id
            from deps import get_neo4j
            from utils.encryption import decrypt_field
            user = get_user_by_id(get_neo4j(), user_id)
            if user and user.get("openrouter_api_key_encrypted"):
                decrypted = decrypt_field(user["openrouter_api_key_encrypted"])
                if decrypted:
                    return decrypted
        except (ProviderError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError):
            logger.debug("Failed to resolve per-user API key, falling back to global")
    return OPENROUTER_API_KEY


async def _attempt_stream(
    req: ChatRequest,
    bare_model: str,
    request_id: str,
    api_key: str,
) -> AsyncGenerator[bytes, None] | int:
    """Single streaming attempt against OpenRouter.

    Returns an async generator of SSE bytes on success or a non-retryable
    error, or an ``int`` HTTP status code when the error is retryable.
    """
    effective_key = api_key or OPENROUTER_API_KEY

    payload_dict: dict = {
        "model": bare_model,
        "messages": [{"role": m.role, "content": m.content} for m in req.messages],
        "temperature": req.temperature,
        "stream": True,
    }
    if req.max_tokens is not None:
        payload_dict["max_tokens"] = req.max_tokens
    if req.top_p is not None:
        payload_dict["top_p"] = req.top_p

    headers = {
        "Authorization": f"Bearer {effective_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://cerid.ai",
        "X-Title": "Cerid AI",
    }
    if request_id:
        headers["X-Request-ID"] = request_id

    try:
        client = _get_chat_client()
        req_obj = client.build_request(
            "POST",
            f"{OPENROUTER_BASE}/chat/completions",
            json=payload_dict,
            headers=headers,
        )
        response = await client.send(req_obj, stream=True)

        status = response.status_code
        if status != 200:
            error_body = (await response.aread()).decode(errors="replace")[:500]
            logger.error(
                "OpenRouter error %d for model=%s: %s",
                status, bare_model, error_body,
            )
            await response.aclose()

            if status in RETRYABLE_STATUS_CODES:
                return status

            # Non-retryable — return a generator that emits the error event
            async def _error_gen() -> AsyncGenerator[bytes, None]:
                friendly = UPSTREAM_ERROR_MESSAGES.get(status, f"Upstream error ({status})")
                err = json.dumps({
                    "error": {
                        "code": status,
                        "message": friendly,
                        "type": "upstream_error",
                    }
                })
                yield f"data: {err}\n\ndata: [DONE]\n\n".encode()

            return _error_gen()

        # Success — return a streaming generator
        async def _success_gen() -> AsyncGenerator[bytes, None]:
            try:
                actual_model_emitted = False
                usage_data: dict | None = None
                async for chunk in response.aiter_bytes():
                    if not actual_model_emitted:
                        try:
                            text = chunk.decode(errors="replace")
                            for line in text.split("\n"):
                                stripped = line.strip()
                                if stripped.startswith("data: ") and stripped != "data: [DONE]":
                                    parsed = json.loads(stripped[6:])
                                    actual = parsed.get("model")
                                    if actual and actual != bare_model:
                                        update = json.dumps(
                                            {"cerid_meta_update": {"actual_model": actual}}
                                        )
                                        yield f"data: {update}\n\n".encode()
                                    actual_model_emitted = True
                                    break
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            pass  # Expected during SSE streaming — partial chunks
                    # Capture usage from the final SSE chunk (before [DONE])
                    try:
                        text_chunk = chunk.decode(errors="replace")
                        for line in text_chunk.split("\n"):
                            stripped = line.strip()
                            if stripped.startswith("data: ") and stripped != "data: [DONE]":
                                parsed_chunk = json.loads(stripped[6:])
                                if "usage" in parsed_chunk:
                                    usage_data = parsed_chunk["usage"]
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        pass  # Expected during SSE streaming — partial chunks
                    yield chunk
            except (httpx.ReadError, httpx.RemoteProtocolError, httpx.ReadTimeout, httpx.StreamClosed) as exc:
                logger.warning(
                    "Stream interrupted for model=%s: %s(%s)", bare_model, type(exc).__name__, exc,
                )
                err = json.dumps({
                    "error": {
                        "message": f"Stream interrupted ({type(exc).__name__})",
                        "type": "stream_error",
                    }
                })
                yield f"data: {err}\n\ndata: [DONE]\n\n".encode()
            finally:
                await response.aclose()
                # Record LLM cost from OpenRouter usage data (fire-and-forget)
                if usage_data:
                    try:
                        from utils.metrics import estimate_cost, get_metrics_collector
                        prompt_tokens = usage_data.get("prompt_tokens", 0)
                        completion_tokens = usage_data.get("completion_tokens", 0)
                        if prompt_tokens or completion_tokens:
                            cost = estimate_cost(bare_model, prompt_tokens, completion_tokens)
                            collector = get_metrics_collector()
                            collector.record_metric("llm_cost_usd", cost)
                            logger.debug(
                                "Chat cost: model=%s prompt=%d completion=%d cost=$%.6f",
                                bare_model, prompt_tokens, completion_tokens, cost,
                            )
                    except (ProviderError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
                        logger.debug("Failed to record chat cost metrics: %s", exc)

        return _success_gen()

    except (httpx.ConnectError, httpx.ReadTimeout) as exc:
        logger.error("OpenRouter connection/timeout error for model=%s: %s", bare_model, exc)
        return 503


async def _proxy_stream(req: ChatRequest, request_id: str, api_key: str = "") -> AsyncGenerator[bytes, None]:
    """Stream chat completion from OpenRouter with one fallback retry."""
    # Smart routing: when model is "auto" or smart routing is enabled with no model
    if req.model == "auto" or (
        getattr(config, "SMART_ROUTING_ENABLED", False) and not req.model
    ):
        try:
            from utils.smart_router import TaskType, route

            last_content = req.messages[-1].content if req.messages else ""
            total_chars = sum(len(m.content) for m in req.messages)
            kb_count = sum(
                1 for m in req.messages
                if m.role == "system" and "<document" in m.content
            )
            decision = await route(
                last_content,
                task_type=TaskType.CHAT,
                cost_sensitivity=req.cost_sensitivity,
                total_chars=total_chars,
                kb_injection_count=kb_count,
            )
            req.model = decision.model
            logger.info(
                "Smart-routed to %s (%s, cost_sensitivity=%s)",
                decision.model, decision.reason, req.cost_sensitivity,
            )
        except (ProviderError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
            logger.warning("Smart routing failed (%s), using fallback", exc)
            req.model = "openai/gpt-4o-mini"

    bare_model = _strip_prefix(req.model)

    # Emit metadata event so the frontend knows the resolved model
    meta = json.dumps({
        "cerid_meta": {
            "requested_model": req.model,
            "resolved_model": bare_model,
        }
    })
    yield f"data: {meta}\n\n".encode()

    # --- First attempt ---
    result = await _attempt_stream(req, bare_model, request_id, api_key)

    if isinstance(result, int):
        original_status = result
        fallback = _pick_fallback(bare_model)
        if fallback:
            logger.info(
                "Retrying with fallback model=%s after %d on model=%s",
                fallback, original_status, bare_model,
            )
            update = json.dumps({
                "cerid_meta_update": {
                    "fallback_model": fallback,
                    "original_error": original_status,
                }
            })
            yield f"data: {update}\n\n".encode()

            # --- Fallback attempt ---
            result = await _attempt_stream(req, fallback, request_id, api_key)

    # Final evaluation
    if isinstance(result, int):
        err = json.dumps({
            "error": {
                "message": f"Upstream error ({result}) — all models failed",
                "type": "upstream_error",
            }
        })
        yield f"data: {err}\n\ndata: [DONE]\n\n".encode()
    else:
        async for chunk in result:
            yield chunk


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/chat/stream")
async def chat_stream(req: ChatRequest, request: Request):
    """Stream chat completion directly via OpenRouter."""
    # Private mode: level >= 3 forces Ollama (local inference only)
    client_id = request.headers.get("X-Client-ID", "unknown")
    private_level = 0
    try:
        from utils.private_mode import get_private_mode_level
        private_level = get_private_mode_level(client_id)
    except (ProviderError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.warning("Private mode check failed (defaulting to disabled): %s", e)

    if private_level >= 3:
        ollama_model = config.INTERNAL_LLM_MODEL or config.OLLAMA_DEFAULT_MODEL or "llama3.2:3b"
        logger.info("Private mode level %d: forcing model to Ollama (%s)", private_level, ollama_model)
        req.model = f"ollama/{ollama_model}"

    api_key = _resolve_api_key(request)

    if not api_key and private_level < 3:
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

    extra_headers: dict[str, str] = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",  # disable nginx buffering
    }
    if private_level > 0:
        extra_headers["X-Private-Mode"] = str(private_level)

    return StreamingResponse(
        _proxy_stream(req, request_id, api_key=api_key),
        media_type="text/event-stream",
        headers=extra_headers,
    )


