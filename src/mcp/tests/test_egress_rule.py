# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Under the local-only profile nothing leaves the host (audit S10-3).

The rule is one function (``core.routing.egress``); these tests patch the HTTP
transport, never the rule, so each chokepoint is shown refusing before a byte
is sent, and each "allowed" control shows the transport patch would have seen
the send.
"""
from __future__ import annotations

import httpx
import pytest

import config
from core.routing import egress
from core.routing.egress import EgressRefused


class _Sent(Exception):
    """Raised by the patched transport: a request reached the wire."""


@pytest.fixture
def wire(monkeypatch):
    sent: list[str] = []

    async def send(self, request, *a, **k):
        sent.append(str(request.url))
        raise _Sent(str(request.url))

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")  # pragma: allowlist secret
    return sent


@pytest.fixture
def local_only(monkeypatch):
    monkeypatch.setattr(config, "ALLOW_CLOUD_EGRESS_WHEN_LOCAL", False)


@pytest.fixture
def cloud_allowed(monkeypatch):
    monkeypatch.setattr(config, "CERID_ENVIRONMENT_PROFILE", "", raising=False)
    monkeypatch.setattr(config, "ALLOW_CLOUD_EGRESS_WHEN_LOCAL", True)


@pytest.mark.parametrize(
    ("profile", "flag", "refused"),
    [("", True, False), ("hybrid", True, False), ("", False, True), ("local-only", True, True), ("local-only", False, True)],
)
def test_the_rule_reads_the_profile_and_its_flag(monkeypatch, profile, flag, refused):
    monkeypatch.setattr(config, "CERID_ENVIRONMENT_PROFILE", profile, raising=False)
    monkeypatch.setattr(config, "ALLOW_CLOUD_EGRESS_WHEN_LOCAL", flag)
    assert bool(egress.cloud_egress_refused()) is refused


@pytest.mark.asyncio
@pytest.mark.parametrize("fn", ["call_llm", "call_llm_raw"])
async def test_cloud_llm_calls_refuse_before_sending(local_only, wire, fn):
    from core.utils import llm_client

    with pytest.raises(EgressRefused):
        await getattr(llm_client, fn)([{"role": "user", "content": "a private claim"}], model="openai/gpt-6-luna")
    assert wire == []


@pytest.mark.asyncio
async def test_cloud_llm_call_reaches_the_wire_when_allowed(cloud_allowed, wire):
    from core.utils.llm_client import call_llm_raw

    with pytest.raises(Exception):  # noqa: B017 — the patched transport's refusal, however the client wraps it
        await call_llm_raw([{"role": "user", "content": "q"}], model="openai/gpt-6-luna")
    assert wire and "openrouter" in wire[0]


def _registry_with_wikipedia():
    from app.data_sources.base import DataSourceRegistry
    from app.data_sources.wikipedia import WikipediaSource

    reg = DataSourceRegistry()
    reg.register(WikipediaSource())
    return reg


@pytest.mark.asyncio
async def test_external_sources_send_nothing(local_only, wire):
    reg = _registry_with_wikipedia()

    assert reg.get_enabled_sources() == []
    assert reg.has_enabled_sources() is False
    assert await reg.query_all("a private question") == []
    assert wire == []


@pytest.mark.asyncio
async def test_external_sources_reach_the_wire_when_allowed(cloud_allowed, wire):
    reg = _registry_with_wikipedia()

    await reg.query_all("photosynthesis")
    assert any("wikipedia" in u for u in wire)


@pytest.mark.asyncio
async def test_web_search_says_it_is_off(local_only, wire):
    from utils.web_search import search_and_verify

    out = await search_and_verify("a private question")

    assert out["results"] == [] and "local-only" in out["error"]
    assert wire == []


def test_chat_refuses_a_cloud_model_and_keeps_a_local_one(local_only, monkeypatch):
    from app.routers import chat

    monkeypatch.setenv("INTERNAL_LLM_PROVIDER", "ollama")
    with pytest.raises(EgressRefused):
        chat._resolve_chat_dispatch("openai/gpt-6-luna", "sk-test")  # pragma: allowlist secret
    base, _key, model, _wire = chat._resolve_chat_dispatch("ollama/gemma-4-26b-a4b", "")
    assert base.endswith("/v1") and model == "gemma-4-26b-a4b"
    assert chat._pick_fallback("ollama/gemma-4-26b-a4b") is None


def test_chat_still_dispatches_to_the_cloud_when_allowed(cloud_allowed):
    from app.routers import chat

    base, _key, _model, _wire = chat._resolve_chat_dispatch("openai/gpt-6-luna", "sk-test")  # pragma: allowlist secret
    assert "openrouter" in base
    assert chat._pick_fallback("openai/gpt-6-luna") is not None


def test_cloud_stages_resolve_local_under_the_rule(local_only):
    from core.utils.internal_llm import _cloud_fallback_refused_by, _resolve_stage_provider

    assert _resolve_stage_provider("inbox_triage_escalate", "ollama") != "openrouter"
    assert "local-only" in _cloud_fallback_refused_by("claim_extraction")


def test_cloud_stages_still_escalate_when_allowed(cloud_allowed):
    from core.utils.internal_llm import _resolve_stage_provider

    assert _resolve_stage_provider("inbox_triage_escalate", "ollama") == "openrouter"


@pytest.mark.asyncio
async def test_verification_does_not_fetch_cited_pages(local_only, wire):
    from core.agents.hallucination.verification import _verify_via_cited_urls

    assert await _verify_via_cited_urls("a claim", ["https://example.com/page"], None) is None
    assert wire == []


@pytest.mark.asyncio
async def test_claim_extraction_does_not_fall_back_to_cloud_models(local_only, wire, monkeypatch):
    from core.agents.hallucination import extraction

    async def local_down(*a, **k):
        raise RuntimeError("local model server down")

    monkeypatch.setattr("core.utils.internal_llm.call_internal_llm", local_down)
    assert await extraction._extract_claims_llm("The sky is green. Water is dry.", 5) == []
    assert wire == []
