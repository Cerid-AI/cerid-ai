# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Task 12: claims with cited URLs must be verified against the cited URL
body first, not re-searched from claim text alone.

Audit finding V-3: the hallucination verifier ignored ``source_breakdown.external``
and re-searched from claim text. A fabricated citation (e.g. "According to
https://wikipedia.org/foo, the sky is green") would get "confirmed" against an
unrelated web-search result rather than being flagged as unsupported.

Fix: when ``source_urls`` is provided, fetch the cited page and NLI-entail the
claim against its body BEFORE considering KB / external fallback paths.
"""
from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import AsyncMock, patch

import pytest

# Mirror the stub setup used in test_hallucination.py so patch targets resolve
# without triggering real (heavy) imports at module import time.
if "agents.query_agent" not in sys.modules:
    _stub = ModuleType("agents.query_agent")
    _stub.agent_query = None  # type: ignore[attr-defined]
    _stub.lightweight_kb_query = None  # type: ignore[attr-defined]
    sys.modules["agents.query_agent"] = _stub
    import agents
    agents.query_agent = _stub  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_cited_url_is_checked_before_web_search(
    mock_chroma, mock_neo4j, mock_redis,
):
    """When a claim has a ``source_urls`` list, the cited URL is fetched and
    NLI-entailed against its body BEFORE KB lookup or external fallback.

    Verifies the short-circuit fires — neither the KB query nor the external
    web-search path should execute on the happy cited-URL path.
    """
    from core.agents.hallucination import verification

    kb_spy = AsyncMock(return_value=[])
    external_spy = AsyncMock(return_value={
        "status": "uncertain", "confidence": 0.2,
        "reason": "unused", "verification_method": "cross_model",
    })
    cited_url_spy = AsyncMock(return_value={
        "status": "verified", "similarity": 0.9,
        "verification_method": "cited_url",
        "verification_model": "nli-onnx",
        "source_urls": ["https://en.wikipedia.org/wiki/Paris"],
        "reasoning": "NLI entailment 0.90",
    })

    with patch("core.agents.query_agent.lightweight_kb_query", kb_spy), \
         patch.object(verification, "_verify_claim_externally", external_spy), \
         patch.object(verification, "_verify_against_cited_url", cited_url_spy):
        verdict = await verification.verify_claim(
            claim="Paris is the capital of France",
            chroma_client=mock_chroma[0],
            neo4j_driver=mock_neo4j[0],
            redis_client=mock_redis,
            source_urls=["https://en.wikipedia.org/wiki/Paris"],
        )

    cited_url_spy.assert_called_once()
    kb_spy.assert_not_called()
    external_spy.assert_not_called()
    assert verdict["status"] == "verified"
    assert verdict["verification_method"] == "cited_url"


@pytest.mark.asyncio
async def test_cited_url_failure_falls_back_to_kb_and_web_search(
    mock_chroma, mock_neo4j, mock_redis,
):
    """If fetching the cited URL fails (timeout, 404, NLI inconclusive),
    fall back to the existing KB + web_search path."""
    from core.agents.hallucination import verification

    kb_spy = AsyncMock(return_value=[])  # empty KB → forces external path
    external_spy = AsyncMock(return_value={
        "status": "verified", "confidence": 0.85,
        "reason": "confirmed by cross-model",
        "verification_method": "cross_model",
        "source_urls": [],
    })
    cited_url_spy = AsyncMock(side_effect=TimeoutError("fetch timed out"))

    with patch("core.agents.query_agent.lightweight_kb_query", kb_spy), \
         patch.object(verification, "_verify_claim_externally", external_spy), \
         patch.object(verification, "_verify_against_cited_url", cited_url_spy), \
         patch("core.agents.hallucination.verification._query_memories",
               new_callable=AsyncMock, return_value=[]):
        verdict = await verification.verify_claim(
            claim="xyz obscure claim",
            chroma_client=mock_chroma[0],
            neo4j_driver=mock_neo4j[0],
            redis_client=mock_redis,
            source_urls=["https://example.com/404"],
        )

    cited_url_spy.assert_called_once()
    # Fell through to the regular path (KB then external)
    kb_spy.assert_called_once()
    external_spy.assert_called_once()
    assert verdict["status"] == "verified"


@pytest.mark.asyncio
async def test_claim_without_source_urls_skips_cited_path(
    mock_chroma, mock_neo4j, mock_redis,
):
    """No ``source_urls`` → existing KB + web_search path runs normally,
    cited-URL verifier is never invoked."""
    from core.agents.hallucination import verification

    kb_spy = AsyncMock(return_value=[])
    external_spy = AsyncMock(return_value={
        "status": "verified", "confidence": 0.8,
        "reason": "cross-model agrees",
        "verification_method": "cross_model",
        "source_urls": [],
    })
    cited_url_spy = AsyncMock()  # must never be called

    with patch("core.agents.query_agent.lightweight_kb_query", kb_spy), \
         patch.object(verification, "_verify_claim_externally", external_spy), \
         patch.object(verification, "_verify_against_cited_url", cited_url_spy), \
         patch("core.agents.hallucination.verification._query_memories",
               new_callable=AsyncMock, return_value=[]):
        verdict = await verification.verify_claim(
            claim="something bare",
            chroma_client=mock_chroma[0],
            neo4j_driver=mock_neo4j[0],
            redis_client=mock_redis,
        )

    cited_url_spy.assert_not_called()
    kb_spy.assert_called_once()
    external_spy.assert_called_once()
    assert verdict["status"] == "verified"


@pytest.mark.asyncio
async def test_cited_url_contradiction_marks_unverified(
    mock_chroma, mock_neo4j, mock_redis,
):
    """If the cited URL's body contradicts the claim (NLI contradiction high),
    the verdict is ``unverified`` — do NOT fall through to KB/web search and
    accidentally confirm a fabricated citation.
    """
    from core.agents.hallucination import verification

    kb_spy = AsyncMock(return_value=[])
    external_spy = AsyncMock()  # must never be called
    cited_url_spy = AsyncMock(return_value={
        "status": "unverified", "similarity": 0.05,
        "verification_method": "cited_url",
        "verification_model": "nli-onnx",
        "source_urls": ["https://en.wikipedia.org/wiki/Sky"],
        "reasoning": "NLI contradiction 0.88",
    })

    with patch("core.agents.query_agent.lightweight_kb_query", kb_spy), \
         patch.object(verification, "_verify_claim_externally", external_spy), \
         patch.object(verification, "_verify_against_cited_url", cited_url_spy):
        verdict = await verification.verify_claim(
            claim="The sky is green",
            chroma_client=mock_chroma[0],
            neo4j_driver=mock_neo4j[0],
            redis_client=mock_redis,
            source_urls=["https://en.wikipedia.org/wiki/Sky"],
        )

    cited_url_spy.assert_called_once()
    kb_spy.assert_not_called()
    external_spy.assert_not_called()
    assert verdict["status"] == "unverified"


@pytest.mark.asyncio
async def test_cited_url_uncertain_falls_through():
    """If the cited URL body NLI-scores as ``uncertain`` (neither strong
    entailment nor strong contradiction), fall through to the existing
    KB + web_search path so we still produce a verdict."""
    from core.agents.hallucination import verification
    from unittest.mock import MagicMock

    mock_chroma_client = MagicMock()
    mock_neo4j_driver = MagicMock()
    mock_redis_client = MagicMock()

    kb_spy = AsyncMock(return_value=[])
    external_spy = AsyncMock(return_value={
        "status": "verified", "confidence": 0.9,
        "reason": "cross-model confirmed",
        "verification_method": "cross_model",
        "source_urls": [],
    })
    cited_url_spy = AsyncMock(return_value={
        "status": "uncertain", "similarity": 0.4,
        "verification_method": "cited_url",
        "verification_model": "nli-onnx",
        "source_urls": ["https://example.com/foo"],
        "reasoning": "NLI neutral",
    })

    with patch("core.agents.query_agent.lightweight_kb_query", kb_spy), \
         patch.object(verification, "_verify_claim_externally", external_spy), \
         patch.object(verification, "_verify_against_cited_url", cited_url_spy), \
         patch("core.agents.hallucination.verification._query_memories",
               new_callable=AsyncMock, return_value=[]):
        verdict = await verification.verify_claim(
            claim="some claim",
            chroma_client=mock_chroma_client,
            neo4j_driver=mock_neo4j_driver,
            redis_client=mock_redis_client,
            source_urls=["https://example.com/foo"],
        )

    cited_url_spy.assert_called_once()
    # Uncertain from cited URL → fall through to normal path
    kb_spy.assert_called_once()
    external_spy.assert_called_once()
    assert verdict["status"] == "verified"
