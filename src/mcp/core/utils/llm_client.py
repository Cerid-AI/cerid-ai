# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unified LLM client — calls OpenRouter directly.

All internal LLM operations route through this client. Bifrost was retired
2026-04-17 (audit C-4); the remaining fallbacks here raise ``RuntimeError``
when ``OPENROUTER_API_KEY`` is unset or the OpenRouter circuit is open, so
the caller sees an explicit failure rather than a silent re-route.

Usage::

    from core.utils.llm_client import call_llm

    answer = await call_llm(
        [{"role": "user", "content": "Summarize this text..."}],
        model="openrouter/openai/gpt-4o-mini",
        breaker_name="bifrost-verify",
    )
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import uuid
from http import HTTPStatus
from typing import TYPE_CHECKING

import httpx

from core.utils.circuit_breaker import get_breaker
from core.utils.tracing import get_request_id, tracing_headers
from errors import ProviderError

if TYPE_CHECKING:
    from core.routing.smart_router import RouteDecision

_logger = logging.getLogger("ai-companion.llm_client")


def _extract_content(data: dict) -> str:
    """Pull the assistant message out of an OpenAI-compatible response.

    OpenAI-compatible gateways (OpenRouter included) return **HTTP 200** with an
    ``{"error": {...}}`` body for moderation blocks, routing failures and
    upstream provider errors. Chained ``.get()`` defaults turned those into an
    empty string, so the failure travelled silently through every downstream
    stage as "the model returned nothing" — the silent-zero class the 2026-05-17
    ablations already surfaced once.

    Raise instead, so the circuit breaker and retry path can see it.
    """
    err = data.get("error")
    if err:
        msg = err.get("message") if isinstance(err, dict) else str(err)
        code = err.get("code") if isinstance(err, dict) else None
        raise ProviderError(
            f"provider returned an error envelope on HTTP 200"
            f"{f' (code={code})' if code else ''}: {msg}"
        )

    choices = data.get("choices")
    if not choices:
        raise ProviderError(
            "provider response contained neither 'choices' nor 'error' "
            f"(keys={sorted(data)[:8]})"
        )
    return choices[0].get("message", {}).get("content", "") or ""

# Free-tier default when no model is supplied and ``INTERNAL_LLM_MODEL`` is
# unset. Mirrors the smart-router FREE tier (``smart_router.FREE_MODELS``) so
# the implicit fallback can't drift from the routing tables. Kept as a local
# constant (not an import) because this module sits below ``core.routing`` in
# the import graph and loads on the hot path.
_DEFAULT_INTERNAL_MODEL = "meta-llama/llama-3.3-70b-instruct"  # model-literal-allowed: designated central internal-LLM fallback constant

# ---------------------------------------------------------------------------
# Singleton connection pool for OpenRouter
# ---------------------------------------------------------------------------

_client: httpx.AsyncClient | None = None
# Track the event loop the singleton was created on so we can detect
# poisoning by transient loops (e.g. `core.utils.contextual._run_coro_isolated`
# which spins up a throwaway loop in a worker thread for sync ingestion calls).
# If a call arrives on a loop other than the owner, we return a one-shot
# client instead of the singleton — preventing the throwaway loop from
# binding the singleton and leaving it dead when the thread exits.
_client_loop: asyncio.AbstractEventLoop | None = None
_client_lock = asyncio.Lock()

# Consecutive auth-failure counter — tracks 401/403 responses that indicate the
# connection pool was poisoned by startup failures before DNS/auth stabilised.
_consecutive_401s: int = 0
# 5 consecutive 401s required to trigger a pool recycle.  Raised from 3 to
# avoid false-positive recycling during startup when OpenRouter auth/DNS may
# not yet be fully stabilised — a one-time burst of 3 startup failures was
# triggering an unnecessary recycle ~70 seconds into container startup.
_POOL_RECYCLE_401_THRESHOLD: int = 5


