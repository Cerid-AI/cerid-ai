# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Internal LLM call utility — routes to OpenRouter, Ollama, or Quenchforge based on INTERNAL_LLM_PROVIDER.

Used by pipeline operations that need lightweight LLM intelligence:
- Query decomposition
- Claim extraction
- Contextual chunk summaries
- AI categorization (smart tier)
- Memory conflict resolution

Quenchforge speaks the Ollama HTTP protocol identically — provider="quenchforge"
reuses the Ollama wire format but reads QUENCHFORGE_URL instead of OLLAMA_URL.

NOT used for user-facing chat (that goes through /chat/stream → OpenRouter).
NOT used for verification (that uses dedicated VERIFICATION_MODEL).
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
import os
import threading
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from http import HTTPStatus
from typing import Any

import httpx

import config
from core.utils.circuit_breaker import CircuitOpenError, get_breaker
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.internal_llm")

# Contextvar-scoped (provider, model) override for call_internal_llm. Set by
# app.processor.worker via llm_call_override() to route a single hybrid-mode
# job to the API tier without touching per-stage routing config. Concurrent
# worker tasks each get their own contextvar copy, so an override set in one
# task's context never leaks into another concurrently-running job.
_llm_override: ContextVar[tuple[str, str] | None] = ContextVar(
    "_llm_override", default=None
)


@contextmanager
def llm_call_override(provider: str, model: str) -> Iterator[None]:
    """Scope a (provider, model) override for every ``call_internal_llm`` call inside the block."""
    token = _llm_override.set((provider, model))
    try:
        yield
    finally:
        _llm_override.reset(token)


# Shared connection pool for Ollama calls (avoids per-request TCP handshake).
# The client's transport binds to the event loop that first uses it, and
# ``is_closed`` stays False when that loop dies — so the singleton must be
# keyed to its owning loop. Ingestion helpers (``contextual._run_coro_isolated``)
# run coroutines on short-lived per-call loops that close on completion;
# reusing a client bound to such a loop raised
# ``RuntimeError: Event loop is closed`` (swallowed under
# ``ingestion.ai_categorize``; 2026-07-12 beta triage). Mirrors the
# per-loop pattern in ``core.utils.llm_client._get_client``. The guard is
# a ``threading.Lock`` (not ``asyncio.Lock``) because asyncio primitives
# themselves bind to a loop on first use — the same cross-loop hazard.
_ollama_client: httpx.AsyncClient | None = None
_ollama_client_loop: asyncio.AbstractEventLoop | None = None
_ollama_client_guard = threading.Lock()


async def _get_ollama_client() -> httpx.AsyncClient:
    global _ollama_client, _ollama_client_loop
    loop = asyncio.get_running_loop()
    with _ollama_client_guard:
        if (
            _ollama_client is None
            or _ollama_client.is_closed
            or _ollama_client_loop is not loop
        ):
            # A stale client bound to another loop cannot be aclose()'d
            # from here; drop the reference and let GC reap its sockets
            # (same trade-off as llm_client's per-loop replacement).
            _ollama_client = httpx.AsyncClient(
                timeout=httpx.Timeout(_LOCAL_READ_TIMEOUT_S, connect=5.0),
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            )
            _ollama_client_loop = loop
        return _ollama_client


async def close_ollama_client() -> None:
    global _ollama_client, _ollama_client_loop
    if _ollama_client and not _ollama_client.is_closed:
        await _ollama_client.aclose()
    _ollama_client = None
    _ollama_client_loop = None


# ── Local-backend pacing (bf-f3 default) ───────────────────────────────────
# The local chat slot serves 1-2 sequences; every extra concurrent request
# queues server-side until the client's timeout fires, and each timed-out
# call used to retry into the same saturated backend (18 "Quenchforge
# timeout (attempt 1/3)" lines in ~50 min of community summarisation —
# qf-pacing). Two levers, both client-side:
#
#   1. A bounded concurrency cap (``INTERNAL_LLM_MAX_CONCURRENCY``, default
#      2) on non-streaming local calls, so background enrichment queues in
#      the client instead of timing out in the server. The user-facing
#      streaming path (``_stream_ollama``) is deliberately NOT capped —
#      background work yields to interactive use.
#   2. A shared cooldown armed on timeout: after a local call times out,
#      subsequent local calls wait out the cooldown before issuing
#      (doubling per consecutive timeout up to a max; reset on success) —
#      backoff-on-timeout, never immediate retry pile-on.
#
# The semaphore is per-event-loop (asyncio primitives bind to a loop on
# first use — same hazard as the shared httpx client above); the cooldown
# clock is process-wide because the backend saturation it models is.
_pacing_sem: asyncio.Semaphore | None = None
_pacing_sem_loop: asyncio.AbstractEventLoop | None = None
_pacing_guard = threading.Lock()
_pacing_cooldown_until: float = 0.0
_pacing_cooldown_seconds: float = 0.0


