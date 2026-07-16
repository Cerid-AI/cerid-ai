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

import asyncio
import json
import logging
import os
import time
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from http import HTTPStatus
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import config
from app.routers.models import DEFAULT_ASSIGNMENTS, _current_assignments
from app.services.private_mode import strip_injected_context
from core.utils.swallowed import log_swallowed_error


# --- Response models (generated: single-return dict-literal routes) ---
class CompressContextResponse(BaseModel):
    compressed_messages: Any
    original_tokens: Any
    compressed_tokens: Any



logger = logging.getLogger("ai-companion.chat")

OPENROUTER_BASE = "https://openrouter.ai/api/v1"

# Resolve OPENROUTER_API_KEY at CALL TIME, not module-import time. The setup
# wizard (`/setup/configure`) writes new keys to .env and patches
# ``os.environ`` at runtime; a module-level capture would freeze the stale
# compose-level value and cause chat to 401 while ``/providers/credits`` (which
# reads ``os.getenv`` per-request) happily reports the new key as valid.
def _env_openrouter_key() -> str:
    return os.getenv("OPENROUTER_API_KEY", "")

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
# Defined in config (Slice 2.2 — model ids live in the registry, not here).
CHAT_FALLBACK_POOL = config.CHAT_FALLBACK_POOL

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

# Human-readable error messages for specific upstream status codes
UPSTREAM_ERROR_MESSAGES: dict[int, str] = {
    401: "Invalid API key. Check your OpenRouter key in settings.",
    402: "OpenRouter credits exhausted. Add credits at https://openrouter.ai/settings/credits",
    403: "Access denied by upstream provider. The selected model may require additional permissions.",
}

# ---------------------------------------------------------------------------
# Route-decision + per-model latency telemetry (Phase 0.4a)
# ---------------------------------------------------------------------------
#
# Chat route decisions (explicit model vs smart-routed "auto" vs a
# cross-family fallback retry) were previously invisible — only
# llm_cost_usd was recorded. These are best-effort daily counters +
# capped per-model latency samples so an integrator can reconcile
# core.routing.smart_router.TIER_P95_MS against observed wall-clock
# latency instead of the hand-maintained constants drifting silently.

_CHAT_METRICS_TTL_S = 35 * 24 * 60 * 60  # ~35 days
_ROUTE_SOURCES = ("explicit", "auto", "fallback")
_MODEL_LATENCY_KEY_PREFIX = "cerid:metrics:chat:model_latency:"
_MODEL_LATENCY_MODELS_KEY = "cerid:metrics:chat:model_latency:models"
_MODEL_LATENCY_MAX_ENTRIES = 100
# A p95 index needs at least two samples to differ from the max.
_MIN_SAMPLES_FOR_P95 = 2


def _chat_route_key(day: str, source: str) -> str:
    return f"cerid:metrics:chat:route:{day}:{source}"


def _model_latency_key(model: str) -> str:
    return f"{_MODEL_LATENCY_KEY_PREFIX}{model}"


def _record_chat_route_decision(route_source: str) -> None:
    """Best-effort daily counter for chat route-decision telemetry.

    ``route_source`` is one of "explicit" (client sent a concrete model),
    "auto" (smart-routed), or "fallback" (cross-family retry fired).
    """
    try:
        from app.deps import get_redis
        redis_client = get_redis()
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        key = _chat_route_key(day, route_source)
        pipe = redis_client.pipeline()
        pipe.incr(key)
        pipe.expire(key, _CHAT_METRICS_TTL_S)
        pipe.execute()
    except Exception as exc:  # noqa: BLE001 — telemetry must never break chat
        log_swallowed_error("app.routers.chat.record_route_decision", exc)