def _new_openrouter_client() -> httpx.AsyncClient:
    """Factory for a fresh httpx client configured for OpenRouter."""
    return httpx.AsyncClient(
        base_url="https://openrouter.ai/api/v1",
        timeout=httpx.Timeout(connect=10, read=60, write=10, pool=10),
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
    )


async def _get_client() -> httpx.AsyncClient:
    """Get or create the shared httpx client for direct OpenRouter calls.

    Connection pool is sized for concurrent verification workloads
    (up to 20 concurrent connections, 10 keep-alive). Uses an asyncio.Lock
    to prevent duplicate client creation under concurrent access.

    Loop-safety: only the main thread — where uvicorn owns the persistent
    FastAPI event loop — is allowed to create/read the singleton. Worker
    threads (e.g. the ThreadPoolExecutor inside
    ``core.utils.contextual._run_coro_isolated`` that spins up a throwaway
    loop for sync ingestion calls) always get a one-shot client. Without
    this guard, the throwaway loop would bind the singleton and leave it
    dead when the thread exits, causing every later request on the main
    loop to fail with ``RuntimeError: Event loop is closed``.
    """
    global _client, _client_loop
    current_loop = asyncio.get_running_loop()

    # Workers that use asyncio.run / new_event_loop in a non-main thread
    # MUST NOT touch the singleton — their loop dies with the thread.
    if threading.current_thread() is not threading.main_thread():
        return _new_openrouter_client()

    # Cheap fast-path: singleton still valid AND we're on its owner loop.
    if (
        _client is not None
        and not _client.is_closed
        and _client_loop is current_loop
    ):
        return _client

    # Owner-loop mismatch on the main thread (e.g. pytest changed loops
    # between tests) — recycle, don't return a dead singleton.
    async with _client_lock:
        if _client is None or _client.is_closed or _client_loop is not current_loop:
            _client = _new_openrouter_client()
            _client_loop = current_loop
    return _client


class _OpenRouterClientCtx:
    """Async context manager yielding an httpx client safe for the current loop.

    - Main thread (where uvicorn owns the persistent loop): yields the shared
      singleton and does NOT close it on exit (preserves connection pool).
    - Worker threads (ThreadPoolExecutor with a transient loop, as in
      ``core.utils.contextual._run_coro_isolated``): yields a fresh one-shot
      client and closes it on exit so no file descriptors are leaked when
      the throwaway loop dies.

    Callers should prefer this over calling :func:`_get_client` directly so
    one-shot clients from non-main threads are guaranteed to be closed.
    """

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._is_one_shot = False

    async def __aenter__(self) -> httpx.AsyncClient:
        client = await _get_client()
        # When _get_client returns a fresh (non-singleton) client for a
        # non-main-thread caller, we own it and must close it on exit.
        self._is_one_shot = client is not _client
        self._client = client
        return client

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._is_one_shot and self._client is not None and not self._client.is_closed:
            await self._client.aclose()


def _acquire_client() -> _OpenRouterClientCtx:
    """Return a context manager that yields a loop-safe httpx client.

    Usage::

        async with _acquire_client() as client:
            resp = await client.post(...)
    """
    return _OpenRouterClientCtx()