def _pacing_max_concurrency() -> int:
    return max(1, int(os.environ.get("INTERNAL_LLM_MAX_CONCURRENCY", "2")))


def _get_pacing_semaphore() -> asyncio.Semaphore:
    global _pacing_sem, _pacing_sem_loop
    loop = asyncio.get_running_loop()
    with _pacing_guard:
        if _pacing_sem is None or _pacing_sem_loop is not loop:
            _pacing_sem = asyncio.Semaphore(_pacing_max_concurrency())
            _pacing_sem_loop = loop
        return _pacing_sem


# Read timeout for the local daemon.
#
# This is a LIVENESS bound ("the daemon has stopped responding"), not a latency
# SLO. Per-stage budgets (e.g. core.agents.memory's asyncio.wait_for) are where
# latency is capped, and they only work if they are TIGHTER than this — a stage
# budget above the transport ceiling can never be reached.
#
# It used to be 60s, a cloud-shaped number, and the layering was inverted:
# memory extraction's 90s local budget sat ABOVE it, so a slow generation was
# cut by the transport at 60s, retried twice more (each attempt doing the same
# work against the same ceiling — a deterministic failure, not a transient
# one), and the 90s budget fired mid-retry having produced nothing. Measured
# 2026-08-27 on the CPU-placed chat slot: ~7 tok/s, so a typical extraction
# takes ~44s and the largest internal call in the tree (max_tokens=2000) needs
# ~290s of generation. Nothing above ~410 tokens could ever finish.
#
# `connect` stays short, so a genuinely dead daemon still fails over fast; this
# bounds GENERATION, which is legitimately slow on local hardware.
# app/routers/ollama_proxy.py already uses 600s against the same backend.
_LOCAL_READ_TIMEOUT_S = float(os.environ.get("INTERNAL_LLM_READ_TIMEOUT_S", "300"))


def _record_pacing_timeout() -> None:
    """Arm (or extend) the shared cooldown after a local-backend timeout."""
    global _pacing_cooldown_until, _pacing_cooldown_seconds
    initial = float(os.environ.get("INTERNAL_LLM_TIMEOUT_COOLDOWN", "2.0"))
    maximum = float(os.environ.get("INTERNAL_LLM_TIMEOUT_COOLDOWN_MAX", "30.0"))
    with _pacing_guard:
        _pacing_cooldown_seconds = min(
            maximum, (_pacing_cooldown_seconds * 2) or initial,
        )
        _pacing_cooldown_until = time.monotonic() + _pacing_cooldown_seconds


def _record_pacing_success() -> None:
    global _pacing_cooldown_until, _pacing_cooldown_seconds
    with _pacing_guard:
        _pacing_cooldown_until = 0.0
        _pacing_cooldown_seconds = 0.0


def _reset_pacing_state() -> None:
    """Test hook: drop the semaphore and disarm the cooldown."""
    global _pacing_sem, _pacing_sem_loop
    with _pacing_guard:
        _pacing_sem = None
        _pacing_sem_loop = None
    _record_pacing_success()


async def _wait_pacing_cooldown() -> None:
    with _pacing_guard:
        remaining = _pacing_cooldown_until - time.monotonic()
    if remaining > 0:
        logger.info(
            "local LLM cooldown after timeout — pacing %.2fs before next call",
            remaining,
        )
        await asyncio.sleep(remaining)


