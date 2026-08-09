# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

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
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

import config
from app.routers.models import DEFAULT_ASSIGNMENTS, _current_assignments
from app.services.private_mode import strip_injected_context
from core.utils.swallowed import log_swallowed_error

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
_ROUTE_SOURCES = ("explicit", "auto", "auto_failed", "fallback")
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
    "auto" (smart-routed), "auto_failed" (smart routing raised and degraded to
    the default assignment — E1 CR-050), or "fallback" (cross-family retry fired).
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
    # None (not "medium") so an unset request resolves through the consumer
    # registry + persisted COST_SENSITIVITY setting (E1 CR-026/028) instead of
    # masking them with a hardcoded default.
    cost_sensitivity: str | None = None  # "low" | "medium" | "high" | None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strip_prefix(model_id: str) -> str:
    """Strip ``openrouter/`` prefix for direct OpenRouter API calls."""
    return model_id.removeprefix("openrouter/")


def _looks_local_model_id(bare_model: str) -> bool:
    """True for bare Ollama/quenchforge tags (``llama3.2``, ``qwen2.5:7b``)
    or explicit ``ollama/…`` prefixes — not vendor/OpenRouter ids with a slash."""
    m = (bare_model or "").strip()
    if not m:
        return False
    if m.startswith("ollama/"):
        return True
    # openrouter/openai/…, anthropic/…, x-ai/…, meta-llama/…
    if "/" in m:
        return False
    return True


def _resolve_chat_dispatch(bare_model: str, openrouter_key: str) -> tuple[str, str, str, str]:
    """Resolve ``(base_url, api_key, send_model, wire)`` for a chat completion.

    A BYOK direct provider explicitly enabled for this model's native provider
    takes precedence over the OpenRouter key, so a BYOK-only user's chat dispatches
    to the direct API + native model id instead of 401ing against OpenRouter (E1
    CR-008 — the confirmed failure scenario). ``wire`` names the adapter the
    streaming path must use (``"openai"`` | ``"anthropic"`` | ``"gemini"``).

    Local Ollama/quenchforge: when the active internal provider is local (or the
    model id is a bare local tag under a local-enabled install), dispatch to the
    daemon's OpenAI-compatible ``/v1/chat/completions`` so GUI chat works without
    OpenRouter (Tier A air-gap path). Otherwise: OpenRouter base + key.
    """
    from core.routing.provider_state import (
        byok_target,
        is_local_provider,
        local_backend_url,
        ollama_enabled,
    )

    target = byok_target(bare_model)
    if target is not None:
        return target.base_url, target.api_key, target.model, target.wire

    send_model = bare_model.removeprefix("ollama/")
    local_ready = is_local_provider() or ollama_enabled()
    if local_ready and _looks_local_model_id(bare_model):
        base = local_backend_url().rstrip("/")
        # Ollama + quenchforge expose OpenAI-compatible completions under /v1.
        return f"{base}/v1", "local", send_model, "openai"  # pragma: allowlist secret — Bearer sentinel, not a real key

    return OPENROUTER_BASE, openrouter_key, bare_model, "openai"


async def _resolve_api_key(request: Request) -> str:
    """Resolve the OpenRouter API key — per-user key if available, else global."""
    user_id = getattr(request.state, "user_id", None) if hasattr(request, "state") else None
    if user_id:
        try:
            from app.db.neo4j.users import get_user_by_id
            from app.deps import get_neo4j
            from utils.encryption import decrypt_field

            def _lookup() -> str | None:
                user = get_user_by_id(get_neo4j(), user_id)
                if user and user.get("openrouter_api_key_encrypted"):
                    return decrypt_field(user["openrouter_api_key_encrypted"])
                return None

            # The sync Neo4j driver call + field decrypt would otherwise block
            # the event loop on every /chat/stream turn in multi-user mode (CR-059).
            decrypted = await asyncio.to_thread(_lookup)
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