async def close_client() -> None:
    """Close the shared httpx client.  Call during application shutdown."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
        _client = None


async def _recycle_client() -> None:
    """Close and recreate the singleton httpx client.

    Called after *_POOL_RECYCLE_401_THRESHOLD* consecutive auth failures.
    Guards against a poisoned pool caused by 401s received before DNS/auth
    stabilised at container startup.
    """
    global _client, _consecutive_401s
    async with _client_lock:
        if _client is not None and not _client.is_closed:
            await _client.aclose()
        _client = httpx.AsyncClient(
            base_url="https://openrouter.ai/api/v1",
            timeout=httpx.Timeout(connect=10, read=60, write=10, pool=10),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
        _consecutive_401s = 0
    _logger.info(
        "OpenRouter connection pool recycled after %d consecutive auth failures",
        _POOL_RECYCLE_401_THRESHOLD,
    )


async def recycle_client() -> None:
    """Public entry-point for :func:`_recycle_client`.  Used by setup endpoints."""
    await _recycle_client()


def reset_auth_failure_count() -> None:
    """Reset the consecutive-401 counter.  Call after a confirmed successful auth."""
    global _consecutive_401s
    _consecutive_401s = 0


def get_consecutive_auth_failures() -> int:
    """Return the current consecutive-401 counter for completion calls.

    A value of 0 means completions are succeeding (or haven't been attempted).
    Used by the health endpoint to distinguish a /auth/key probe 401 (which can
    be a rate-limit false positive) from a genuine auth failure on completions.
    """
    return _consecutive_401s


def _new_idempotency_key() -> str:
    """Per-call OpenRouter ``Idempotency-Key`` value.

    Format: ``<request_id>-<uuid12>``. The key MUST be generated once per
    logical ``call_llm`` invocation and reused on every transport-level
    retry inside the breaker closure — that is how OpenRouter (and any
    idempotency-aware proxy in front of it) deduplicates the cost-ledger
    write so a retried POST doesn't double-bill the same logical request
    or leave orphan verification rows for the m0002 cleanup to reconcile.

    Including ``request_id`` in the key makes the OpenRouter-side dedupe
    log line correlate to our request log without a second lookup.
    """
    req = get_request_id() or "no-req"
    return f"{req}-{uuid.uuid4().hex[:12]}"


def _build_openrouter_headers(api_key: str, idem_key: str) -> dict[str, str]:
    """Standard OpenRouter request headers: auth, tracing, idempotency."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Idempotency-Key": idem_key,
    }
    headers.update(tracing_headers())
    return headers


def _strip_openrouter_prefix(model: str) -> str:
    """Strip the ``openrouter/`` prefix from model IDs.

    Settings store model IDs with this prefix for Bifrost compatibility
    (e.g. ``openrouter/openai/gpt-4o-mini``).  OpenRouter's API expects
    the bare ID (``openai/gpt-4o-mini``).
    """
    if model.startswith("openrouter/"):
        return model[len("openrouter/"):]
    return model


# ---------------------------------------------------------------------------
# BYOK direct-provider dispatch (E1 CR-008, Phase 3e-2a)
# ---------------------------------------------------------------------------


class _DirectClientCtx:
    """One-shot httpx client for a direct BYOK provider base_url.

    Unlike the pooled OpenRouter singleton, direct-provider calls use a fresh
    client per call (mirroring ``_call_ollama_direct``) — always closed on exit,
    so no per-loop singleton bookkeeping is needed for the low-volume BYOK path.
    """

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> httpx.AsyncClient:
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(connect=10, read=60, write=10, pool=10),
        )
        return self._client

    async def __aexit__(self, *_: object) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()


def _acquire_direct_client(base_url: str) -> _DirectClientCtx:
    """Context manager yielding a one-shot httpx client for a BYOK base_url."""
    return _DirectClientCtx(base_url)


async def _call_openai_compatible(
    messages: list[dict[str, str]],
    *,
    model: str,
    base_url: str,
    api_key: str,
    breaker_name: str,
    temperature: float = 0.1,
    max_tokens: int = 500,
    timeout: float | None = None,
    response_format: dict | None = None,
    extra_payload: dict | None = None,
) -> str:
    """Dispatch an OpenAI-shaped chat completion to a direct BYOK provider.

    OpenRouter, OpenAI, and xAI all speak the same ``/chat/completions`` wire, so
    the payload is identical to the OpenRouter path — only ``base_url`` + the
    bearer key differ (no OpenRouter-specific idempotency/referer headers). Runs
    under a per-provider ``byok-<provider>`` breaker so a failing direct provider
    is isolated from the shared OpenRouter breaker.
    """
    payload: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format:
        payload["response_format"] = response_format
    if extra_payload:
        payload.update(extra_payload)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    headers.update(tracing_headers())
    breaker = get_breaker(breaker_name)

    async def _do_call() -> str:
        async with _acquire_direct_client(base_url) as client:
            post_kwargs: dict = {"headers": headers, "json": payload}
            if timeout is not None:
                post_kwargs["timeout"] = timeout
            resp = await client.post("/chat/completions", **post_kwargs)
            resp.raise_for_status()
            data = resp.json()
            return _extract_content(data)

    return await breaker.call(_do_call)