def _resolve_stage_provider(stage: str | None, default_provider: str) -> str:
    """Resolve the LLM provider for a specific call site.

    Lookup order (first match wins):
    1. ``PROVIDER_STAGE_<NORMALIZED_STAGE>`` env var. Stage names like
       ``"longmemeval/score"`` normalize to ``LONGMEMEVAL_SCORE``.
    2. ``config.PIPELINE_PROVIDERS[stage]`` for well-known stages
       (``claim_extraction``, ``query_decomposition``, …).
    3. ``default_provider`` (the global ``INTERNAL_LLM_PROVIDER``).

    Lets operators send heavy or latency-sensitive call sites to a
    different provider than the global default — e.g. route
    ``stage=longmemeval/score`` to OpenRouter to escape local-chat-slot
    queueing while keeping privacy-sensitive stages (``memory_resolution``,
    ``claim_extraction``) on the local daemon.
    """
    if not stage:
        return default_provider
    normalized = stage.upper().replace("/", "_").replace("-", "_")
    env_override = os.environ.get(f"PROVIDER_STAGE_{normalized}")
    if env_override:
        return env_override
    pipeline_providers = getattr(config, "PIPELINE_PROVIDERS", {})
    if stage in pipeline_providers:
        return pipeline_providers[stage]
    return default_provider


def _resolve_stage_model(stage: str | None) -> str:
    """Resolve the LLM model id for a specific call site.

    Lookup order (first match wins):
    1. ``PROVIDER_STAGE_<NORMALIZED_STAGE>_MODEL`` env var — operator pin
       (e.g. ``PROVIDER_STAGE_FAITHFULNESS_DECOMPOSE_MODEL=openrouter/google/gemini-2.5-flash``).
    2. ``config.stage_profiles.STAGE_PROFILES[stage]`` → tier → model from
       ``utils.model_registry.ACTIVE_MODELS["tiers"]``. The smart default —
       judges land on a moderate model, summaries on a simple one, frontier
       generation on the user's expert pick.
    3. Empty string. ``call_llm``'s existing fallback chain
       (``INTERNAL_LLM_MODEL`` → ``_DEFAULT_INTERNAL_MODEL``) takes over.

    Stage profile classification lives in :mod:`config.stage_profiles`;
    the (hardness → tier → model id) policy lives in the registry. Both
    are user-tunable without touching this resolver.
    """
    if not stage:
        return ""
    try:
        from config.stage_profiles import env_pin_for, tier_for
    except ImportError:
        return ""
    pinned = env_pin_for(stage)
    if pinned:
        return pinned
    tier = tier_for(stage)
    if not tier:
        return ""
    try:
        from utils.model_registry import get_model
    except ImportError:
        return ""
    try:
        return get_model("tiers", tier) or ""
    except Exception:  # noqa: BLE001 — registry must never block a call
        log_swallowed_error("core.utils.internal_llm.resolve_stage_model", Exception(f"registry lookup failed for tier={tier}"))
        return ""


def _build_ollama_options(
    temperature: float, max_tokens: int, json_mode: bool,
) -> dict[str, Any]:
    """Build the Ollama/Quenchforge ``options`` block — additive advanced flags,
    all default-off. Shared by the streaming and non-streaming local paths so
    speculative + constrained decode apply to BOTH; the streaming twin used to
    drop the speculative-decode draft model the non-streaming path applied
    (CR-070). The wire stays valid against stock Ollama and Quenchforge.
    """
    options: dict[str, Any] = {"temperature": temperature, "num_predict": max_tokens}
    if json_mode and getattr(config, "ENABLE_CONSTRAINED_DECODE", False):
        # Constrained decode pairs with json_mode by forcing deterministic
        # output — otherwise the model can still emit valid JSON that varies
        # per sample. Operators wanting freshness override the flag.
        options["temperature"] = 0.0
    if getattr(config, "ENABLE_SPECULATIVE_DECODE", False):
        draft_model = (
            getattr(config, "INTERNAL_LLM_DRAFT_MODEL", "")
            or os.getenv("INTERNAL_LLM_DRAFT_MODEL", "")
        )
        if draft_model:
            options["draft_model"] = draft_model
    return options


