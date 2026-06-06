# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

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
import logging
import os
from typing import Any

import httpx

import config
from core.utils.circuit_breaker import CircuitOpenError, get_breaker

logger = logging.getLogger("ai-companion.internal_llm")

# Shared connection pool for Ollama calls (avoids per-request TCP handshake)
_ollama_client: httpx.AsyncClient | None = None
_ollama_client_lock = asyncio.Lock()


async def _get_ollama_client() -> httpx.AsyncClient:
    global _ollama_client
    if _ollama_client is not None and not _ollama_client.is_closed:
        return _ollama_client
    async with _ollama_client_lock:
        if _ollama_client is None or _ollama_client.is_closed:
            _ollama_client = httpx.AsyncClient(
                timeout=httpx.Timeout(60.0, connect=5.0),
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            )
    return _ollama_client


async def close_ollama_client() -> None:
    global _ollama_client
    if _ollama_client and not _ollama_client.is_closed:
        await _ollama_client.aclose()
        _ollama_client = None


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
    It also drives per-stage provider routing — see
    :func:`_resolve_stage_provider`.
    """
    default_provider = getattr(config, "INTERNAL_LLM_PROVIDER", "openrouter")
    provider = _resolve_stage_provider(stage, default_provider)
    log: logging.Logger | logging.LoggerAdapter = logger
    if stage:
        log = logging.LoggerAdapter(logger, {"llm_stage": stage})
        try:
            import sentry_sdk  # type: ignore[import-not-found]
            sentry_sdk.set_tag("llm_stage", stage)
        except ImportError:
            pass
        log.debug("internal LLM call provider=%s stage=%s", provider, stage)

    if provider in ("ollama", "quenchforge"):
        return await _call_ollama(
            messages,
            provider=provider,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=response_format is not None and response_format.get("type") == "json_object",
        )
    else:
        # Default: direct OpenRouter via unified client
        from core.utils.llm_client import call_llm
        return await call_llm(
            messages,
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
    model = getattr(config, "INTERNAL_LLM_MODEL", "") or config.OLLAMA_DEFAULT_MODEL
    # Breaker key is provider- AND workload-specific. Pre-v0.93.9 both providers
    # shared the "ollama" breaker; v0.93.9 split by provider. The chat path now
    # also gets its OWN "quenchforge-chat" breaker, separate from the
    # "quenchforge-embed" / "quenchforge-rerank" breakers in quenchforge_client:
    # the slow Vega II chat slot returns transient 502 under load, and a shared
    # "quenchforge" breaker let those chat 502s open the circuit for the
    # (healthy, fast) embed/rerank slots too — locking out the whole backend.
    breaker = get_breaker("quenchforge-chat") if provider == "quenchforge" else get_breaker("ollama")

    # Advanced flags (default off). When any is set, additive payload fields
    # are surfaced; the wire stays valid against stock Ollama and Quenchforge.
    options: dict[str, Any] = {
        "temperature": temperature,
        "num_predict": max_tokens,
    }
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

    async def _do_call() -> str:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": options,
        }
        # Both backends accept format: "json" to enforce JSON output
        if json_mode:
            payload["format"] = "json"
        # Prefix-cache keep-alive: ask the backend to keep the model loaded
        # between calls so prompt-prefix reuse (KV cache hits) survives.
        if getattr(config, "ENABLE_PROMPT_PREFIX_CACHE", False):
            payload["keep_alive"] = getattr(
                config, "PROMPT_PREFIX_KEEP_ALIVE", "30m"
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
                if (500 <= status < 600 or status == 429) and attempt + 1 < max_retries:
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
        return result
    except CircuitOpenError as exc:
        logger.warning("%s circuit breaker open — falling back to OpenRouter", label)
        last_exc = exc
    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError) as exc:
        last_exc = exc  # the specific failure was already logged in the retry loop
    # Local backend exhausted — fall back to OpenRouter. Record the degradation
    # so /health.inference_routing.llm reports serving=openrouter / degraded
    # instead of advertising the local provider that just failed.
    inference_health.record_fallback(
        "llm",
        configured=provider,
        served_by="openrouter",
        detail=str(last_exc) if last_exc else "",
    )
    del last_exc  # informational only; the fall-through path doesn't need it

    # Explicitly use a known-valid OpenRouter model for fallback —
    # INTERNAL_LLM_MODEL may hold an Ollama-native name (e.g. "llama3.2:3b")
    # that OpenRouter rejects with 400.  Also forward json_mode so callers
    # like memory extraction that request structured JSON get it on fallback.
    from core.utils.llm_client import call_llm

    fallback_messages = messages
    fallback_response_format = None
    if json_mode:
        fallback_response_format = {"type": "json_object"}
        # OpenAI/OpenRouter reject response_format=json_object unless the word
        # "json" appears somewhere in the prompt (HTTP 400). A local backend
        # (Ollama/Quenchforge) is lenient, so a caller that asked for JSON
        # without literally saying "json" succeeds locally but 400s on the
        # cloud fallback. Guarantee the token defensively so the fallback is
        # actually reachable for every json-mode caller.
        if not any("json" in str(m.get("content", "")).lower() for m in messages):
            fallback_messages = [
                *messages,
                {"role": "system", "content": "Respond with valid JSON only."},
            ]
    return await call_llm(
        fallback_messages,
        model="openai/gpt-4o-mini",
        temperature=temperature,
        max_tokens=max_tokens,
        response_format=fallback_response_format,
    )