def _split_system_messages(
    messages: list[dict[str, str]],
) -> tuple[str, list[dict[str, str]]]:
    """Split OpenAI-style messages into ``(system_text, non_system_messages)``.

    Anthropic hoists the system prompt to a top-level ``system`` param instead of
    a ``role: system`` message — all system messages are concatenated; the rest
    pass through unchanged and in order.
    """
    system_parts: list[str] = []
    convo: list[dict[str, str]] = []
    for m in messages:
        if m.get("role") == "system":
            content = m.get("content", "")
            if content:
                system_parts.append(content)
        else:
            convo.append(m)
    return "\n\n".join(system_parts), convo


async def _call_anthropic(
    messages: list[dict[str, str]],
    *,
    model: str,
    base_url: str,
    api_key: str,
    breaker_name: str,
    temperature: float = 0.1,
    max_tokens: int = 500,
    timeout: float | None = None,
    response_format: dict | None = None,
) -> str:
    """Dispatch a chat completion to the Anthropic Messages API (BYOK).

    Translates the OpenAI-shaped request to Anthropic's wire: the system prompt is
    hoisted to a top-level param, ``max_tokens`` is required, auth is ``x-api-key``
    + ``anthropic-version`` (not a bearer token), and the ``content[].text``
    response blocks are concatenated. Anthropic has no ``response_format`` param,
    so a json-mode request is expressed as a system instruction (mirroring the
    OpenRouter json fallback). Runs under the ``byok-anthropic`` breaker.
    """
    from core.routing.provider_state import ANTHROPIC_VERSION

    system, convo = _split_system_messages(messages)
    if response_format and response_format.get("type") == "json_object":
        instruction = "Respond with valid JSON only."
        system = f"{system}\n\n{instruction}".strip() if system else instruction

    payload: dict = {
        "model": model,
        "messages": convo,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if system:
        payload["system"] = system

    headers = {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    headers.update(tracing_headers())
    breaker = get_breaker(breaker_name)

    async def _do_call() -> str:
        async with _acquire_direct_client(base_url) as client:
            post_kwargs: dict = {"headers": headers, "json": payload}
            if timeout is not None:
                post_kwargs["timeout"] = timeout
            resp = await client.post("/messages", **post_kwargs)
            resp.raise_for_status()
            data = resp.json()
            return "".join(
                block.get("text", "")
                for block in data.get("content", [])
                if block.get("type") == "text"
            )

    return await breaker.call(_do_call)


def _to_gemini_contents(
    messages: list[dict[str, str]],
) -> tuple[str, list[dict]]:
    """Convert OpenAI-style messages to ``(system_text, gemini_contents)``.

    Gemini hoists the system prompt to ``systemInstruction`` and names the
    assistant role ``model``; ``contents`` parts carry the text.
    """
    system_parts: list[str] = []
    contents: list[dict] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content", "")
        if role == "system":
            if content:
                system_parts.append(content)
            continue
        gemini_role = "model" if role == "assistant" else "user"
        contents.append({"role": gemini_role, "parts": [{"text": content}]})
    return "\n\n".join(system_parts), contents


async def _call_gemini(
    messages: list[dict[str, str]],
    *,
    model: str,
    base_url: str,
    api_key: str,
    breaker_name: str,
    temperature: float = 0.1,
    max_tokens: int = 500,
    timeout: float | None = None,
    response_format: dict | None = None,
) -> str:
    """Dispatch a chat completion to the Google Gemini generateContent API (BYOK).

    Translates the OpenAI-shaped request to Gemini's wire: the system prompt hoisted
    to ``systemInstruction``, ``assistant`` → ``model``, generation params under
    ``generationConfig`` (``maxOutputTokens`` / ``temperature`` / ``responseMimeType``
    for json mode), auth via ``x-goog-api-key``, and the
    ``candidates[].content.parts[].text`` response concatenated. Runs under the
    ``byok-google`` breaker.
    """
    system, contents = _to_gemini_contents(messages)
    gen_config: dict = {"maxOutputTokens": max_tokens, "temperature": temperature}
    if response_format and response_format.get("type") == "json_object":
        gen_config["responseMimeType"] = "application/json"
    payload: dict = {"contents": contents, "generationConfig": gen_config}
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}

    headers = {
        "x-goog-api-key": api_key,
        "content-type": "application/json",
    }
    headers.update(tracing_headers())
    breaker = get_breaker(breaker_name)

    async def _do_call() -> str:
        async with _acquire_direct_client(base_url) as client:
            post_kwargs: dict = {"headers": headers, "json": payload}
            if timeout is not None:
                post_kwargs["timeout"] = timeout
            resp = await client.post(f"/models/{model}:generateContent", **post_kwargs)
            resp.raise_for_status()
            data = resp.json()
            candidates = data.get("candidates", [])
            if not candidates:
                return ""
            parts = candidates[0].get("content", {}).get("parts", [])
            return "".join(p.get("text", "") for p in parts)

    return await breaker.call(_do_call)


async def call_llm(
    messages: list[dict[str, str]],
    *,
    model: str = "",
    temperature: float = 0.1,
    max_tokens: int = 500,
    timeout: float | None = None,
    response_format: dict | None = None,
    extra_payload: dict | None = None,
    breaker_name: str = "openrouter",
    cost_sensitivity: str = "medium",  # noqa: ARG001 — observability / forward-compat
) -> str:
    """Call an LLM via OpenRouter directly.  Returns assistant content as string.

    Falls back to Bifrost if ``OPENROUTER_API_KEY`` is not set or the
    OpenRouter circuit breaker is open.

    Parameters
    ----------
    messages:
        Chat messages (OpenAI-compatible format).
    model:
        Model ID (with or without ``openrouter/`` prefix).  When empty,
        uses ``INTERNAL_LLM_MODEL`` from settings or Llama 3.3 70B.
    temperature:
        Sampling temperature.
    max_tokens:
        Maximum response tokens.
    timeout:
        Per-request timeout override (seconds).  ``None`` = client default.
    response_format:
        Optional ``{"type": "json_object"}`` for structured output.
    extra_payload:
        Additional keys merged into the API payload.
    breaker_name:
        Circuit breaker name for this call category.
    cost_sensitivity:
        ``"low"`` | ``"medium"`` | ``"high"``.  Only meaningful when ``model``
        is empty and the caller is relying on upstream routing: this parameter
        accepts a user-scoped cost preference so callers can forward it
        without an if-else.  When ``model`` is set explicitly, the choice is
        already made and this value is ignored.
    """
    if not model:
        model = os.getenv("INTERNAL_LLM_MODEL", "") or _DEFAULT_INTERNAL_MODEL

    # E1 CR-008: a BYOK direct provider (openai/xai) explicitly enabled for this
    # model's native provider serves it directly — a BYOK-only user with no
    # OpenRouter credit must still succeed. Resolved BEFORE the OpenRouter-key
    # check so the direct path is not blocked by a missing OPENROUTER_API_KEY.
    # When no BYOK is enabled, byok_target returns None and the OpenRouter path
    # below runs byte-identically.
    from core.routing.provider_state import byok_target
    _target = byok_target(model)
    if _target is not None:
        if _target.wire == "anthropic":
            return await _call_anthropic(
                messages,
                model=_target.model,
                base_url=_target.base_url,
                api_key=_target.api_key,
                breaker_name=f"byok-{_target.provider}",
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
                response_format=response_format,
            )
        if _target.wire == "gemini":
            return await _call_gemini(
                messages,
                model=_target.model,
                base_url=_target.base_url,
                api_key=_target.api_key,
                breaker_name=f"byok-{_target.provider}",
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
                response_format=response_format,
            )
        return await _call_openai_compatible(
            messages,
            model=_target.model,
            base_url=_target.base_url,
            api_key=_target.api_key,
            breaker_name=f"byok-{_target.provider}",
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            response_format=response_format,
            extra_payload=extra_payload,
        )

    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not configured. Set the key in .env — Bifrost "
            "was retired and is no longer available as a fallback gateway."
        )

    model = _strip_openrouter_prefix(model)

    payload: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format:
        payload["response_format"] = response_format
    if extra_payload:
        payload.update(extra_payload)

    breaker = get_breaker(breaker_name)
    # Generate ONCE per logical call so transport-level retries inside the
    # breaker closure reuse the same key (the entire purpose of idempotency).
    idem_key = _new_idempotency_key()

    async def _do_call() -> str:
        async with _acquire_client() as client:
            headers = _build_openrouter_headers(api_key, idem_key)

            post_kwargs: dict = {"headers": headers, "json": payload}
            if timeout is not None:
                post_kwargs["timeout"] = timeout

            resp = await client.post("/chat/completions", **post_kwargs)
            resp.raise_for_status()
            reset_auth_failure_count()
            data = resp.json()
            return _extract_content(data)

    try:
        return await breaker.call(_do_call)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (401, 403):
            global _consecutive_401s
            _consecutive_401s += 1
            _logger.warning(
                "OpenRouter auth failed (%d), consecutive_auth_failures=%d",
                exc.response.status_code, _consecutive_401s,
            )
            if _consecutive_401s >= _POOL_RECYCLE_401_THRESHOLD:
                await _recycle_client()
        elif HTTPStatus.BAD_REQUEST <= exc.response.status_code < HTTPStatus.INTERNAL_SERVER_ERROR:
            # 4xx (other than auth) — diagnose with model name + response body
            # so Sentry / log captures the actual rejection cause. Without this
            # the 2026-05-20 audit saw 80 events of bare "HTTPStatusError 400"
            # in the breadcrumb with no actionable detail. The body usually
            # names the offending field (deprecated model, bad payload shape).
            body_preview = ""
            try:
                body_preview = exc.response.text[:500]
            except Exception:  # silent-catch-allowed: diagnostic body capture is best-effort; the outer raise re-fires regardless, so a missed body preview can only reduce diagnostic detail, never mask an error
                body_preview = "<body capture failed>"
            _logger.warning(
                "OpenRouter 4xx %d on model=%s (breaker=%s): %s",
                exc.response.status_code, model, breaker_name, body_preview,
            )
            try:
                import sentry_sdk  # type: ignore[import-not-found]
                sentry_sdk.set_tag("openrouter_model", model)
                sentry_sdk.set_tag("openrouter_status", str(exc.response.status_code))
                sentry_sdk.set_context(
                    "openrouter_4xx",
                    {"model": model, "status": exc.response.status_code,
                     "response_preview": body_preview},
                )
            except ImportError:
                pass
        raise