async def _anthropic_stream_translate(
    request: Request,
    upstream: httpx.Response,
    bare_model: str,
    *,
    start_monotonic: float | None = None,
) -> AsyncGenerator[bytes, None]:
    """Translate an Anthropic Messages SSE stream into the OpenAI-shaped SSE the
    frontend consumes (E1 CR-008, 3e-2b).

    Anthropic streams typed events (``message_start`` / ``content_block_delta`` /
    ``message_delta`` / ``message_stop``); each ``text_delta`` is forwarded as an
    OpenAI ``choices[].delta.content`` chunk and the stream is closed with a
    ``[DONE]`` sentinel. Cancellation-safe and best-effort on usage/latency,
    mirroring :func:`_success_gen` so the two streaming paths behave identically to
    the client and to the metrics layer.
    """
    gen_start_monotonic = start_monotonic if start_monotonic is not None else time.monotonic()
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    done_emitted = False
    try:
        async for line in upstream.aiter_lines():
            # Client went away — stop pulling from upstream.
            if await request.is_disconnected():
                logger.info(
                    "chat stream client disconnected (anthropic, model=%s) — aborting upstream",
                    bare_model,
                )
                break

            stripped = line.strip()
            if not stripped.startswith("data:"):
                continue
            data_str = stripped[len("data:"):].strip()
            if not data_str:
                continue
            try:
                event = json.loads(data_str)
            except json.JSONDecodeError:
                continue

            etype = event.get("type")
            if etype == "content_block_delta":
                delta = event.get("delta", {})
                if delta.get("type") == "text_delta":
                    text = delta.get("text", "")
                    if text:
                        chunk = json.dumps(
                            {"choices": [{"index": 0, "delta": {"content": text}}]}
                        )
                        yield f"data: {chunk}\n\n".encode()
            elif etype == "message_start":
                usage["prompt_tokens"] = (
                    event.get("message", {}).get("usage", {}).get("input_tokens", 0)
                )
            elif etype == "message_delta":
                out_tokens = event.get("usage", {}).get("output_tokens")
                if out_tokens:
                    usage["completion_tokens"] = out_tokens
            elif etype == "message_stop":
                yield b"data: [DONE]\n\n"
                done_emitted = True
                break
        if not done_emitted:
            # Stream ended (or the client left) without a message_stop — the
            # frontend still needs the terminating sentinel to finalize.
            yield b"data: [DONE]\n\n"
            done_emitted = True
    except (asyncio.CancelledError, GeneratorExit):
        logger.info(
            "chat stream cancelled (anthropic, model=%s) — closing upstream", bare_model
        )
        raise
    except (
        httpx.ReadError,
        httpx.RemoteProtocolError,
        httpx.ReadTimeout,
        httpx.StreamClosed,
    ) as exc:
        logger.warning(
            "Anthropic stream interrupted for model=%s: %s(%s)",
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
            log_swallowed_error("app.routers.chat.anthropic_stream.upstream_close", exc)
        if usage["prompt_tokens"] or usage["completion_tokens"]:
            try:
                from utils.metrics import estimate_cost, get_metrics_collector
                cost = estimate_cost(
                    bare_model, usage["prompt_tokens"], usage["completion_tokens"]
                )
                get_metrics_collector().record_metric("llm_cost_usd", cost)
            except Exception as exc:  # noqa: BLE001 — metrics are best-effort
                log_swallowed_error("app.routers.chat.anthropic_stream.llm_cost_record", exc)
        _record_chat_model_latency(bare_model, time.monotonic() - gen_start_monotonic)


async def _gemini_stream_translate(
    request: Request,
    upstream: httpx.Response,
    bare_model: str,
    *,
    start_monotonic: float | None = None,
) -> AsyncGenerator[bytes, None]:
    """Translate a Gemini ``streamGenerateContent`` SSE stream into the OpenAI-shaped
    SSE the frontend consumes (E1 CR-008, 3e-2c).

    Gemini classic emits one ``data:`` line per chunk (a partial
    GenerateContentResponse) and ends the stream WITHOUT a ``[DONE]`` sentinel of
    its own; each ``candidates[].content.parts[].text`` is forwarded as an OpenAI
    ``choices[].delta.content`` chunk and the stream is closed with ``[DONE]``.
    Cancellation-safe and best-effort on usage/latency, mirroring
    :func:`_success_gen` and :func:`_anthropic_stream_translate`.
    """
    gen_start_monotonic = start_monotonic if start_monotonic is not None else time.monotonic()
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    try:
        async for line in upstream.aiter_lines():
            # Client went away — stop pulling from upstream.
            if await request.is_disconnected():
                logger.info(
                    "chat stream client disconnected (gemini, model=%s) — aborting upstream",
                    bare_model,
                )
                break

            stripped = line.strip()
            if not stripped.startswith("data:"):
                continue
            data_str = stripped[len("data:"):].strip()
            if not data_str:
                continue
            try:
                event = json.loads(data_str)
            except json.JSONDecodeError:
                continue

            candidates = event.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                text = "".join(p.get("text", "") for p in parts)
                if text:
                    chunk = json.dumps(
                        {"choices": [{"index": 0, "delta": {"content": text}}]}
                    )
                    yield f"data: {chunk}\n\n".encode()
            meta = event.get("usageMetadata")
            if meta:
                usage["prompt_tokens"] = meta.get("promptTokenCount", usage["prompt_tokens"])
                usage["completion_tokens"] = meta.get(
                    "candidatesTokenCount", usage["completion_tokens"]
                )
        # Gemini sends no terminating sentinel — the frontend needs one to finalize.
        yield b"data: [DONE]\n\n"
    except (asyncio.CancelledError, GeneratorExit):
        logger.info(
            "chat stream cancelled (gemini, model=%s) — closing upstream", bare_model
        )
        raise
    except (
        httpx.ReadError,
        httpx.RemoteProtocolError,
        httpx.ReadTimeout,
        httpx.StreamClosed,
    ) as exc:
        logger.warning(
            "Gemini stream interrupted for model=%s: %s(%s)",
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
            log_swallowed_error("app.routers.chat.gemini_stream.upstream_close", exc)
        if usage["prompt_tokens"] or usage["completion_tokens"]:
            try:
                from utils.metrics import estimate_cost, get_metrics_collector
                cost = estimate_cost(
                    bare_model, usage["prompt_tokens"], usage["completion_tokens"]
                )
                get_metrics_collector().record_metric("llm_cost_usd", cost)
            except Exception as exc:  # noqa: BLE001 — metrics are best-effort
                log_swallowed_error("app.routers.chat.gemini_stream.llm_cost_record", exc)
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
    # E1 CR-008: prefer an explicitly-enabled BYOK direct provider for this model
    # over the OpenRouter key/base — the confirmed failure scenario is a BYOK-only
    # user's chat 401ing against OpenRouter. Anthropic uses a distinct request +
    # SSE shape (translated back to OpenAI SSE on success); openai/xai + OpenRouter
    # share the OpenAI wire. No BYOK → OpenRouter path unchanged.
    base_url, dispatch_key, send_model, wire = _resolve_chat_dispatch(bare_model, effective_key)

    client = _get_chat_client()
    if wire == "anthropic":
        from core.routing.provider_state import ANTHROPIC_VERSION

        system_parts = [m.content for m in req.messages if m.role == "system" and m.content]
        convo = [
            {"role": m.role, "content": m.content}
            for m in req.messages
            if m.role != "system"
        ]
        anthropic_payload: dict = {
            "model": send_model,
            "messages": convo,
            # Anthropic REQUIRES max_tokens; the chat request may omit it.
            "max_tokens": req.max_tokens or 4096,
            "temperature": req.temperature,
            "stream": True,
        }
        if system_parts:
            anthropic_payload["system"] = "\n\n".join(system_parts)
        if req.top_p is not None:
            anthropic_payload["top_p"] = req.top_p
        headers = {
            "x-api-key": dispatch_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        if request_id:
            headers["X-Request-ID"] = request_id
        req_obj = client.build_request(
            "POST", f"{base_url}/messages", json=anthropic_payload, headers=headers,
        )
    elif wire == "gemini":
        system_parts = [m.content for m in req.messages if m.role == "system" and m.content]
        contents = [
            {
                "role": "model" if m.role == "assistant" else "user",
                "parts": [{"text": m.content}],
            }
            for m in req.messages
            if m.role != "system"
        ]
        gen_config: dict = {
            "maxOutputTokens": req.max_tokens or 4096,
            "temperature": req.temperature,
        }
        if req.top_p is not None:
            gen_config["topP"] = req.top_p
        gemini_payload: dict = {"contents": contents, "generationConfig": gen_config}
        if system_parts:
            gemini_payload["systemInstruction"] = {
                "parts": [{"text": "\n\n".join(system_parts)}]
            }
        headers = {
            "x-goog-api-key": dispatch_key,
            "content-type": "application/json",
        }
        if request_id:
            headers["X-Request-ID"] = request_id
        req_obj = client.build_request(
            "POST",
            f"{base_url}/models/{send_model}:streamGenerateContent?alt=sse",
            json=gemini_payload,
            headers=headers,
        )
    else:
        payload_dict: dict = {
            "model": send_model,
            "messages": [{"role": m.role, "content": m.content} for m in req.messages],
            "temperature": req.temperature,
            "stream": True,
        }
        if req.max_tokens is not None:
            payload_dict["max_tokens"] = req.max_tokens
        if req.top_p is not None:
            payload_dict["top_p"] = req.top_p
        headers = {
            "Authorization": f"Bearer {dispatch_key}",
            "Content-Type": "application/json",
        }
        # OpenRouter attribution headers only on cloud — local Ollama/quenchforge
        # ignore them but some reverse proxies reject unknown headers.
        if "openrouter.ai" in base_url:
            headers["HTTP-Referer"] = "https://cerid.ai"
            headers["X-Title"] = "Cerid AI"
        if request_id:
            headers["X-Request-ID"] = request_id
        req_obj = client.build_request(
            "POST", f"{base_url}/chat/completions", json=payload_dict, headers=headers,
        )

    try:
        start_monotonic = time.monotonic()
        response = await client.send(req_obj, stream=True)

        status = response.status_code
        if status != HTTPStatus.OK:
            error_body = (await response.aread()).decode(errors="replace")[:500]
            logger.error(
                "Upstream error %d for model=%s (wire=%s): %s",
                status, bare_model, wire, error_body,
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

        # Success — return a cancellation-safe streaming generator (translated to
        # OpenAI-shaped SSE for the Anthropic wire).
        if wire == "anthropic":
            return _anthropic_stream_translate(  # type: ignore[arg-type]
                request, response, bare_model, start_monotonic=start_monotonic
            )
        if wire == "gemini":
            return _gemini_stream_translate(  # type: ignore[arg-type]
                request, response, bare_model, start_monotonic=start_monotonic
            )
        return _success_gen(  # type: ignore[arg-type]
            request, response, bare_model, start_monotonic=start_monotonic
        )

    except (httpx.ConnectError, httpx.ReadTimeout) as exc:
        logger.error("Upstream connection/timeout error for model=%s: %s", bare_model, exc)
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
                # E1 CR-051: count injected DOCUMENTS, not system messages. The FE
                # joins every <document> block into ONE system message (see
                # web/src/lib/kb-utils.ts::formatChunkWithHeader), so the old
                # per-message count was 0 or 1 and the router's kb_injection_count
                # >= 3 SIMPLE->MODERATE tilt was unreachable in every real request.
                kb_count = sum(
                    m.content.count("<document")
                    for m in req.messages
                    if m.role == "system"
                )
                # E1 CR-026/028: resolve cost-sensitivity through the request →
                # consumer-registry → persisted-setting chain so the GUI's stored
                # cost preference actually steers the smart router, even when the
                # client sends no per-request value.
                from app.services.request_policy import resolve_cost_sensitivity
                cost_sensitivity = resolve_cost_sensitivity(
                    req.cost_sensitivity, request.headers.get("x-client-id", "gui")
                )
                decision = await route(
                    last_content,
                    task_type=TaskType.CHAT,
                    cost_sensitivity=cost_sensitivity,
                    total_chars=total_chars,
                    kb_injection_count=kb_count,
                )
                # Local cascade (ollama/quenchforge): dispatch the bare model on
                # the local OpenAI-compatible stream when the local daemon is the
                # active provider or OLLAMA_ENABLED. Otherwise fall back to the
                # general cloud assignment so we never 400 OpenRouter with a bare
                # Ollama id when local is offline (CR-053 residual).
                from core.routing.provider_state import is_local_provider, ollama_enabled

                if decision.provider in ("ollama", "quenchforge"):
                    # is_local_provider("ollama") is a type check (always true for
                    # the name) — only the active provider / OLLAMA_ENABLED gate
                    # means the daemon is actually on.
                    local_ok = is_local_provider() or ollama_enabled()
                    if local_ok:
                        req.model = decision.model
                        logger.info(
                            "Smart-routed to local %s (%s) via %s stream",
                            decision.model, decision.reason, decision.provider,
                        )
                    else:
                        req.model = _current_assignments().get(
                            "general", DEFAULT_ASSIGNMENTS["general"]
                        )
                        logger.info(
                            "Smart router chose local %s but local daemon not "
                            "enabled — using cloud assignment %s (CR-053)",
                            decision.model, req.model,
                        )
                else:
                    req.model = decision.model
                    logger.info(
                        "Smart-routed to %s (%s, cost_sensitivity=%s)",
                        decision.model, decision.reason, cost_sensitivity,
                    )
            except Exception as exc:
                # E1 CR-050: mark the route source so the degradation to the
                # default assignment is observable in the route telemetry — a
                # failed auto-route otherwise recorded as a plain "auto", making a
                # silently-broken smart router indistinguishable from a working one.
                route_source = "auto_failed"
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


async def _collect_nonstream_response(
    gen: AsyncGenerator[bytes, None],
) -> tuple[dict[str, Any], int]:
    """Buffer an SSE proxy stream into a single OpenAI-shaped ``chat.completion``.

    Honors ``ChatRequest.stream=False`` (CR-064): a non-streaming client gets one
    JSON object, not ``text/event-stream``. Upstream ``aiter_bytes()`` splits SSE
    frames at arbitrary byte boundaries, so accumulate and split on the ``\\n\\n``
    frame delimiter rather than parsing each yielded chunk in isolation. Returns
    ``(body, status_code)``.
    """
    content_parts: list[str] = []
    resolved_model = ""
    error: dict[str, Any] | None = None
    buffer = ""

    def _consume_frame(frame: str) -> None:
        nonlocal resolved_model, error
        for line in frame.splitlines():
            if not line.startswith("data: "):
                continue
            payload = line[len("data: "):].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                obj = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if "cerid_meta" in obj:
                resolved_model = obj["cerid_meta"].get("resolved_model", resolved_model)
            elif "cerid_meta_update" in obj:
                continue
            elif "error" in obj:
                error = obj["error"]
            else:
                for choice in obj.get("choices", []):
                    piece = choice.get("delta", {}).get("content")
                    if piece:
                        content_parts.append(piece)

    async for raw in gen:
        buffer += raw.decode("utf-8", "replace")
        while "\n\n" in buffer:
            frame, buffer = buffer.split("\n\n", 1)
            _consume_frame(frame)
    if buffer:
        _consume_frame(buffer)

    if error is not None:
        return {"error": error}, int(HTTPStatus.BAD_GATEWAY)

    body: dict[str, Any] = {
        "object": "chat.completion",
        "model": resolved_model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "".join(content_parts)},
                "finish_reason": "stop",
            }
        ],
    }
    return body, int(HTTPStatus.OK)


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/chat/stream")
async def chat_stream(req: ChatRequest, request: Request):
    """Stream a chat completion via OpenRouter, BYOK direct, or local
    Ollama/quenchforge — or, when the client sets ``stream=false``, return a
    single JSON ``chat.completion`` instead of SSE (CR-064)."""
    # Private Mode L2+ server-side gate: this endpoint forwards the caller's
    # pre-assembled messages verbatim to the provider, so the web client's
    # client-side KB-bypass cannot protect a direct API/SDK caller. Strip any
    # injected KB/memory context here, before smart-routing inspects it or the
    # payload is forwarded (single strip — _attempt_stream runs twice).
    req.messages = strip_injected_context(req.messages)
    api_key = await _resolve_api_key(request)

    if not api_key:
        from core.routing.provider_state import is_local_provider, ollama_enabled

        # Local-only installs: no OpenRouter key required when the internal
        # provider is ollama/quenchforge (or OLLAMA_ENABLED). Sentinel key is
        # replaced by _resolve_chat_dispatch for local OpenAI-compat calls.
        if is_local_provider() or ollama_enabled():
            api_key = "local"  # pragma: allowlist secret — non-secret sentinel for local OpenAI-compat Bearer
            # Prefer a bare local model if the client still has a cloud id.
            if not _looks_local_model_id(_strip_prefix(req.model)):
                from core.routing.provider_state import active_provider
                local_default = os.getenv("INTERNAL_LLM_MODEL", "").strip() or "llama3.2"
                logger.info(
                    "Local chat: rewriting model %s → %s (provider=%s)",
                    req.model, local_default, active_provider(),
                )
                req.model = local_default
        else:
            err = {
                "error": {
                    "message": (
                        "OPENROUTER_API_KEY not configured. Set a key, or enable a "
                        "local provider (INTERNAL_LLM_PROVIDER=ollama|quenchforge)."
                    ),
                    "type": "config_error",
                }
            }
            if not req.stream:
                return JSONResponse(err, status_code=503)
            return StreamingResponse(
                iter([
                    f"data: {json.dumps(err)}\n\ndata: [DONE]\n\n".encode()
                ]),
                media_type="text/event-stream",
                status_code=503,
            )

    request_id = request.headers.get("X-Request-ID", "")
    logger.info("Chat proxy: model=%s request_id=%s stream=%s", req.model, request_id, req.stream)

    if not req.stream:
        body, status = await _collect_nonstream_response(
            _proxy_stream(request, req, request_id, api_key=api_key)
        )
        return JSONResponse(body, status_code=status)

    return StreamingResponse(
        _proxy_stream(request, req, request_id, api_key=api_key),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable nginx buffering
        },
    )