def _record_chat_model_latency(model: str, duration_s: float) -> None:
    """Best-effort rolling per-model latency sample (capped list).

    Lets an integrator reconcile ``TIER_P95_MS`` in
    ``core.routing.smart_router`` against observed wall-clock latency.
    """
    try:
        from app.deps import get_redis
        redis_client = get_redis()
        key = _model_latency_key(model)
        pipe = redis_client.pipeline()
        pipe.lpush(key, repr(duration_s))
        pipe.ltrim(key, 0, _MODEL_LATENCY_MAX_ENTRIES - 1)
        pipe.expire(key, _CHAT_METRICS_TTL_S)
        pipe.sadd(_MODEL_LATENCY_MODELS_KEY, model)
        pipe.execute()
    except Exception as exc:  # noqa: BLE001 — telemetry must never break chat
        log_swallowed_error("app.routers.chat.record_model_latency", exc)


def get_chat_route_counts_today(redis_client: Any) -> dict[str, int]:
    """Today's chat route-decision counts, keyed by source.

    Read by ``app.processor.router`` to surface on ``/processor/status``.
    """
    counts: dict[str, int] = dict.fromkeys(_ROUTE_SOURCES, 0)
    try:
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        for source in _ROUTE_SOURCES:
            raw = redis_client.get(_chat_route_key(day, source))
            if raw is not None:
                counts[source] = int(raw)
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error("app.routers.chat.get_route_counts", exc)
    return counts


