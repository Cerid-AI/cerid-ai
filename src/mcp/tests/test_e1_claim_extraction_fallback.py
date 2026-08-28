# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""E1 Phase-5 — claim-extraction external fallback leads with a DISTINCT model (CR-071).

Registry: ``docs/superpowers/specs/2026-07-17-audit-e1-findings-registry.jsonl``
(CR-071). The external fallback chain's first entry was config.LLM_INTERNAL_MODEL —
the INTERNAL model, already attempted via call_internal_llm and possibly a bare
local name — so the first "fallback" re-tried the model that had just failed.
The fix leads with CATEGORIZE_MODELS["smart"] instead. RED-then-GREEN.

Originally this also asserted the lead entry carried a ":free" suffix, because
the fix of the day pointed it at the free Llama 3.3 slug. OpenRouter retired
that slug on 2026-08-27 (404: "This model is unavailable for free"), and the
remaining :free pool is shared and rate-limited, so no reliable free entry
exists to lead with. That assertion is dropped rather than re-pointed at
another :free slug, which would only trade a 404 for a 429.

The regression CR-071 actually guarded is unchanged and still pinned: the first
external attempt must not be the model call_internal_llm already tried.

The second test pins the property that makes a chain a fallback at all — that it
spans more than one provider. Nothing asserted that until 2026-08-28, and in the
meantime the chain quietly went from three attempts to two: the retarget of
CATEGORIZE_MODELS["smart"] onto gemini-3.1-flash-lite made it identical to the
cheap-tier diversity slot, which then deduplicated away. Two providers is still
a real fallback, so that is what is pinned; a collapse to one is not, and would
mean an OpenAI or Google outage takes claim extraction down entirely.
"""
from __future__ import annotations

import httpx
import pytest


@pytest.mark.asyncio
async def test_cr071_external_chain_leads_with_a_model_not_already_tried(monkeypatch):
    import config
    import core.agents.hallucination.extraction as ext

    async def _internal_fail(*_a, **_k):
        raise RuntimeError("internal LLM down")

    monkeypatch.setattr(ext, "call_internal_llm", _internal_fail)

    seen: list[str] = []

    async def _capture(_messages, *, model, **_k):
        seen.append(model)
        return '{"claims": ["The sky is blue."]}'

    monkeypatch.setattr(ext, "call_llm", _capture)

    claims = await ext._extract_claims_llm("The sky is blue today outside.", 5)

    assert claims == ["The sky is blue."]
    # The first external fallback is the categorization model, NOT the internal
    # model that call_internal_llm just tried and failed on. That is the whole
    # of CR-071 and it is what must never regress.
    assert seen[0] == config.CATEGORIZE_MODELS["smart"]
    internal = getattr(config, "LLM_INTERNAL_MODEL", None)
    if internal:
        assert seen[0] != internal, "chain must not lead with the already-tried internal model"


def _provider_of(model_id: str) -> str:
    """`openrouter/google/gemini-3.1-flash-lite` -> `google`.

    Falls back to the whole id for shapes that carry no provider segment (a bare
    local model name, say), so an unrecognised entry counts as its own provider
    rather than silently collapsing into another.
    """
    parts = model_id.split("/")
    return parts[1] if len(parts) >= 3 else model_id


@pytest.mark.asyncio
async def test_cr071_external_chain_spans_more_than_one_provider(monkeypatch):
    import core.agents.hallucination.extraction as ext

    async def _internal_fail(*_a, **_k):
        raise RuntimeError("internal LLM down")

    monkeypatch.setattr(ext, "call_internal_llm", _internal_fail)

    seen: list[str] = []

    # httpx.ConnectError, not a bare RuntimeError: the loop advances only on
    # the failures it recognises as retriable, and an unexpected exception is
    # deliberately allowed to propagate rather than be swallowed. A provider
    # outage — the thing provider diversity exists to survive — arrives as a
    # connect error, so that is the fault to inject.
    async def _capture_and_fail(_messages, *, model, **_k):
        seen.append(model)
        raise httpx.ConnectError(f"{model} unavailable")

    monkeypatch.setattr(ext, "call_llm", _capture_and_fail)

    await ext._extract_claims_llm("The sky is blue today outside.", 5)

    assert seen, "every external model failed without a single attempt being made"
    assert len(seen) == len(set(seen)), f"chain re-tries the same model: {seen}"

    providers = {_provider_of(m) for m in seen}
    assert len(providers) > 1, (
        "the external fallback chain resolved to a single provider "
        f"({providers} from {seen}). Retrying one provider is not a fallback: "
        "if it is degraded, every attempt fails together."
    )
