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
from collections import deque
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from http import HTTPStatus
from typing import Any

import httpx

import config
from config.stage_profiles import (
    INTERACTIVE_STAGES,
    MCP_STAGE_PREFIX,
    is_background_stage,
    normalize_stage,
)
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
#      the client instead of timing out in the server. The cap is a PRIORITY
#      gate, not a FIFO one: interactive stages may take every permit while
#      background stages get ``capacity - 1`` and stand aside whenever an
#      interactive caller is waiting. The user-facing streaming path
#      (``_stream_ollama``) is deliberately NOT capped at all.
#   2. A cooldown armed on timeout: after a local call times out,
#      subsequent local calls of the SAME class wait out the cooldown before
#      issuing (doubling per consecutive timeout up to a max; reset on
#      success) — backoff-on-timeout, never immediate retry pile-on. Per
#      class, so an ingest flood timing out cannot pace a chat turn.
#
# The gate is per-event-loop (asyncio primitives bind to a loop on
# first use — same hazard as the shared httpx client above); the cooldown
# clock is process-wide because the backend saturation it models is.

# The blocking-stage set lives in config.stage_profiles alongside the
# classification it complements (BACKGROUND_STAGES), so the two cannot drift.
# MCP tool stages are interactive by PREFIX — an MCP client (e.g. Claude Code
# via cerid-kb) is waiting synchronously on the tool call, whether or not that
# stage is classified — see docs/superpowers/plans/2026-09-06-chat-verify-followups.md
# Task 1.

_pacing_guard = threading.Lock()


class _PriorityGate:
    """Bounded-concurrency gate that lets interactive callers pre-empt the queue.

    Interactive callers may hold every permit; background callers may hold at
    most ``capacity - 1`` and are held back while any interactive caller is
    waiting. Background callers are served in arrival order via a ticket
    queue: ``Condition``'s own waiter deque cannot carry that order, because a
    background caller bounced by interactive demand re-enters ``wait()`` at the
    back of it.
    """

    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._background_capacity = max(1, capacity - 1)
        self._held = 0
        self._background_held = 0
        self._interactive_waiting = 0
        self._background_tickets: deque[int] = deque()
        self._next_background_ticket = 0
        self._cond = asyncio.Condition()

    def _admits(self, interactive: bool) -> bool:
        if self._held >= self._capacity:
            return False
        if interactive:
            return True
        return (
            self._background_held < self._background_capacity
            and self._interactive_waiting == 0
        )

    @asynccontextmanager
    async def slot(self, interactive: bool) -> AsyncIterator[None]:
        await self._acquire(interactive)
        try:
            yield
        finally:
            await self._release(interactive)

    async def _acquire(self, interactive: bool) -> None:
        if interactive:
            _note_interactive_demand()
        async with self._cond:
            if interactive:
                await self._wait_interactive()
                # Re-stamp: a caller that queued for a whole backend call is
                # still live demand while its own call runs, which is what
                # interactive_demand_recent()'s window is asked about.
                _note_interactive_demand()
            else:
                await self._wait_background()
                self._background_held += 1
            self._held += 1

    async def _wait_interactive(self) -> None:
        self._interactive_waiting += 1
        try:
            await self._cond.wait_for(lambda: self._admits(True))
        finally:
            # Runs under the condition lock on the cancellation path too, so a
            # cancelled waiter neither leaks its count nor keeps background
            # callers parked behind demand that is gone — hence the wake-up.
            self._interactive_waiting -= 1
            self._cond.notify_all()

    async def _wait_background(self) -> None:
        ticket = self._next_background_ticket
        self._next_background_ticket += 1
        self._background_tickets.append(ticket)
        try:
            await self._cond.wait_for(
                lambda: self._background_tickets[0] == ticket
                and self._admits(False)
            )
        finally:
            # Leaving the queue — admitted or cancelled — promotes the next
            # ticket, whose predicate nobody else would re-evaluate.
            self._background_tickets.remove(ticket)
            self._cond.notify_all()

    async def _release(self, interactive: bool) -> None:
        # A cancellation landing on this await (a second cancel, or one
        # delivered while the condition lock is contended) would lose the
        # permit for the life of the process; the give-back runs to completion
        # even when the caller stops waiting for it.
        await asyncio.shield(self._give_back_permit(interactive))

    async def _give_back_permit(self, interactive: bool) -> None:
        async with self._cond:
            self._held -= 1
            if not interactive:
                self._background_held -= 1
            self._cond.notify_all()

    def held(self) -> int:
        """Permits currently held, including the caller's own."""
        return self._held


