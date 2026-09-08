# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Resolve the local chat model against what the gateway actually serves.

INTERNAL_LLM_MODEL is an operator-set config value that can drift from what
quenchforge has loaded — today the gateway silently routes any chat name to
its single slot, but a pending gateway build 400s on a name mismatch, so
this resolution is load-bearing (task-6-brief.md).
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import core.utils.internal_llm as mod


@pytest.fixture(autouse=True)
def _fresh_resolver_state(monkeypatch):
    mod.reset_effective_local_model_cache()
    monkeypatch.setattr(mod.config, "INTERNAL_LLM_MODEL", "llama3.1-8b", raising=False)
    monkeypatch.setattr(mod.config, "OLLAMA_DEFAULT_MODEL", "llama3.2:3b", raising=False)
    monkeypatch.setattr(
        mod.config, "QUENCHFORGE_URL", "http://quenchforge.test:11434", raising=False
    )
    yield
    mod.reset_effective_local_model_cache()


class _FakeTagsResponse:
    def __init__(self, names: list[str], status_code: int = 200) -> None:
        self._names = names
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=MagicMock(), response=self)

    def json(self) -> dict:
        return {"models": [{"name": n} for n in self._names]}


def _wire_tags(monkeypatch, names: list[str]) -> None:
    monkeypatch.setattr(mod.httpx, "get", lambda url, timeout: _FakeTagsResponse(names))


def test_configured_name_served_is_used(monkeypatch):
    _wire_tags(monkeypatch, ["llama3.1-8b", "nomic-embed-text-v1.5"])
    assert mod.effective_local_model() == "llama3.1-8b"


def test_not_served_falls_back_to_first_non_embedding_and_warns(monkeypatch, caplog):
    _wire_tags(monkeypatch, ["nomic-embed-text-v1.5", "qwen2.5-7b-instruct-q4_k_m"])
    with caplog.at_level(logging.WARNING, logger="ai-companion.internal_llm"):
        result = mod.effective_local_model()
    assert result == "qwen2.5-7b-instruct-q4_k_m"
    warnings = [
        r for r in caplog.records
        if r.name == "ai-companion.internal_llm" and r.levelno == logging.WARNING
    ]
    assert len(warnings) == 1
    assert "llama3.1-8b" in warnings[0].getMessage()
    assert "qwen2.5-7b-instruct-q4_k_m" in warnings[0].getMessage()


def test_only_embedding_models_served_keeps_configured_and_warns(monkeypatch, caplog):
    _wire_tags(monkeypatch, ["nomic-embed-text-v1.5", "bge-reranker-v2-m3"])
    with caplog.at_level(logging.WARNING, logger="ai-companion.internal_llm"):
        result = mod.effective_local_model()
    assert result == "llama3.1-8b"
    warnings = [
        r for r in caplog.records
        if r.name == "ai-companion.internal_llm" and r.levelno == logging.WARNING
    ]
    assert len(warnings) == 1
    assert "llama3.1-8b" in warnings[0].getMessage()


def test_fetch_failure_falls_back_to_configured_silently(monkeypatch, caplog):
    def _raise(url, timeout):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(mod.httpx, "get", _raise)
    with caplog.at_level(logging.WARNING, logger="ai-companion.internal_llm"):
        result = mod.effective_local_model()
    assert result == "llama3.1-8b"
    # A fetch failure is swallowed under the "ai-companion.swallowed" channel
    # per the repo's silent-catch rule; the resolver's own "not served"
    # warning must not fire — that would be a false claim about the served
    # list, which was never actually retrieved.
    resolver_warnings = [
        r for r in caplog.records
        if r.name == "ai-companion.internal_llm" and r.levelno == logging.WARNING
    ]
    assert not resolver_warnings


