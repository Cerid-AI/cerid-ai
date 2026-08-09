"""Citation binding in ``pkb_answer_with_citations``.

Regression guard for the 2026-07-29 GA audit finding: the binder read
``r["text"]`` while the query envelope emits ``content``, so every claim
was silently marked unsupported and ``citations`` was always empty.

The pre-existing coverage in ``test_chunks_per_answer_metric.py`` missed
this because its fixtures used ``"text"`` keys and stubbed ``extract_claims``
to ``[]`` — the binding loop never ran against the real shape. These tests
use the envelope shape returned by a live ``/agent/query``.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest


def _envelope_results():
    """Result items in the shape ``agent_query`` actually returns.

    Key detail: chunk text lives under ``content``. Verified against a live
    stack — result keys include ``content``, never ``text``.
    """
    return [
        {
            "content": "The Cerid AI retrieval pipeline uses a cross-encoder "
                       "model to rerank candidate chunks before synthesis.",
            "artifact_id": "art-1",
            "chunk_id": "chunk-1",
        },
        {
            "content": "Unrelated filler about scheduling and backups.",
            "artifact_id": "art-2",
            "chunk_id": "chunk-2",
        },
    ]


def _patch_answer_path(monkeypatch, claims):
    import app.mcp_tools.retrieval as retrieval_mod
    from core.retrieval.surface_router import SurfaceRoute

    route = SurfaceRoute(
        primary="vector",
        surfaces=["vector"],
        intent="factual",
        confidence=1.0,
        matched_entity_hint=None,
    )
    monkeypatch.setattr("core.retrieval.surface_router.route", lambda _q: route)
    monkeypatch.setattr(
        "core.agents.query_agent.agent_query",
        AsyncMock(return_value={
            "results": _envelope_results(),
            "context": "ctx",
            "total_results": 2,
        }),
    )
    monkeypatch.setattr(
        "core.utils.internal_llm.call_internal_llm",
        AsyncMock(return_value="an answer"),
    )
    monkeypatch.setattr(
        "core.agents.hallucination.extraction.extract_claims",
        AsyncMock(return_value=(claims, "stub")),
    )
    monkeypatch.setattr(retrieval_mod, "get_redis", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(retrieval_mod, "get_chroma", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(retrieval_mod, "get_neo4j", MagicMock(return_value=MagicMock()))
    return retrieval_mod


@pytest.mark.asyncio
async def test_claim_grounded_in_a_chunk_is_cited(monkeypatch):
    """A claim lifted verbatim from a chunk must bind to that chunk."""
    claim = "The Cerid AI retrieval pipeline uses a cross-encoder model"
    retrieval_mod = _patch_answer_path(monkeypatch, [claim])

    out = await retrieval_mod.pkb_answer_with_citations("how does reranking work?")

    assert out["citations"], (
        "verbatim-from-chunk claim produced no citation — the binder is "
        "reading a key the envelope does not emit"
    )
    assert out["unsupported_claims"] == []

    cite = out["citations"][0]
    assert cite["source"]["artifact_id"] == "art-1"
    assert cite["source"]["chunk_id"] == "chunk-1"
    # The snippet must carry real text, not an empty string.
    assert cite["source"]["text_snippet"].strip()
    assert "cross-encoder" in cite["source"]["text_snippet"]


@pytest.mark.asyncio
async def test_ungrounded_claim_is_reported_unsupported(monkeypatch):
    """The unsupported path must still work — this is not 'cite everything'."""
    claim = "Neptune has diamond rain and seventeen distinct moons"
    retrieval_mod = _patch_answer_path(monkeypatch, [claim])

    out = await retrieval_mod.pkb_answer_with_citations("tell me about Neptune")

    assert out["citations"] == []
    assert claim in out["unsupported_claims"]
