# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""E1 Phase-5 — claim-extraction external fallback leads with a free model (CR-071).

Registry: ``docs/superpowers/specs/2026-07-17-audit-e1-findings-registry.jsonl``
(CR-071). The external fallback chain's first entry was config.LLM_INTERNAL_MODEL —
the INTERNAL model, already attempted via call_internal_llm and possibly a bare
local name — mislabeled as the free tier, so "try free models first" did not hold.
The fix leads with the genuinely-free ":free" Llama slug. RED-then-GREEN.
"""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_cr071_external_chain_leads_with_free_model(monkeypatch):
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
    # The first external fallback is the genuinely-free ":free" model, not the
    # already-tried internal model mislabeled as free.
    assert seen[0] == config.CATEGORIZE_MODELS["smart"]
    assert ":free" in seen[0]