class _Cooldown:
    __slots__ = ("seconds", "until")

    def __init__(self) -> None:
        self.until = 0.0
        self.seconds = 0.0


_pacing_gate: _PriorityGate | None = None
_pacing_gate_loop: asyncio.AbstractEventLoop | None = None
_interactive_cooldown = _Cooldown()
_background_cooldown = _Cooldown()
_interactive_demand_at: float = 0.0


def _pacing_max_concurrency() -> int:
    return max(1, int(getattr(config, "INTERNAL_LLM_MAX_CONCURRENCY", 2)))


def _get_pacing_gate() -> _PriorityGate:
    global _pacing_gate, _pacing_gate_loop
    loop = asyncio.get_running_loop()
    with _pacing_guard:
        if _pacing_gate is None or _pacing_gate_loop is not loop:
            _pacing_gate = _PriorityGate(_pacing_max_concurrency())
            _pacing_gate_loop = loop
        return _pacing_gate


def _cooldown_for(interactive: bool) -> _Cooldown:
    return _interactive_cooldown if interactive else _background_cooldown


def _is_interactive(stage: str | None, interactive: bool) -> bool:
    return (
        interactive
        or stage in INTERACTIVE_STAGES
        or (stage is not None and stage.startswith(MCP_STAGE_PREFIX))
    )


def _note_interactive_demand() -> None:
    global _interactive_demand_at
    _interactive_demand_at = time.monotonic()


def interactive_demand_recent(window_s: float = 300.0) -> bool:
    """True when an interactive stage acquired or waited on the pacing gate
    within the last *window_s* seconds.

    Background schedulers read this to stand down while a chat session is
    live, rather than competing for the local backend's two permits.
    """
    if _interactive_demand_at <= 0.0:
        return False
    return (time.monotonic() - _interactive_demand_at) < window_s


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


def _record_pacing_timeout(interactive: bool) -> None:
    """Arm (or extend) the caller class's cooldown after a local-backend timeout."""
    initial = float(os.environ.get("INTERNAL_LLM_TIMEOUT_COOLDOWN", "2.0"))
    maximum = float(os.environ.get("INTERNAL_LLM_TIMEOUT_COOLDOWN_MAX", "30.0"))
    cooldown = _cooldown_for(interactive)
    with _pacing_guard:
        cooldown.seconds = min(maximum, (cooldown.seconds * 2) or initial)
        cooldown.until = time.monotonic() + cooldown.seconds


def _record_pacing_success(interactive: bool) -> None:
    cooldown = _cooldown_for(interactive)
    with _pacing_guard:
        cooldown.until = 0.0
        cooldown.seconds = 0.0


def _reset_pacing_state() -> None:
    """Test hook: drop the gate, disarm both cooldowns, forget interactive demand."""
    global _pacing_gate, _pacing_gate_loop, _interactive_demand_at
    with _pacing_guard:
        _pacing_gate = None
        _pacing_gate_loop = None
        _interactive_demand_at = 0.0
    _record_pacing_success(interactive=True)
    _record_pacing_success(interactive=False)


async def _wait_pacing_cooldown(interactive: bool) -> None:
    cooldown = _cooldown_for(interactive)
    with _pacing_guard:
        remaining = cooldown.until - time.monotonic()
    if remaining > 0:
        logger.info(
            "local LLM cooldown after timeout — pacing %.2fs before next %s call",
            remaining,
            "interactive" if interactive else "background",
        )
        await asyncio.sleep(remaining)