def _build_chat_payload(
    model: str,
    messages: list[dict[str, str]],
    options: dict[str, Any],
    *,
    stream: bool,
    json_mode: bool,
) -> dict[str, Any]:
    """Build the Ollama/Quenchforge ``/api/chat`` request body.

    Shared by the non-streaming collector (:func:`_call_ollama`) and the
    streaming generator (:func:`_stream_ollama`) so the wire shape is identical
    across both — only ``stream`` differs. Centralizing the payload keeps the
    two paths from drifting (json format, prefix-cache keep-alive).
    """
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": stream,
        "options": options,
    }
    # Both backends accept format: "json" to enforce JSON output.
    if json_mode:
        payload["format"] = "json"
    # Prefix-cache keep-alive: ask the backend to keep the model loaded between
    # calls so prompt-prefix reuse (KV cache hits) survives.
    if getattr(config, "ENABLE_PROMPT_PREFIX_CACHE", False):
        payload["keep_alive"] = getattr(config, "PROMPT_PREFIX_KEEP_ALIVE", "30m")
    return payload


def _ensure_json_prompt_token(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    """OpenAI/OpenRouter reject ``response_format=json_object`` with HTTP 400
    unless the literal token "json" appears in the prompt (E1 CR-103). Local
    backends are lenient, so a caller that asks for JSON without saying "json"
    succeeds locally and 400s on the cloud path. Append a system nudge when the
    token is absent so json-mode is reachable on every OpenRouter path — both the
    direct branch and the local->cloud fallback."""
    if any("json" in str(m.get("content", "")).lower() for m in messages):
        return messages
    return [*messages, {"role": "system", "content": "Respond with valid JSON only."}]


async def call_internal_llm(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.1,
    max_tokens: int = 500,
    response_format: dict | None = None,
    stage: str | None = None,
) -> str:
    """Route internal LLM call to configured provider.

    Returns the assistant message content as a string.
    Providers: "ollama" (local), "quenchforge" (local Mac+AMD), or
    "openrouter" (default cloud aggregator).

    The *stage* argument is a first-class observability breadcrumb: every
    internal-LLM call is attributed to a named pipeline stage (e.g.
    ``"topic_extraction"``, ``"claim_extraction"``, ``"contextual_summary"``).
    It also drives:
      - per-stage provider routing (:func:`_resolve_stage_provider`)
      - per-stage model selection (:func:`_resolve_stage_model`), which
        maps stages → (task_type, hardness) → tier → model id via
        :mod:`config.stage_profiles`. Caller doesn't pick a model; the
        registry does, and the operator can override per stage via env
        (``PROVIDER_STAGE_<NAME>_MODEL``) or per tier via the registry.
    """
    default_provider = getattr(config, "INTERNAL_LLM_PROVIDER", "openrouter")
    provider = _resolve_stage_provider(stage, default_provider)
    resolved_model = _resolve_stage_model(stage)
    override = _llm_override.get()
    if override is not None:
        provider, resolved_model = override
    log: logging.Logger | logging.LoggerAdapter = logger
    if stage:
        log = logging.LoggerAdapter(logger, {"llm_stage": stage})
        try:
            import sentry_sdk  # type: ignore[import-not-found]
            sentry_sdk.set_tag("llm_stage", stage)
            if resolved_model:
                sentry_sdk.set_tag("llm_stage_model", resolved_model)
        except ImportError:
            pass
        log.debug(
            "internal LLM call provider=%s stage=%s model=%s",
            provider, stage, resolved_model or "<caller-default>",
        )

    json_mode = response_format is not None and response_format.get("type") == "json_object"
    if provider in ("ollama", "quenchforge"):
        return await _call_ollama(
            messages,
            provider=provider,
            model=resolved_model,
            stage=stage,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
        )
    else:
        # Default: direct OpenRouter via unified client. CR-103: apply the same
        # json-token guard the local->cloud fallback uses, so a json-mode caller
        # whose stage resolves DIRECTLY to openrouter doesn't 400.
        from core.utils.llm_client import call_llm
        return await call_llm(
            _ensure_json_prompt_token(messages) if json_mode else messages,
            model=resolved_model,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
        )


async def _call_ollama(
    messages: list[dict[str, str]],
    *,
    temperature: float,
    max_tokens: int,
    json_mode: bool = False,
    provider: str = "ollama",
    model: str = "",
    stage: str | None = None,
) -> str:
    """Call a local Ollama-protocol backend (stock Ollama or Quenchforge).

    The wire format is identical across both backends; ``provider`` only
    selects the URL (``OLLAMA_URL`` vs ``QUENCHFORGE_URL``) and the
    user-facing label in fallback log lines.
    """
    import httpx

    if provider == "quenchforge":
        base_url = getattr(config, "QUENCHFORGE_URL", "") or os.getenv(
            "OLLAMA_URL", "http://localhost:11434"
        )
        label = "Quenchforge"
        start_hint = "is the quenchforge daemon running?"
    else:
        base_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
        label = "Ollama"
        start_hint = "is 'ollama serve' running?"
    # E1 CR-038: honor a stage-resolved / override model when it names a LOCAL
    # model (a bare id like "llama3.1-8b"). A tier id ("openrouter/...", carries a
    # "/") can't be served by the local daemon, so use the local default there.
    local_model = model if (model and "/" not in model) else (
        getattr(config, "INTERNAL_LLM_MODEL", "") or config.OLLAMA_DEFAULT_MODEL
    )
    # Breaker key is provider- AND workload-specific. Pre-v0.93.9 both providers
    # shared the "ollama" breaker; v0.93.9 split by provider. The chat path now
    # also gets its OWN "quenchforge-chat" breaker, separate from the
    # "quenchforge-embed" / "quenchforge-rerank" breakers in quenchforge_client:
    # the slow Vega II chat slot returns transient 502 under load, and a shared
    # "quenchforge" breaker let those chat 502s open the circuit for the
    # (healthy, fast) embed/rerank slots too — locking out the whole backend.
    breaker = get_breaker("quenchforge-chat") if provider == "quenchforge" else get_breaker("ollama")

    options = _build_ollama_options(temperature, max_tokens, json_mode)

    async def _do_call() -> str:
        payload = _build_chat_payload(
            local_model, messages, options, stream=False, json_mode=json_mode,
        )
        client = await _get_ollama_client()
        resp = await client.post(
            f"{base_url}/api/chat",
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("message", {}).get("content", "")

    # Retry transient back-pressure INSIDE the breaker call so one logical
    # request is at most ONE breaker outcome. Pre-fix the retry loop wrapped
    # breaker.call, so a single transiently-loading slot (502 × N retries)
    # counted as N breaker failures and opened a healthy backend's breaker by
    # itself. Mirrors quenchforge_client's embed/rerank pattern (retry inside
    # breaker.call). On AMD-Mac the chat slot can return 502/timeout under
    # sustained load, but a short backoff lets the daemon catch up. Capped at 3
    # attempts so a truly dead daemon still fails over within ~5 s.
    # 5xx, timeouts, and ConnectError are retryable; circuit-open and 4xx are not
    # (4xx is re-raised straight through and the breaker excludes it from the
    # failure count).
    max_retries = int(os.environ.get("INTERNAL_LLM_MAX_RETRIES", "3"))
    backoff_base = float(os.environ.get("INTERNAL_LLM_RETRY_BACKOFF", "0.5"))
    from core.utils import inference_health

    async def _do_call_with_retries() -> str:
        inner_exc: Exception | None = None
        for attempt in range(max_retries):
            try:
                # bf-f3 pacing: honor the shared timeout cooldown, then take a
                # bounded-concurrency slot for the duration of the attempt only
                # (backoff sleeps below never hold a slot).
                await _wait_pacing_cooldown()
                async with _get_pacing_semaphore():
                    return await _do_call()
            except httpx.ConnectError as exc:
                inner_exc = exc
                if attempt + 1 < max_retries:
                    delay = backoff_base * (2 ** attempt)
                    logger.info(
                        "%s connect error (attempt %d/%d) — retry in %.2fs",
                        label, attempt + 1, max_retries, delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                logger.warning(
                    "%s unreachable at %s (%s) — falling back to OpenRouter",
                    label, base_url, start_hint,
                )
                raise
            except httpx.TimeoutException as exc:
                inner_exc = exc
                # Arm the shared cooldown so CONCURRENT callers back off too —
                # per-call retry backoff alone let N in-flight calls each
                # retry into the already-saturated backend (qf-pacing).
                _record_pacing_timeout()
                if attempt + 1 < max_retries:
                    delay = backoff_base * (2 ** attempt)
                    logger.info(
                        "%s timeout (attempt %d/%d) — retry in %.2fs",
                        label, attempt + 1, max_retries, delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                logger.warning(
                    "%s request timed out after %d attempts — falling back to OpenRouter",
                    label, max_retries,
                )
                raise
            except httpx.HTTPStatusError as exc:
                inner_exc = exc
                status = exc.response.status_code
                # Retry server-side (5xx) and rate-limit (429) but not 4xx
                # classes that signal a bad request — backoff won't fix those.
                if (HTTPStatus.INTERNAL_SERVER_ERROR <= status < 600 or status == HTTPStatus.TOO_MANY_REQUESTS) and attempt + 1 < max_retries:
                    delay = backoff_base * (2 ** attempt)
                    logger.info(
                        "%s HTTP %d (attempt %d/%d) — retry in %.2fs",
                        label, status, attempt + 1, max_retries, delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                logger.warning(
                    "%s HTTP %d (after %d attempts) — falling back to OpenRouter",
                    label, status, attempt + 1,
                )
                raise
        # Unreachable: every iteration returns or raises.
        raise inner_exc if inner_exc is not None else RuntimeError(
            "internal_llm retry loop fell through without a result"
        )

    last_exc: Exception | None = None
    try:
        result = await breaker.call(_do_call_with_retries)
        inference_health.record_success("llm", provider=provider)
        _record_pacing_success()
        return result
    except CircuitOpenError as exc:
        logger.warning("%s circuit breaker open — falling back to OpenRouter", label)
        last_exc = exc
    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError) as exc:
        last_exc = exc  # the specific failure was already logged in the retry loop
    # Local backend exhausted. Falling back to OpenRouter re-sends the identical
    # payload (user content) to the cloud — which is exactly what an operator who
    # chose local inference for privacy does not want. Honour the opt-out before
    # egressing.
    if not getattr(config, "ALLOW_CLOUD_EGRESS_WHEN_LOCAL", True):
        logger.error(
            "%s failed and ALLOW_CLOUD_EGRESS_WHEN_LOCAL=false — not falling "
            "back to OpenRouter (stage=%s)", label, stage or "<none>",
        )
        raise RuntimeError(
            f"Local inference provider {provider!r} is unavailable and cloud "
            f"fallback is disabled (ALLOW_CLOUD_EGRESS_WHEN_LOCAL=false)."
        ) from last_exc

    # Record the degradation so /health.inference_routing.llm reports
    # serving=openrouter / degraded instead of advertising the local provider
    # that just failed.
    inference_health.record_fallback(
        "llm",
        configured=provider,
        served_by="openrouter",
        detail=f"stage={stage or '<none>'}: {last_exc}" if last_exc else f"stage={stage or '<none>'}",
    )
    del last_exc  # informational only; the fall-through path doesn't need it

    # Explicitly use a known-valid OpenRouter model for fallback —
    # INTERNAL_LLM_MODEL may hold an Ollama-native name (e.g. "llama3.2:3b")
    # that OpenRouter rejects with 400.  Also forward json_mode so callers
    # like memory extraction that request structured JSON get it on fallback.
    from core.utils.llm_client import call_llm

    fallback_messages = _ensure_json_prompt_token(messages) if json_mode else messages
    fallback_response_format = {"type": "json_object"} if json_mode else None
    # E1 CR-102: a stage-resolved OpenRouter model (tier id, carries a "/") is
    # valid on the cloud fallback — use it instead of always downgrading to the
    # JSON fallback model. A bare/local hint is not a valid OpenRouter id, so the
    # known-good fallback model still covers that case.
    fallback_model = model if (model and "/" in model) else config.INTERNAL_LLM_JSON_FALLBACK_MODEL
    return await call_llm(
        fallback_messages,
        model=fallback_model,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format=fallback_response_format,
    )


async def _stream_ollama(
    messages: list[dict[str, str]],
    *,
    temperature: float,
    max_tokens: int,
    json_mode: bool = False,
    provider: str = "ollama",
    model: str | None = None,
) -> AsyncIterator[str]:
    """Stream assistant-content deltas from a local Ollama-protocol backend.

    Yields each ``message.content`` fragment as the NDJSON stream arrives.
    Errors (connect/timeout/HTTP) propagate to the caller, which decides
    whether to fall back — this generator does not swallow them.

    ``model`` defaults to ``INTERNAL_LLM_MODEL`` / ``OLLAMA_DEFAULT_MODEL``;
    :func:`llm_call_override` may pass an explicit model (E1 residual).
    """
    if provider == "quenchforge":
        base_url = getattr(config, "QUENCHFORGE_URL", "") or os.getenv(
            "OLLAMA_URL", "http://localhost:11434"
        )
    else:
        base_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
    resolved_model = (
        model
        or getattr(config, "INTERNAL_LLM_MODEL", "")
        or config.OLLAMA_DEFAULT_MODEL
    )
    options = _build_ollama_options(temperature, max_tokens, json_mode)
    payload = _build_chat_payload(
        resolved_model, messages, options, stream=True, json_mode=json_mode,
    )

    client = await _get_ollama_client()
    async with client.stream("POST", f"{base_url}/api/chat", json=payload) as resp:
        if resp.status_code >= HTTPStatus.BAD_REQUEST:
            # Read the error body before raising so the exception carries detail
            # (streaming responses raise ResponseNotRead otherwise).
            await resp.aread()
            resp.raise_for_status()
        async for line in resp.aiter_lines():
            if not line.strip():
                continue
            try:
                data = _json.loads(line)
            except ValueError:
                continue
            piece = data.get("message", {}).get("content", "")
            if piece:
                yield piece
            if data.get("done"):
                break


async def call_internal_llm_stream(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.1,
    max_tokens: int = 500,
    response_format: dict | None = None,
    stage: str,
) -> AsyncIterator[str]:
    """Stream an internal-LLM completion as content deltas.

    Companion to :func:`call_internal_llm` for the inline-verification path:
    yields assistant-content fragments as they arrive so a consumer (e.g.
    :func:`core.agents.hallucination.inline_gate.inline_nli_gate`) can gate
    sentences mid-stream instead of verifying post-hoc.

    ``stage`` is required and keyword-only — the streaming path is always a
    named synthesis stage, which structurally satisfies the call-site stage
    contract (:mod:`tests.test_llm_call_site_contract`) without a separate lint.

    Local providers (ollama/quenchforge) stream token deltas over NDJSON. For
    non-local providers, or if local streaming fails before the first token,
    this degrades to a single chunk holding the full non-streaming result — so
    the capability is provider-agnostic without duplicating the OpenRouter
    transport. A local failure *after* partial output stops cleanly rather than
    re-emitting duplicate content.
    """
    default_provider = getattr(config, "INTERNAL_LLM_PROVIDER", "openrouter")
    provider = _resolve_stage_provider(stage, default_provider)
    # E1 CR-111: honor an llm_call_override scoped around this stream — its
    # docstring promises to cover "every call_internal_llm call inside the block",
    # and the streaming entry point is one. Provider selects local vs cloud;
    # model must also apply on the local branch (residual: was ignored there).
    override = _llm_override.get()
    override_model: str | None = None
    if override is not None:
        provider = override[0]
        override_model = override[1] or None
    json_mode = (
        response_format is not None
        and response_format.get("type") == "json_object"
    )

    if provider in ("ollama", "quenchforge"):
        from core.utils import inference_health
        yielded_any = False
        try:
            async for chunk in _stream_ollama(
                messages,
                provider=provider,
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=json_mode,
                model=override_model,
            ):
                yielded_any = True
                yield chunk
            # E1 CR-093: record the streaming success so the breaker + /health see
            # it — the streaming path was previously invisible to inference_health.
            inference_health.record_success("llm", provider=provider)
            return
        except (
            httpx.ConnectError,
            httpx.TimeoutException,
            httpx.HTTPStatusError,
        ) as exc:
            log_swallowed_error("core.utils.internal_llm.stream_fallback", exc)
            if yielded_any:
                # E1 CR-093: partial content already reached the consumer, so
                # re-streaming would duplicate it — we do NOT retry. But the answer
                # is TRUNCATED: record the degradation and RAISE so the caller knows
                # it is incomplete. Pre-fix this returned SILENTLY, and the inline-gate
                # consumer computed claims/citations over the partial text and
                # presented it as verified. gated_synthesis catches this and falls
                # back to a complete non-streaming synthesis.
                inference_health.record_fallback(
                    "llm", configured=provider, served_by="openrouter",
                    detail=str(exc),
                )
                raise
            # No tokens yet → safe to fall back to the non-streaming path below
            # (call_internal_llm records its own success/fallback outcome).

    # Non-local provider, or local streaming failed before first token.
    full = await call_internal_llm(
        messages,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format=response_format,
        stage=stage,
    )
    if full:
        yield full