def get_chat_model_latency_stats(redis_client: Any) -> dict[str, dict[str, float | int]]:
    """Per-model ``{count, p50_s, p95_s}`` from the rolling latency samples.

    Read by ``app.processor.router`` to surface on ``/processor/status``.
    """
    stats: dict[str, dict[str, float | int]] = {}
    try:
        raw_models = redis_client.smembers(_MODEL_LATENCY_MODELS_KEY)
        for raw_model in raw_models:
            model = raw_model.decode() if isinstance(raw_model, bytes) else str(raw_model)
            raw_durations = redis_client.lrange(_model_latency_key(model), 0, -1)
            durations: list[float] = []
            for raw in raw_durations:
                try:
                    durations.append(float(raw))
                except (TypeError, ValueError) as exc:  # silent-catch-allowed: malformed duration-row float — skip, keep the rest
                    log_swallowed_error("app.routers.chat.get_model_latency_stats", exc)
                    continue
            if not durations:
                continue
            durations.sort()
            n = len(durations)
            stats[model] = {
                "count": n,
                "p50_s": round(durations[n // 2], 3),
                "p95_s": round(
                    durations[int(n * 0.95)]
                    if n >= _MIN_SAMPLES_FOR_P95
                    else durations[-1],
                    3,
                ),
            }
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error("app.routers.chat.get_model_latency_stats", exc)
    return stats


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


class ContextCompressRequest(BaseModel):
    messages: list[_ChatMessage]
    target_tokens: int = 4000


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
            from app.db.neo4j.users import get_user_by_id
            from app.deps import get_neo4j
            from utils.encryption import decrypt_field
            user = get_user_by_id(get_neo4j(), user_id)
            if user and user.get("openrouter_api_key_encrypted"):
                decrypted = decrypt_field(user["openrouter_api_key_encrypted"])
                if decrypted:
                    return decrypted
        except Exception as exc:
            log_swallowed_error("app.routers.chat.resolve_per_user_api_key", exc)
    return _env_openrouter_key()


async def _success_gen(
    request: Request,
    upstream: httpx.Response,
    bare_model: str,
    *,
    start_monotonic: float | None = None,
) -> AsyncGenerator[bytes, None]:
    """Cancellation-safe streaming generator for an in-flight OpenRouter response.

    Polls ``request.is_disconnected()`` between yields so a client abort breaks
    us out of the loop; on ``CancelledError``/``GeneratorExit`` the ``finally``
    block closes ``upstream`` so the upstream TCP socket is released instead of
    being left spinning until OpenRouter finishes.

    ``start_monotonic`` — when supplied by ``_attempt_stream`` — is the
    wall-clock start of the upstream call, used to record observed
    per-model latency for TIER_P95_MS reconciliation (Phase 0.4a).
    Defaults to "now" for callers (mainly tests) that construct this
    generator directly without going through ``_attempt_stream``.
    """
    actual_model_emitted = False
    usage_data: dict | None = None
    gen_start_monotonic = start_monotonic if start_monotonic is not None else time.monotonic()
    try:
        async for chunk in upstream.aiter_bytes():
            # Client went away — stop pulling from upstream.
            if await request.is_disconnected():
                logger.info(
                    "chat stream client disconnected (model=%s) — aborting upstream",
                    bare_model,
                )
                break

            # Decode once per chunk. OpenRouter emits newline-delimited SSE
            # events per chunk in practice, so splitting on "\n" gives us
            # complete event payloads.
            try:
                text_chunk = chunk.decode(errors="replace")
            except UnicodeDecodeError:
                text_chunk = ""

            # Emit cerid_meta_update once, before forwarding the first data chunk,
            # so it sits as a self-contained SSE event with \n\n framing.
            if not actual_model_emitted and text_chunk:
                for line in text_chunk.split("\n"):
                    stripped = line.strip()
                    if stripped.startswith("data: ") and stripped != "data: [DONE]":
                        try:
                            parsed = json.loads(stripped[6:])
                        except json.JSONDecodeError:
                            continue
                        actual = parsed.get("model")
                        if actual and actual != bare_model:
                            update = json.dumps(
                                {"cerid_meta_update": {"actual_model": actual}}
                            )
                            yield f"data: {update}\n\n".encode()
                        actual_model_emitted = True
                        break

            # Cheap pre-check: only re-parse the chunk when it actually
            # contains a usage block. Avoids O(chunks²) JSON decoding.
            if '"usage"' in text_chunk:
                for line in text_chunk.split("\n"):
                    stripped = line.strip()
                    if stripped.startswith("data: ") and stripped != "data: [DONE]":
                        try:
                            parsed_chunk = json.loads(stripped[6:])
                        except json.JSONDecodeError:
                            continue
                        if isinstance(parsed_chunk, dict) and parsed_chunk.get("usage"):
                            usage_data = parsed_chunk["usage"]

            yield chunk
    except (asyncio.CancelledError, GeneratorExit):
        logger.info(
            "chat stream cancelled (model=%s) — closing upstream", bare_model
        )
        raise
    except (
        httpx.ReadError,
        httpx.RemoteProtocolError,
        httpx.ReadTimeout,
        httpx.StreamClosed,
    ) as exc:
        logger.warning(
            "Stream interrupted for model=%s: %s(%s)",
            bare_model, type(exc).__name__, exc,
        )
        err = json.dumps({
            "error": {
                "message": f"Stream interrupted ({type(exc).__name__})",
                "type": "stream_error",
            }
        })
        yield f"data: {err}\n\ndata: [DONE]\n\n".encode()
    finally:
        try:
            await upstream.aclose()
        except Exception as exc:  # noqa: BLE001 — best-effort close
            log_swallowed_error("app.routers.chat.success_gen.upstream_close", exc)
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
            except Exception as exc:  # noqa: BLE001 — metrics are best-effort
                log_swallowed_error("app.routers.chat.success_gen.llm_cost_record", exc)
        # Record observed wall-clock latency for TIER_P95_MS reconciliation
        # (Phase 0.4a) — best-effort, never raises (see _record_chat_model_latency).
        _record_chat_model_latency(bare_model, time.monotonic() - gen_start_monotonic)


async def _attempt_stream(
    request: Request,
    req: ChatRequest,
    bare_model: str,
    request_id: str,
    api_key: str,
) -> AsyncGenerator[bytes, None] | int:
    """Single streaming attempt against OpenRouter.

    Returns an async generator of SSE bytes on success or a non-retryable
    error, or an ``int`` HTTP status code when the error is retryable.
    """
    effective_key = api_key or _env_openrouter_key()

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
        start_monotonic = time.monotonic()
        response = await client.send(req_obj, stream=True)

        status = response.status_code
        if status != HTTPStatus.OK:
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

        # Success — return a cancellation-safe streaming generator.
        return _success_gen(  # type: ignore[arg-type]
            request, response, bare_model, start_monotonic=start_monotonic
        )

    except (httpx.ConnectError, httpx.ReadTimeout) as exc:
        logger.error("OpenRouter connection/timeout error for model=%s: %s", bare_model, exc)
        return 503


async def _proxy_stream(
    request: Request,
    req: ChatRequest,
    request_id: str,
    api_key: str = "",
) -> AsyncGenerator[bytes, None]:
    """Stream chat completion from OpenRouter with one fallback retry.

    Cancellation-safe: if the caller cancels this generator (e.g. client
    disconnect mid-stream), the inner ``_success_gen`` finally-block closes
    the OpenRouter upstream so we don't leak sockets/tokens.
    """
    try:
        route_source = "explicit"
        # Smart routing: when model is "auto" or smart routing is enabled with no model
        if req.model == "auto" or (
            getattr(config, "SMART_ROUTING_ENABLED", False) and not req.model
        ):
            route_source = "auto"
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
            except Exception as exc:
                log_swallowed_error('app.routers.chat', exc)
                logger.warning("Smart routing failed (%s), using fallback", exc)
                req.model = _current_assignments().get("general", DEFAULT_ASSIGNMENTS["general"])

        bare_model = _strip_prefix(req.model)

        # Emit metadata event so the frontend knows the resolved model
        meta = json.dumps({
            "cerid_meta": {
                "requested_model": req.model,
                "resolved_model": bare_model,
            }
        })
        yield f"data: {meta}\n\n".encode()

        # Early-out if the client is already gone before we fire the upstream call.
        if await request.is_disconnected():
            logger.info("chat stream client disconnected before upstream open")
            return

        # --- First attempt ---
        result = await _attempt_stream(request, req, bare_model, request_id, api_key)

        if isinstance(result, int):
            original_status = result
            fallback = _pick_fallback(bare_model)
            if fallback:
                route_source = "fallback"
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
                result = await _attempt_stream(
                    request, req, fallback, request_id, api_key
                )

        # Route decision is now final — record telemetry (Phase 0.4a).
        _record_chat_route_decision(route_source)

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
    except (asyncio.CancelledError, GeneratorExit):
        logger.info("chat proxy_stream cancelled — propagating")
        raise


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/chat/stream")
async def chat_stream(req: ChatRequest, request: Request):
    """Stream chat completion directly via OpenRouter."""
    # Private Mode L2+ server-side gate: this endpoint forwards the caller's
    # pre-assembled messages verbatim to the provider, so the web client's
    # client-side KB-bypass cannot protect a direct API/SDK caller. Strip any
    # injected KB/memory context here, before smart-routing inspects it or the
    # payload is forwarded (single strip — _attempt_stream runs twice).
    req.messages = strip_injected_context(req.messages)
    api_key = _resolve_api_key(request)

    if not api_key:
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
        _proxy_stream(request, req, request_id, api_key=api_key),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable nginx buffering
        },
    )


@router.post("/chat/compress", response_model=CompressContextResponse)
async def compress_context(req: ContextCompressRequest):
    """Compress conversation history to fit a target token budget."""
    from utils.context_compression import (
        _estimate_messages_tokens,
        compress_history,
    )

    raw_messages = [{"role": m.role, "content": m.content} for m in req.messages]
    original_tokens = _estimate_messages_tokens(raw_messages)

    compressed = await compress_history(raw_messages, req.target_tokens)
    compressed_tokens = _estimate_messages_tokens(compressed)

    return {
        "compressed_messages": compressed,
        "original_tokens": original_tokens,
        "compressed_tokens": compressed_tokens,
    }