async def call_llm_raw(
    messages: list[dict[str, str]],
    *,
    model: str = "",
    temperature: float = 0.1,
    max_tokens: int = 500,
    timeout: float | None = None,
    response_format: dict | None = None,
    extra_payload: dict | None = None,
    breaker_name: str = "openrouter",
    cost_sensitivity: str = "medium",  # noqa: ARG001 — observability / forward-compat
) -> dict:
    """Like :func:`call_llm` but returns the full parsed response dict.

    Used by verification which needs access to annotations (source URLs)
    and the raw message object, not just the text content.
    """
    # E1 CR-008 (BYOK): call_llm_raw intentionally has NO byok_target branch,
    # unlike call_llm above. This is the verification web transport — its models
    # carry OpenRouter's ``:online`` web-search overlay (VERIFICATION_EXPERT_WEB_MODEL
    # etc.) and return the citation annotations _extract_citation_urls reads. Direct
    # BYOK providers (api.x.ai / api.openai.com / …) have no ``:online`` model and
    # return no such annotations, so routing this call through byok_target would
    # silently break web-search verification. Verification stays OpenRouter-only by
    # design (operator decision 2026-07-20); a BYOK-only user without an OpenRouter
    # key cannot run external web verification — a documented limitation, not a bug.
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not configured. Set the key in .env — Bifrost "
            "was retired and is no longer available as a fallback gateway."
        )

    if not model:
        model = os.getenv("INTERNAL_LLM_MODEL", "") or _DEFAULT_INTERNAL_MODEL

    model = _strip_openrouter_prefix(model)

    payload: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format:
        payload["response_format"] = response_format
    if extra_payload:
        payload.update(extra_payload)

    breaker = get_breaker(breaker_name)
    # See call_llm: per-call key, stable across breaker-internal retries.
    idem_key = _new_idempotency_key()

    async def _do_call() -> dict:
        async with _acquire_client() as client:
            headers = _build_openrouter_headers(api_key, idem_key)

            post_kwargs: dict = {"headers": headers, "json": payload}
            if timeout is not None:
                post_kwargs["timeout"] = timeout

            resp = await client.post("/chat/completions", **post_kwargs)
            # 402 = credits exhausted — propagate as-is
            if resp.status_code == HTTPStatus.PAYMENT_REQUIRED:
                from core.agents.hallucination.verification import CreditExhaustedError
                raise CreditExhaustedError("openrouter")
            resp.raise_for_status()
            reset_auth_failure_count()
            return resp.json()

    try:
        return await breaker.call(_do_call)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (401, 403):
            global _consecutive_401s
            _consecutive_401s += 1
            _logger.warning(
                "OpenRouter auth failed (%d), consecutive_auth_failures=%d (raw)",
                exc.response.status_code, _consecutive_401s,
            )
            if _consecutive_401s >= _POOL_RECYCLE_401_THRESHOLD:
                await _recycle_client()
        raise