def test_throttles_repeated_fetch_failures_within_ttl(monkeypatch):
    """A gateway that has never answered even once must still be throttled
    to at most one fetch attempt per TTL window — gating the freshness
    check on "cache is not None" (instead of the attempt timestamp alone)
    would retry on every single call for as long as it stays down, adding
    up to 5s of latency to every chat request (review round 2)."""
    calls = {"n": 0}
    clock = {"t": 0.0}

    def _raise(url, timeout):
        calls["n"] += 1
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(mod.httpx, "get", _raise)
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock["t"])

    for _ in range(5):
        result = mod.effective_local_model()
        assert result == "llama3.1-8b"  # kept, configured value, silently
        clock["t"] += 1.0  # small steps, all well within the 300s TTL

    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_throttles_repeated_async_fetch_failures_within_ttl(monkeypatch):
    """Async counterpart — this is the path that actually runs on every
    ``_call_ollama`` invocation, so an un-throttled retry loop here is the
    one that would add 5s to every chat request."""
    calls = {"n": 0}
    clock = {"t": 0.0}

    async def _get(url, timeout=None):
        calls["n"] += 1
        raise httpx.ConnectError("refused")

    fake_client = MagicMock()
    fake_client.get = _get
    monkeypatch.setattr(mod, "_get_ollama_client", AsyncMock(return_value=fake_client))
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock["t"])

    for _ in range(5):
        result = await mod.effective_local_model_async()
        assert result == "llama3.1-8b"
        clock["t"] += 1.0

    assert calls["n"] == 1


def test_resolution_is_cached_for_the_process(monkeypatch):
    calls = {"n": 0}

    def _get(url, timeout):
        if url.endswith("/api/tags"):
            calls["n"] += 1
        return _FakeTagsResponse(["llama3.1-8b"])

    monkeypatch.setattr(mod.httpx, "get", _get)
    mod.effective_local_model()
    mod.effective_local_model()
    assert calls["n"] == 1


class _FakeChatResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"message": {"content": "ok"}}


class _PassThroughBreaker:
    async def call(self, fn):  # type: ignore[no-untyped-def]
        return await fn()


# ---------------------------------------------------------------------------
# Review fix 1: the async hot path (_call_ollama) must never block the event
# loop on the blocking `httpx.get` — it has its own async fetch.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_path_does_not_call_sync_httpx_get(monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("sync httpx.get must not be called from the async resolver")

    monkeypatch.setattr(mod.httpx, "get", _boom)

    fake_client = MagicMock()
    fake_client.get = AsyncMock(return_value=_FakeTagsResponse(["llama3.1-8b"]))
    monkeypatch.setattr(mod, "_get_ollama_client", AsyncMock(return_value=fake_client))

    result = await mod.effective_local_model_async()

    assert result == "llama3.1-8b"
    # Tags fetch, then the chat-slot-model root fetch on the same client.
    assert fake_client.get.await_count == 2


@pytest.mark.asyncio
async def test_call_ollama_sends_resolved_model_via_async_resolver(monkeypatch):
    """_call_ollama's own fallback resolution must go through the async
    resolver too — not the sync one, and not the blocking httpx.get."""

    def _boom(*args, **kwargs):
        raise AssertionError("sync httpx.get must not be called from _call_ollama")

    monkeypatch.setattr(mod.httpx, "get", _boom)

    async def _get(url, timeout=None):
        return _FakeTagsResponse(["llama3.1-8b"])

    async def _post(url, *, json):
        return _FakeChatResponse()

    fake_client = MagicMock()
    fake_client.get = _get
    fake_client.post = _post
    monkeypatch.setattr(mod, "_get_ollama_client", AsyncMock(return_value=fake_client))
    monkeypatch.setattr(mod, "get_breaker", lambda _name: _PassThroughBreaker())
    monkeypatch.setenv("OLLAMA_URL", "http://test-host:11434")

    result = await mod._call_ollama(
        [{"role": "user", "content": "hi"}], temperature=0, max_tokens=5,
    )
    assert result == "ok"


# ---------------------------------------------------------------------------
# Review fix 2: the served-list TTL is real — a stale cache re-resolves, and
# a failed refresh keeps the last resolved name instead of the raw config.
# ---------------------------------------------------------------------------