# Cloud providers a Private Mode degrade should route away from. Kept
# separate from config.PIPELINE_PROVIDERS (a routing table, not a privacy
# classification) so adding a cloud provider here is a deliberate choice.
_CLOUD_PROVIDERS = frozenset({"openrouter"})

# Private Mode level at and above which cloud-pinned stages degrade to the
# local provider — matches config.environment_profiles.resolve_profile's
# import-time rule (L1 or above disables cloud stages).
_PRIVATE_MODE_CLOUD_CUTOFF = 1

# Registered by the app layer at startup (core/ must not import app/, so the
# app wires this in rather than internal_llm importing app.services.private_mode
# directly). None until wired — e.g. a script that never calls app.main.
_private_mode_level_probe: Callable[[], int] | None = None


def set_private_mode_level_probe(probe: Callable[[], int] | None) -> None:
    """Register the callable ``_resolve_stage_provider`` polls for the live
    Private Mode level.

    ``config.environment_profiles.resolve_profile`` degrades a cloud profile
    to ``local-only`` at settings load, but Private Mode's level lives in
    Redis and is mutable at runtime — an operator flipping it on mid-process
    must not leave already-resolved cloud pins (``PROVIDER_STAGE_*``,
    ``PIPELINE_PROVIDERS``) still calling out. When the probe reports L1 or
    above, cloud-pinned stages resolve to the local provider instead — the
    import-time degrade rule, honoured at call time.
    """
    global _private_mode_level_probe
    _private_mode_level_probe = probe


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

    A live Private Mode L1+ overrides all of the above for a resolved cloud
    provider under ``hybrid``/``cloud-first``: see
    :func:`set_private_mode_level_probe`.
    """
    if not stage:
        resolved = default_provider
    else:
        env_override = os.environ.get(f"PROVIDER_STAGE_{normalize_stage(stage)}")
        if env_override:
            resolved = env_override
        else:
            pipeline_providers = getattr(config, "PIPELINE_PROVIDERS", {})
            resolved = pipeline_providers.get(stage, default_provider)

    if (
        _private_mode_level_probe is not None
        and resolved in _CLOUD_PROVIDERS
        and getattr(config, "CERID_ENVIRONMENT_PROFILE", "") in ("hybrid", "cloud-first")
        and _private_mode_level_probe() >= _PRIVATE_MODE_CLOUD_CUTOFF
    ):
        from config.environment_profiles import degrade_target_provider

        return degrade_target_provider(
            getattr(config, "INTERNAL_LLM_PROVIDER", "openrouter"),
            os.environ.get("HOST_RECOMMENDED_LOCAL_BACKEND"),
        )
    return resolved


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


# ── Effective local chat model resolution (chat-verify-pipeline Task 6) ────
# INTERNAL_LLM_MODEL is an operator-set config value that can drift from what
# quenchforge actually has loaded — the gateway today silently routes any
# chat name to its single slot, so the drift caused no request failure, only
# invisible cost accounting (PricingTable raising "Unknown model" for a name
# never registered) and a chat call landing on whatever model happened to be
# loaded. A pending gateway build 400s on a name mismatch instead, making
# this resolution load-bearing rather than cosmetic.
_LOCAL_EMBED_RERANK_PATTERNS = ("embed", "rerank", "bge-", "nomic", "minilm", "e5-")

_SERVED_MODELS_TTL_S = 300.0
_SERVED_MODELS_TIMEOUT_S = 5.0
_served_models_guard = threading.Lock()
_served_models_cache: list[str] | None = None
# None = never attempted a fetch yet. Distinct from 0.0 so the freshness
# check below can throttle repeated FAILED attempts (cache stays None,
# but the attempt still happened) the same as it throttles successes —
# gating on "cache is not None" instead would retry every single call
# for as long as the gateway never answers even once.
_served_models_cache_ts: float | None = None

_effective_local_model_guard = threading.Lock()
_effective_local_model_cache: str | None = None
# Served-list snapshot (its cache ts) the cached resolution above was
# computed against — lets a served-list refresh trigger a re-resolution
# without re-resolving (and re-logging) on every call.
_effective_local_model_resolved_ts: float | None = None
# Last model background stages resolved to — see _note_background_resolution.
_background_model_resolution: str | None = None
# The gateway's own chat-slot model (quenchforge `GET /` -> slots.chat.model).
# Refreshed by the same fetch as the served list; None when the gateway does
# not report it (Ollama, older quenchforge).
_gateway_chat_slot_model_cache: str | None = None


def is_embedding_or_rerank_model(name: str) -> bool:
    """True when *name* looks like an embedding/reranking model, not chat."""
    lowered = name.lower()
    return any(pattern in lowered for pattern in _LOCAL_EMBED_RERANK_PATTERNS)


def _served_models_url() -> str:
    return getattr(config, "QUENCHFORGE_URL", "") or os.getenv(
        "OLLAMA_URL", "http://localhost:11434"
    )


def _served_names(payload: dict) -> list[str]:
    """Names the gateway actually serves. quenchforge reports ``loaded`` per
    cached GGUF; a file that is cached but not loaded by any slot is not
    served, whatever the list says."""
    return [
        m.get("name", "")
        for m in payload.get("models", [])
        if m.get("loaded", True) is not False
    ]


def _fetch_served_models() -> tuple[list[str] | None, float]:
    """GET ``{QUENCHFORGE_URL}/api/tags`` synchronously, cached for
    ``_SERVED_MODELS_TTL_S``. Sync callers only — the async hot path
    (``_call_ollama``) uses :func:`_fetch_served_models_async` instead so a
    cache-miss/refresh never blocks the event loop.

    Returns ``(served, ts)``: ``served`` is ``None`` on a fetch failure
    (whether nothing has ever been cached, or a refresh attempt failed);
    ``ts`` is the monotonic time this specific snapshot (or failed attempt)
    was taken, so callers can detect whether two calls saw the *same*
    underlying attempt without re-reading module state after releasing the
    lock (a concurrent refresh could otherwise stamp a stale snapshot with
    a newer timestamp than the one it was actually fetched under).

    The freshness gate is on ``_served_models_cache_ts`` alone — NOT on
    whether ``_served_models_cache`` is populated. A failed attempt is
    throttled exactly like a success (``ts`` is stamped either way): a
    gateway that has never answered even once must not be re-hammered on
    every single call, which gating on "cache is not None" would do.
    """
    global _served_models_cache, _served_models_cache_ts
    now = time.monotonic()
    with _served_models_guard:
        if (
            _served_models_cache_ts is not None
            and (now - _served_models_cache_ts) < _SERVED_MODELS_TTL_S
        ):
            return _served_models_cache, _served_models_cache_ts
    base_url = _served_models_url()
    try:
        resp = httpx.get(f"{base_url}/api/tags", timeout=_SERVED_MODELS_TIMEOUT_S)
        resp.raise_for_status()
        names = _served_names(resp.json())
    except Exception as exc:
        log_swallowed_error("core.utils.internal_llm.fetch_served_models", exc)
        with _served_models_guard:
            _served_models_cache_ts = now
        return None, now
    _refresh_gateway_chat_slot_model_sync(base_url)
    with _served_models_guard:
        _served_models_cache = names
        _served_models_cache_ts = now
        return names, now


def _refresh_gateway_chat_slot_model_sync(base_url: str) -> None:
    """Best-effort GET of ``{base_url}/`` for ``slots.chat.model``. Called
    right after a successful served-list fetch; any error (older
    quenchforge with no root route, Ollama, a timeout) leaves the cache
    unchanged rather than disturbing the just-fetched served list."""
    global _gateway_chat_slot_model_cache
    try:
        resp = httpx.get(f"{base_url}/", timeout=_SERVED_MODELS_TIMEOUT_S)
        resp.raise_for_status()
        chat_slot = resp.json().get("slots", {}).get("chat", {}).get("model")
    except Exception as exc:
        log_swallowed_error("core.utils.internal_llm.fetch_gateway_chat_slot_model", exc)
        return
    _gateway_chat_slot_model_cache = chat_slot


async def _fetch_served_models_async() -> tuple[list[str] | None, float]:
    """Async counterpart of :func:`_fetch_served_models` for the hot async
    call path (``_call_ollama``). Same cache, same TTL, same failure-
    throttling semantics — but fetches with the module's loop-bound
    ``httpx.AsyncClient`` (:func:`_get_ollama_client`) instead of the
    blocking ``httpx.get``, so a cache-miss/refresh never stalls the event
    loop for concurrent requests.
    """
    global _served_models_cache, _served_models_cache_ts
    now = time.monotonic()
    with _served_models_guard:
        if (
            _served_models_cache_ts is not None
            and (now - _served_models_cache_ts) < _SERVED_MODELS_TTL_S
        ):
            return _served_models_cache, _served_models_cache_ts
    base_url = _served_models_url()
    try:
        client = await _get_ollama_client()
        resp = await client.get(
            f"{base_url}/api/tags", timeout=_SERVED_MODELS_TIMEOUT_S
        )
        resp.raise_for_status()
        names = _served_names(resp.json())
    except Exception as exc:
        log_swallowed_error("core.utils.internal_llm.fetch_served_models_async", exc)
        with _served_models_guard:
            _served_models_cache_ts = now
        return None, now
    await _refresh_gateway_chat_slot_model_async(base_url, client)
    with _served_models_guard:
        _served_models_cache = names
        _served_models_cache_ts = now
        return names, now


async def _refresh_gateway_chat_slot_model_async(base_url: str, client: httpx.AsyncClient) -> None:
    """Async counterpart of :func:`_refresh_gateway_chat_slot_model_sync`."""
    global _gateway_chat_slot_model_cache
    try:
        resp = await client.get(f"{base_url}/", timeout=_SERVED_MODELS_TIMEOUT_S)
        resp.raise_for_status()
        chat_slot = resp.json().get("slots", {}).get("chat", {}).get("model")
    except Exception as exc:
        log_swallowed_error("core.utils.internal_llm.fetch_gateway_chat_slot_model_async", exc)
        return
    _gateway_chat_slot_model_cache = chat_slot


def _resolve_effective_local_model(served: list[str] | None, previous: str | None) -> str:
    """Pure resolution logic shared by the sync and async entry points.

    Callers hold ``_effective_local_model_guard`` for the duration and pass
    the previously resolved name in ``previous``. The served-list TTL makes
    this recompute every ``_SERVED_MODELS_TTL_S``, so warning on every config
    miss reported the same unchanged fact ~288 times a day; the WARNING is
    emitted only when the resolution actually changes.
    """
    configured = getattr(config, "INTERNAL_LLM_MODEL", "") or config.OLLAMA_DEFAULT_MODEL
    if served is None:
        # No served list at all (first-ever fetch failed, or refresh failed
        # with nothing cached). Keep whatever was last resolved rather than
        # reverting to the raw config value; if nothing has ever resolved,
        # fall back to the configured name silently.
        return previous if previous is not None else configured
    if configured in served:
        return configured
    chat_slot = _gateway_chat_slot_model_cache
    if chat_slot and chat_slot in served:
        if previous != chat_slot:
            logger.warning(
                "configured local chat model %r is not served by the gateway; "
                "using the gateway's chat-slot model %r",
                configured, chat_slot,
            )
        return chat_slot
    # A still-served previous resolution stays put: the served list's order can
    # change between refreshes, and flapping between equally valid models
    # would churn every stage's model for no reason.
    if previous is not None and previous in served:
        return previous
    candidates = [m for m in served if m and not is_embedding_or_rerank_model(m)]
    if not candidates:
        if previous != configured:
            logger.warning(
                "configured local chat model %r is not served by the gateway "
                "(served=%s); no non-embedding alternative found, keeping it",
                configured, served,
            )
        return configured
    resolved = candidates[0]
    if previous != resolved:
        logger.warning(
            "configured local chat model %r is not served by the gateway; "
            "using served model %r instead",
            configured, resolved,
        )
    return resolved


def _apply_resolution(served: list[str] | None, snapshot_ts: float) -> str:
    """Recompute (and cache) the resolution iff ``snapshot_ts`` — the time
    the caller's own fetch attempt actually ran under, passed in directly
    rather than re-read from module state — differs from the timestamp the
    last resolution was computed from. This is what makes the served-list
    TTL real (re-resolves on a stale-cache refresh) while still logging
    the "not served" WARNING only once per actual change, not once per
    call. Taking ``snapshot_ts`` as a parameter (instead of re-reading
    ``_served_models_cache_ts`` here) avoids a race where a concurrent
    refresh between the fetcher's lock section and this one could stamp
    the caller's OLD served snapshot with a NEWER timestamp than the one
    it was actually fetched under.
    """
    global _effective_local_model_cache, _effective_local_model_resolved_ts
    with _effective_local_model_guard:
        if (
            _effective_local_model_cache is not None
            and _effective_local_model_resolved_ts == snapshot_ts
        ):
            return _effective_local_model_cache
        _effective_local_model_cache = _resolve_effective_local_model(
            served, _effective_local_model_cache,
        )
        _effective_local_model_resolved_ts = snapshot_ts
        return _effective_local_model_cache


def effective_local_model() -> str:
    """Resolve the local chat model name to send to the gateway (sync).

    ``INTERNAL_LLM_MODEL`` wins when the gateway actually serves it.
    Otherwise falls back to the first served model that isn't an
    embedding/rerank model, logging one WARNING naming both. Re-resolves
    whenever the served-list cache refreshes (at most every
    ``_SERVED_MODELS_TTL_S``); a refresh failure keeps the last resolved
    name. For sync callers only (health, pricing) — the async hot path
    (``_call_ollama``) uses :func:`effective_local_model_async` so a
    cache-miss never blocks the event loop.
    :func:`reset_effective_local_model_cache` clears everything for tests.
    """
    served, ts = _fetch_served_models()
    return _apply_resolution(served, ts)


async def effective_local_model_async() -> str:
    """Async-safe counterpart of :func:`effective_local_model` for the hot
    async call path (``_call_ollama``) — see :func:`_fetch_served_models_async`
    for why this must not call the blocking ``httpx.get``.
    """
    served, ts = await _fetch_served_models_async()
    return _apply_resolution(served, ts)


async def _local_model_for_stage(stage: str | None) -> str:
    """Resolve the local model name for *stage*.

    Background stages (``config.stage_profiles.BACKGROUND_STAGES``) prefer the
    second, smaller slot ``INTERNAL_LLM_MODEL_BACKGROUND`` — on class-A
    hardware a 3B answers an extraction prompt 2.5x faster than the 7B chat
    slot at equal recall, and nobody is waiting on those calls. The preference
    holds only when the gateway actually SERVES that name: an unserved name is
    silently rerouted by today's gateway and 400s on a pending build, so an
    unconfigured second slot must fall back to the chat model rather than fail
    every background call.
    """
    default_model = await effective_local_model_async()
    background_model = getattr(config, "INTERNAL_LLM_MODEL_BACKGROUND", "")
    if not background_model or not is_background_stage(stage):
        return default_model
    served, _ts = await _fetch_served_models_async()
    resolved = (
        background_model
        if served is not None and background_model in served
        else default_model
    )
    _note_background_resolution(background_model, resolved)
    return resolved


def _note_background_resolution(configured: str, resolved: str) -> None:
    """Log one INFO per resolution CHANGE — the served list refreshes every
    ``_SERVED_MODELS_TTL_S``, so logging per call would repeat the same
    unchanged fact ~288 times a day (the lesson :func:`_resolve_effective_local_model`
    already learned for its own WARNING)."""
    global _background_model_resolution
    with _effective_local_model_guard:
        if _background_model_resolution == resolved:
            return
        _background_model_resolution = resolved
    if resolved == configured:
        logger.info("background stages resolved to local model %r", resolved)
    else:
        logger.info(
            "background model %r is not served by the gateway; background "
            "stages use %r instead",
            configured, resolved,
        )


def is_local_model_name(name: str) -> bool:
    """True when *name* names a local chat model.

    Matches the configured value, the Ollama fallback default, or the
    gateway-resolved effective model — whichever of those *name* happens to
    be, it is a local-inference identifier with no per-token cost. Cloud
    model ids always carry a ``provider/`` prefix, so a bare miss on the two
    cheap config checks short-circuits before touching the network.
    """
    if not name or "/" in name:
        return False
    if name == getattr(config, "INTERNAL_LLM_MODEL", ""):
        return True
    if name == getattr(config, "OLLAMA_DEFAULT_MODEL", ""):
        return True
    return name == effective_local_model()


def reset_effective_local_model_cache() -> None:
    """Test hook: forget the cached served list and resolved model."""
    global _effective_local_model_cache, _effective_local_model_resolved_ts
    global _served_models_cache, _served_models_cache_ts
    global _background_model_resolution, _gateway_chat_slot_model_cache
    with _effective_local_model_guard:
        _effective_local_model_cache = None
        _effective_local_model_resolved_ts = None
        _background_model_resolution = None
    with _served_models_guard:
        _served_models_cache = None
        _served_models_cache_ts = None
        _gateway_chat_slot_model_cache = None


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
    interactive: bool = False,
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
      - pacing priority on the local backend: stages in
        ``INTERACTIVE_STAGES`` (or any call passing *interactive*) take
        precedence over background enrichment at the concurrency gate.
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
            interactive=interactive,
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
    interactive: bool = False,
) -> str:
    """Call a local Ollama-protocol backend (stock Ollama or Quenchforge).

    The wire format is identical across both backends; ``provider`` only
    selects the URL (``OLLAMA_URL`` vs ``QUENCHFORGE_URL``) and the
    user-facing label in fallback log lines.
    """
    import httpx

    is_interactive = _is_interactive(stage, interactive)

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
    # "/") can't be served by the local daemon, so use the resolved local
    # default there — validated against what the gateway actually serves
    # (Task 6), and for a background stage against the second slot
    # (INTERNAL_LLM_MODEL_BACKGROUND) first. The async resolver is required
    # here: a cache-miss/refresh fetch must never block this coroutine's event
    # loop the way the sync `effective_local_model()` (used by health/pricing)
    # would.
    local_model = (
        model if (model and "/" not in model) else await _local_model_for_stage(stage)
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
                # bf-f3 pacing: honor this class's timeout cooldown, then take
                # a gate permit for the duration of the attempt only (backoff
                # sleeps below never hold a permit).
                await _wait_pacing_cooldown(is_interactive)
                async with _get_pacing_gate().slot(is_interactive):
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
                # Arm the cooldown so CONCURRENT callers of the same class
                # back off too — per-call retry backoff alone let N in-flight
                # calls each retry into the saturated backend (qf-pacing).
                _record_pacing_timeout(is_interactive)
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
        _record_pacing_success(is_interactive)
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
        # The stream API has no interactive override; classify by stage alone,
        # then hold one pacing-gate permit for the whole stream — a streaming
        # chat turn must count against the two CPU permits like a
        # non-streaming one.
        is_interactive = _is_interactive(stage, False)
        try:
            await _wait_pacing_cooldown(is_interactive)
            async with _get_pacing_gate().slot(is_interactive):
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