# ---------------------------------------------------------------------------
# Smart-routed LLM call (uses smart_router to pick model + provider)
# ---------------------------------------------------------------------------


# Estimated cost of the OpenRouter fallback model (``_DEFAULT_INTERNAL_MODEL``,
# meta-llama/llama-3.3-70b-instruct) — the same per-1K rate ``smart_router.route``
# stamps for the paid llama-3.3 tier. Keep in sync with _DEFAULT_INTERNAL_MODEL.
_FALLBACK_COST_PER_1K = 0.00015


def _openrouter_fallback_decision(original: "RouteDecision", *, model: str) -> "RouteDecision":
    """E1 CR-013: a RouteDecision reflecting an actual local->OpenRouter fallback.

    The router planned a local (ollama/quenchforge) serve, but the local backend
    was unavailable and paid OpenRouter served the bytes. Return a decision whose
    provider/model/cost describe what ACTUALLY served, so the SDK response stops
    reporting local/free for cloud usage.
    """
    from core.routing.smart_router import RouteDecision

    return RouteDecision(
        model=model,
        provider="openrouter_paid",
        reason=f"{original.reason} → local backend unavailable, served by OpenRouter",
        estimated_cost_per_1k=_FALLBACK_COST_PER_1K,
        tier_p95_ms=original.tier_p95_ms,
    )