def test_reresolves_after_ttl_when_served_list_changes(monkeypatch):
    served = {"models": ["llama3.1-8b"]}
    calls = {"n": 0}
    clock = {"t": 0.0}

    def _get(url, timeout):
        if url.endswith("/api/tags"):
            calls["n"] += 1
        return _FakeTagsResponse(served["models"])

    monkeypatch.setattr(mod.httpx, "get", _get)
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock["t"])

    assert mod.effective_local_model() == "llama3.1-8b"
    assert calls["n"] == 1

    # Gateway now serves something else, but we're still inside the TTL —
    # must keep serving the already-resolved name without refetching.
    served["models"] = ["qwen2.5-7b-instruct-q4_k_m"]
    clock["t"] += 100.0
    assert mod.effective_local_model() == "llama3.1-8b"
    assert calls["n"] == 1

    # Past the 300s TTL — refetches and re-resolves against the new list.
    clock["t"] += 250.0
    assert mod.effective_local_model() == "qwen2.5-7b-instruct-q4_k_m"
    assert calls["n"] == 2


def test_keeps_last_resolved_name_when_ttl_refresh_fetch_fails(monkeypatch, caplog):
    served = ["nomic-embed-text-v1.5", "qwen2.5-7b-instruct-q4_k_m"]
    should_fail = {"on": False}
    clock = {"t": 0.0}

    def _get(url, timeout):
        if should_fail["on"]:
            raise httpx.ConnectError("refused")
        return _FakeTagsResponse(served)

    monkeypatch.setattr(mod.httpx, "get", _get)
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock["t"])

    # First resolution: configured "llama3.1-8b" isn't served, falls back.
    assert mod.effective_local_model() == "qwen2.5-7b-instruct-q4_k_m"

    # Advance past the TTL, then make the refresh fetch fail. Clear the
    # first resolution's own (expected) warning so only the second call's
    # log output is under test below.
    clock["t"] += 301.0
    should_fail["on"] = True
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="ai-companion.internal_llm"):
        result = mod.effective_local_model()

    # Kept the last resolved name — did not revert to the raw configured
    # value just because the refresh attempt failed.
    assert result == "qwen2.5-7b-instruct-q4_k_m"
    # No new "not served" warning — nothing new was actually learned this
    # time; the refresh simply failed and the prior resolution was kept.
    resolver_warnings = [
        r for r in caplog.records
        if r.name == "ai-companion.internal_llm" and r.levelno == logging.WARNING
    ]
    assert not resolver_warnings


def test_ttl_refresh_with_unchanged_served_list_warns_once(monkeypatch, caplog):
    """The served-list TTL makes the resolution recompute every 300s. A
    config miss that has not changed is not news each time — warning on every
    refresh emitted ~288 identical WARNINGs a day."""
    served = ["nomic-embed-text-v1.5", "qwen2.5-7b-instruct-q4_k_m"]
    clock = {"t": 0.0}

    monkeypatch.setattr(mod.httpx, "get", lambda url, timeout: _FakeTagsResponse(served))
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock["t"])

    with caplog.at_level(logging.WARNING, logger="ai-companion.internal_llm"):
        assert mod.effective_local_model() == "qwen2.5-7b-instruct-q4_k_m"
        clock["t"] += 301.0
        assert mod.effective_local_model() == "qwen2.5-7b-instruct-q4_k_m"

    resolver_warnings = [
        r for r in caplog.records
        if r.name == "ai-companion.internal_llm" and r.levelno == logging.WARNING
    ]
    assert len(resolver_warnings) == 1


def test_ttl_refresh_warns_again_when_the_resolution_changes(monkeypatch, caplog):
    """Suppressing the repeat must not suppress a genuine change."""
    served = {"models": ["nomic-embed-text-v1.5", "qwen2.5-7b-instruct-q4_k_m"]}
    clock = {"t": 0.0}

    monkeypatch.setattr(
        mod.httpx, "get", lambda url, timeout: _FakeTagsResponse(served["models"])
    )
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock["t"])

    with caplog.at_level(logging.WARNING, logger="ai-companion.internal_llm"):
        assert mod.effective_local_model() == "qwen2.5-7b-instruct-q4_k_m"
        served["models"] = ["nomic-embed-text-v1.5", "mistral-7b-instruct"]
        clock["t"] += 301.0
        assert mod.effective_local_model() == "mistral-7b-instruct"

    resolver_warnings = [
        r for r in caplog.records
        if r.name == "ai-companion.internal_llm" and r.levelno == logging.WARNING
    ]
    assert len(resolver_warnings) == 2


# ---------------------------------------------------------------------------
# Review fix 3: the outgoing /api/chat payload carries the resolved name.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_ollama_payload_model_is_the_resolved_name(monkeypatch):
    """When the configured model isn't served, the /api/chat payload's
    ``model`` field must be the resolved name, not the raw config value."""
    captured: dict = {}

    async def _post(url, *, json):
        captured["model"] = json["model"]
        return _FakeChatResponse()

    async def _get(url, timeout=None):
        return _FakeTagsResponse(["nomic-embed-text-v1.5", "qwen2.5-7b-instruct-q4_k_m"])

    fake_client = MagicMock()
    fake_client.post = _post
    fake_client.get = _get
    monkeypatch.setattr(mod, "_get_ollama_client", AsyncMock(return_value=fake_client))
    monkeypatch.setattr(mod, "get_breaker", lambda _name: _PassThroughBreaker())
    monkeypatch.setenv("OLLAMA_URL", "http://test-host:11434")

    result = await mod._call_ollama(
        [{"role": "user", "content": "hi"}], temperature=0, max_tokens=5,
    )

    assert result == "ok"
    assert captured["model"] == "qwen2.5-7b-instruct-q4_k_m"


# ---------------------------------------------------------------------------
# Task 1 (2026-09-07 close-out): the gateway's own chat-slot model
# (quenchforge `GET /` -> slots.chat.model) is preferred over an arbitrary
# alphabetically-first served model when the configured name isn't served.
# ---------------------------------------------------------------------------


def _resolve(served, previous, chat_slot):
    mod._gateway_chat_slot_model_cache = chat_slot
    return mod._resolve_effective_local_model(served, previous)


def test_configured_model_wins_when_served(monkeypatch):
    monkeypatch.setattr(mod.config, "INTERNAL_LLM_MODEL", "qwen2.5-7b-instruct-q4_k_m", raising=False)
    assert _resolve(
        ["qwen2.5-3b-instruct-q4_k_m", "qwen2.5-7b-instruct-q4_k_m"], None, "qwen2.5-7b-instruct-q4_k_m"
    ) == "qwen2.5-7b-instruct-q4_k_m"


def test_unserved_configured_falls_back_to_chat_slot_model(monkeypatch, caplog):
    monkeypatch.setattr(mod.config, "INTERNAL_LLM_MODEL", "llama3.1-8b", raising=False)
    with caplog.at_level(logging.WARNING, logger=mod.logger.name):
        got = _resolve(
            ["qwen2.5-3b-instruct-q4_k_m", "qwen2.5-7b-instruct-q4_k_m"], None, "qwen2.5-7b-instruct-q4_k_m"
        )
    assert got == "qwen2.5-7b-instruct-q4_k_m"
    assert "chat-slot model" in caplog.text


def test_still_served_previous_resolution_stays_put(monkeypatch):
    monkeypatch.setattr(mod.config, "INTERNAL_LLM_MODEL", "llama3.1-8b", raising=False)
    served = ["qwen2.5-3b-instruct-q4_k_m", "qwen2.5-7b-instruct-q4_k_m"]
    assert _resolve(served, "qwen2.5-7b-instruct-q4_k_m", None) == "qwen2.5-7b-instruct-q4_k_m"
    assert _resolve(served, "gone-model", None) == "qwen2.5-3b-instruct-q4_k_m"