async def route_and_call(
    messages: list[dict[str, str]],
    *,
    query: str = "",
    task_type: str = "internal",  # "chat", "internal", "verification", etc.
    temperature: float = 0.1,
    max_tokens: int = 500,
    response_format: dict | None = None,
    cost_sensitivity: str = "medium",
    kb_injection_count: int = 0,
    total_chars: int = 0,
    slo_budget_ms: int | None = None,
) -> tuple[str, "RouteDecision"]:
    """Smart-route a query to the best LLM, then call it.

    ``cost_sensitivity`` is forwarded to :func:`smart_router.route` so the
    user's cost preference influences model selection for this call.  See
    Task 17 audit C-6: this value used to get dropped at the router boundary
    and default to ``medium`` regardless of the user's setting.

    ``slo_budget_ms`` (optional) is a wall-clock budget. Forwarded to
    :func:`smart_router.route`; raises ``BudgetUnsatisfiableError`` (which
    callers should let propagate) when no tier fits.

    Returns ``(content, route_decision)`` tuple.
    """
    from core.routing.smart_router import TaskType, route

    # Client-defined task types (external clients use domain-specific labels
    # like "gtm_creative" or custom agent phases) map to safe INTERNAL routing
    # rather than raising. Stage-based provider routing (PROVIDER_STAGE_*) is a
    # separate mechanism keyed on `stage=`, unaffected by this. (GA P0.3)
    try:
        task = TaskType(task_type)
    except ValueError:
        _logger.warning(
            "route_and_call: unknown task_type %r — defaulting to internal routing",
            task_type,
        )
        task = TaskType.INTERNAL
    decision = await route(
        query,
        task_type=task,
        cost_sensitivity=cost_sensitivity,
        kb_injection_count=kb_injection_count,
        total_chars=total_chars,
        slo_budget_ms=slo_budget_ms,
    )

    if decision.provider == "ollama":
        from core.utils import inference_health

        # Call the local backend directly; own the fallback here (not inside the
        # transport) so the returned decision can be corrected to match the serve.
        try:
            content = await _call_ollama_direct(
                messages,
                model=decision.model,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            inference_health.record_success("llm", provider="ollama")
            return content, decision
        except Exception as exc:  # noqa: BLE001 — any local transport failure falls back
            from core.utils.swallowed import log_swallowed_error
            log_swallowed_error("core.utils.llm_client.route_and_call_fallback", exc)
            _logger.warning("Local backend failed (%s) — falling back to OpenRouter", exc)
            inference_health.record_fallback(
                "llm", configured="ollama", served_by="openrouter", detail=str(exc),
            )
            # E1 CR-013: OpenRouter now serves the bytes. Use a known-valid
            # OpenRouter model (INTERNAL_LLM_MODEL may hold a local name that 400s)
            # AND update the decision so the stamped provider/model/cost describe the
            # actual serve, not the pre-fallback local plan (provider=ollama/cost=0).
            content = await call_llm(
                messages,
                model=_DEFAULT_INTERNAL_MODEL,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
                cost_sensitivity=cost_sensitivity,
            )
            return content, _openrouter_fallback_decision(
                decision, model=_DEFAULT_INTERNAL_MODEL
            )
    else:
        # Call OpenRouter
        content = await call_llm(
            messages,
            model=decision.model,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            cost_sensitivity=cost_sensitivity,
        )
        return content, decision


async def _call_ollama_direct(
    messages: list[dict[str, str]],
    *,
    model: str,
    temperature: float,
    max_tokens: int,
) -> str:
    """Direct local-backend call for smart-routed queries.

    Pure transport: raises on failure. The caller (``route_and_call``) owns the
    OpenRouter fallback so it can correct the returned RouteDecision to match the
    actual serve (E1 CR-013) — pre-fix this swallowed the failure and fell back
    internally, leaving the decision reporting the local plan for cloud bytes.
    """
    import httpx as _httpx

    from core.routing.provider_state import local_backend_url
    # E1 CR-098: honor QUENCHFORGE_URL on a quenchforge box — pre-fix this read
    # OLLAMA_URL only, so the probe validated one daemon and the call hit another.
    ollama_url = local_backend_url()
    async with _httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{ollama_url}/api/chat",
            json={
                "model": model,
                "messages": messages,
                "stream": False,
                "options": {"temperature": temperature, "num_predict": max_tokens},
            },
        )
        resp.raise_for_status()
        return resp.json().get("message", {}).get("content", "")