def test_alphabetical_fallback_only_without_a_chat_slot_answer(monkeypatch):
    monkeypatch.setattr(mod.config, "INTERNAL_LLM_MODEL", "llama3.1-8b", raising=False)
    assert _resolve(
        ["qwen2.5-3b-instruct-q4_k_m", "qwen2.5-7b-instruct-q4_k_m"], None, None
    ) == "qwen2.5-3b-instruct-q4_k_m"


def test_chat_slot_model_must_itself_be_served(monkeypatch):
    monkeypatch.setattr(mod.config, "INTERNAL_LLM_MODEL", "llama3.1-8b", raising=False)
    assert _resolve(
        ["qwen2.5-3b-instruct-q4_k_m"], None, "qwen2.5-7b-instruct-q4_k_m"
    ) == "qwen2.5-3b-instruct-q4_k_m"


class _FakeRootResponse:
    def __init__(self, chat_model: str | None, status_code: int = 200) -> None:
        self._chat_model = chat_model
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=MagicMock(), response=self)

    def json(self) -> dict:
        slots = {"chat": {"configured": True, "url": "http://gw:11434"}}
        if self._chat_model is not None:
            slots["chat"]["model"] = self._chat_model
        return {"slots": slots}


def test_sync_fetch_populates_chat_slot_cache_from_root_payload(monkeypatch):
    def _get(url, timeout):
        if url.endswith("/api/tags"):
            return _FakeTagsResponse(["qwen2.5-7b-instruct-q4_k_m"])
        return _FakeRootResponse("qwen2.5-7b-instruct-q4_k_m")

    monkeypatch.setattr(mod.httpx, "get", _get)
    mod._fetch_served_models()
    assert mod._gateway_chat_slot_model_cache == "qwen2.5-7b-instruct-q4_k_m"


@pytest.mark.asyncio
async def test_async_fetch_populates_chat_slot_cache_from_root_payload(monkeypatch):
    async def _get(url, timeout=None):
        if url.endswith("/api/tags"):
            return _FakeTagsResponse(["qwen2.5-7b-instruct-q4_k_m"])
        return _FakeRootResponse("qwen2.5-7b-instruct-q4_k_m")

    fake_client = MagicMock()
    fake_client.get = _get
    monkeypatch.setattr(mod, "_get_ollama_client", AsyncMock(return_value=fake_client))
    await mod._fetch_served_models_async()
    assert mod._gateway_chat_slot_model_cache == "qwen2.5-7b-instruct-q4_k_m"


def test_root_fetch_failure_leaves_chat_slot_cache_and_served_list_intact(monkeypatch):
    """A gateway with no root route (older quenchforge, Ollama) must not
    disturb the served list the tags fetch just succeeded at, nor clobber
    a chat-slot name learned on a previous, luckier fetch."""
    mod._gateway_chat_slot_model_cache = "qwen2.5-7b-instruct-q4_k_m"

    def _get(url, timeout):
        if url.endswith("/api/tags"):
            return _FakeTagsResponse(["qwen2.5-7b-instruct-q4_k_m"])
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(mod.httpx, "get", _get)
    served, _ts = mod._fetch_served_models()
    assert served == ["qwen2.5-7b-instruct-q4_k_m"]
    assert mod._gateway_chat_slot_model_cache == "qwen2.5-7b-instruct-q4_k_m"


def test_served_names_drop_cached_but_unloaded_models():
    payload = {"models": [
        {"name": "qwen2.5-7b-instruct-q4_k_m", "loaded": True},
        {"name": "qwen2.5-3b-instruct-q4_k_m", "loaded": False},
        {"name": "nomic-embed-text-v1.5"},
    ]}
    assert mod._served_names(payload) == ["qwen2.5-7b-instruct-q4_k_m", "nomic-embed-text-v1.5"]

