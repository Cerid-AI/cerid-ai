# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Authoritative-source evidence says what was actually measured.

``verify_claim_authoritatively`` runs for real against a fake data-source
registry and a fake NLI scorer. The verdict tests drive ``_verify_claim`` down
to its last fallback with the KB and cross-model stages stubbed, so the
authoritative stage (real) decides the verdict.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from core.agents.hallucination import authoritative_verify, verification
from core.agents.hallucination.authoritative_verify import verify_claim_authoritatively

_CLAIM = "Aspirin inhibits cyclooxygenase enzymes."
_SUPPORT = "Aspirin irreversibly inhibits the cyclooxygenase enzymes COX-1 and COX-2."
_UNRELATED = "Willow bark has been used in folk medicine for centuries."
_CONTRA = "Aspirin has no effect on cyclooxygenase enzymes."


class _Registry:
    def __init__(self, results: list[dict[str, Any]] | None = None, error: BaseException | None = None):
        self.results = results or []
        self.error = error

    async def query_all(self, search_terms: str, **_kw: Any) -> list[dict[str, Any]]:
        if self.error is not None:
            raise self.error
        return self.results


def _src(name: str, content: str, **extra: Any) -> dict[str, Any]:
    return {"source": name, "content": content, "source_url": f"https://{name}.example/x", **extra}


def _nli(entailment: float, contradiction: float) -> dict[str, Any]:
    return {
        "entailment": entailment, "contradiction": contradiction,
        "neutral": max(0.0, 1.0 - entailment - contradiction), "label": "x",
    }


def _scorer(table: dict[str, Any], calls: list[tuple[str, str]] | None = None):
    """NLI keyed on the premise text; raises for premises mapped to an exception."""
    async def _score(premise: str, hypothesis: str) -> dict[str, Any]:
        if calls is not None:
            calls.append((premise, hypothesis))
        out = table.get(premise, _nli(0.1, 0.1))
        if isinstance(out, BaseException):
            raise out
        return out
    return _score


async def _run(registry: _Registry, score, kb: str | None = None) -> dict[str, Any]:
    with patch("core.utils.nli.nli_score_async", side_effect=score):
        return await verify_claim_authoritatively(
            _CLAIM, kb_results=[{"content": kb}] if kb else None, registry=registry,
        )


# ── F52: a failed source query is not "no results" ─────────────────────────

@pytest.mark.parametrize("error, name", [
    (RuntimeError("network down"), "RuntimeError"),
    (TimeoutError(), "TimeoutError"),
])
async def test_a_failed_source_query_is_reported_as_a_failure(error, name):
    result = await _run(_Registry(error=error), _scorer({}))

    assert result["authoritative_sources"] == []
    assert result["query_error"] == name
    assert "failed" in result["evidence_summary"]
    assert "No authoritative sources returned results" not in result["evidence_summary"]


async def test_an_empty_answer_is_still_no_results():
    result = await _run(_Registry(results=[]), _scorer({}))

    assert "query_error" not in result
    assert result["evidence_summary"] == "No authoritative sources returned results."


# ── F32 / F33: unscored sources are marked and never count as evidence ─────

async def test_sources_nli_could_not_score_are_marked_unscored():
    boom = RuntimeError("nli down")
    result = await _run(
        _Registry(results=[_src("wiki", _SUPPORT), _src("pubchem", _UNRELATED)]),
        _scorer({_SUPPORT: boom, _UNRELATED: boom}),
    )

    sources = result["authoritative_sources"]
    assert [s["nli_scored"] for s in sources] == [False, False]
    assert "none could be NLI-scored" in result["evidence_summary"]
    assert "no strong entailment or contradiction" not in result["evidence_summary"]


async def test_a_partial_scoring_failure_neither_duplicates_nor_drops_scores():
    result = await _run(
        _Registry(results=[_src("wiki", _SUPPORT), _src("pubchem", _UNRELATED)]),
        _scorer({_SUPPORT: _nli(0.92, 0.01), _UNRELATED: RuntimeError("nli down")}),
    )

    sources = result["authoritative_sources"]
    assert [(s["source"], s["nli_scored"]) for s in sources] == [("wiki", True), ("pubchem", False)]
    assert sources[0]["nli_entailment"] == pytest.approx(0.92)
    assert result["evidence_summary"].startswith("1 authoritative source(s) support")
    assert "1 source(s) could not be NLI-scored" in result["evidence_summary"]


async def test_a_producer_supplied_score_does_not_pass_for_a_computed_one():
    result = await _run(
        _Registry(results=[_src("wiki", _SUPPORT, nli_entailment=0.97)]),
        _scorer({_SUPPORT: RuntimeError("nli down")}),
    )

    (source,) = result["authoritative_sources"]
    assert source["nli_scored"] is False
    assert source["nli_entailment"] == 0.0
    assert "support" not in result["evidence_summary"]


# ── F34: cross-validation compares against the source that bears on the claim

async def test_cross_validation_uses_the_source_that_bears_on_the_claim():
    kb = "COX inhibition is how aspirin reduces inflammation."
    calls: list[tuple[str, str]] = []
    result = await _run(
        _Registry(results=[_src("folk", _UNRELATED), _src("wiki", _SUPPORT)]),
        _scorer({_UNRELATED: _nli(0.05, 0.05), _SUPPORT: _nli(0.9, 0.02), kb: _nli(0.8, 0.05)}, calls),
        kb=kb,
    )

    cross_calls = [c for c in calls if c[0] == kb]
    assert cross_calls == [(kb, _SUPPORT)]
    assert result["cross_validation"]["compared_source"] == "wiki"
    assert result["cross_validation"]["kb_vs_external_agreement"] == pytest.approx(0.8)


async def test_cross_validation_that_cannot_run_says_why():
    kb = "COX inhibition is how aspirin reduces inflammation."
    calls: list[tuple[str, str]] = []
    result = await _run(
        _Registry(results=[_src("folk", _UNRELATED)]),
        _scorer({_UNRELATED: _nli(0.05, 0.05)}, calls),
        kb=kb,
    )

    assert not [c for c in calls if c[0] == kb]
    assert result["cross_validation"] == {"skipped": "no external source bears on the claim"}


# ── F53: named thresholds, and disagreement is reported as disagreement ─────

async def test_sources_that_disagree_are_summarised_as_disagreeing():
    result = await _run(
        _Registry(results=[_src("wiki", _SUPPORT), _src("blog", _CONTRA)]),
        _scorer({_SUPPORT: _nli(0.9, 0.02), _CONTRA: _nli(0.03, 0.9)}),
    )

    assert result["evidence_summary"].startswith("Authoritative sources disagree")


async def test_support_uses_the_verdict_entailment_threshold():
    import config

    below = round(config.NLI_ENTAILMENT_THRESHOLD - 0.05, 3)
    result = await _run(_Registry(results=[_src("wiki", _SUPPORT)]), _scorer({_SUPPORT: _nli(below, 0.02)}))

    assert "support" not in result["evidence_summary"]


# ── Verdicts from the authoritative fallback (F33, F106) ───────────────────

_KB_ROW = {
    "content": "Aspirin is a nonsteroidal anti-inflammatory drug.",
    "relevance": 0.55, "artifact_id": "art-1", "filename": "nsaids.md", "domain": "general",
}


async def _verdict_from_last_fallback(monkeypatch, registry: _Registry, score) -> dict[str, Any]:
    import config

    monkeypatch.setattr(config, "EXPERT_VERIFY_USE_AUTHORITATIVE_SOURCES", True)
    monkeypatch.setattr(config, "HALLUCINATION_THRESHOLD", 0.65)
    monkeypatch.setattr(config, "HALLUCINATION_UNVERIFIED_THRESHOLD", 0.4)
    monkeypatch.setattr(config, "EXTERNAL_VERIFY_KB_THRESHOLD", 0.5)
    monkeypatch.setattr(authoritative_verify, "_registry", registry)
    monkeypatch.setattr(verification, "get_cached_verdict", AsyncMock(return_value=None))
    monkeypatch.setattr(verification, "cache_verdict", AsyncMock())
    monkeypatch.setattr(verification, "_verify_via_cited_urls", AsyncMock(return_value=None))
    monkeypatch.setattr(verification, "_gather_kb_evidence", AsyncMock(return_value=[dict(_KB_ROW)]))
    monkeypatch.setattr(
        verification, "_score_kb_grounding",
        AsyncMock(return_value=(0.55, {"reason": "Partial match"}, _nli(0.3, 0.1))),
    )
    monkeypatch.setattr(
        verification, "_verify_claim_externally",
        AsyncMock(return_value={
            "status": "uncertain", "confidence": 0.4,
            "verification_method": "web_search", "source_urls": [],
        }),
    )
    with patch("core.utils.nli.nli_score_async", side_effect=score):
        return await verification._verify_claim(_CLAIM, None, None, None)


async def test_an_unscored_source_cannot_verify_a_claim(monkeypatch):
    result = await _verdict_from_last_fallback(
        monkeypatch,
        _Registry(results=[_src("wiki", _SUPPORT, nli_entailment=0.97)]),
        _scorer({_SUPPORT: RuntimeError("nli down")}),
    )

    assert result["status"] == "uncertain"
    assert result["verification_method"] == "kb"


async def test_a_scored_source_still_verifies_a_claim(monkeypatch):
    result = await _verdict_from_last_fallback(
        monkeypatch,
        _Registry(results=[_src("wiki", _SUPPORT)]),
        _scorer({_SUPPORT: _nli(0.91, 0.02)}),
    )

    assert result["status"] == "verified"
    assert result["verification_method"] == "authoritative"
    assert result["similarity"] == pytest.approx(0.91)


async def test_an_authoritative_contradiction_carries_its_measured_score(monkeypatch):
    result = await _verdict_from_last_fallback(
        monkeypatch,
        _Registry(results=[_src("blog", _CONTRA)]),
        _scorer({_CONTRA: _nli(0.04, 0.88)}),
    )

    assert result["status"] == "unverified"
    assert result["verification_method"] == "authoritative"
    assert result["similarity"] == pytest.approx(0.04)


# ── F54: a cited source gets a citation verdict, not a recency one ─────────

def _llm_says(verdict: str, reasoning: str) -> dict[str, Any]:
    content = f'{{"verdict": "{verdict}", "confidence": 0.9, "reasoning": "{reasoning}"}}'
    return {"choices": [{"message": {"content": content, "annotations": []}}]}


@pytest.mark.parametrize("llm_verdict, status, label", [
    ("refuted", "unverified", "Cited source appears fabricated"),
    ("supported", "verified", "Cited source exists"),
])
async def test_a_citation_verdict_speaks_about_the_source(monkeypatch, llm_verdict, status, label):
    monkeypatch.setattr(verification, "_independent_search_evidence_urls", AsyncMock(return_value=[]))
    with patch(
        "core.utils.llm_client.call_llm_raw",
        new_callable=AsyncMock, return_value=_llm_says(llm_verdict, "searched the journal index"),
    ):
        result = await verification._verify_claim_externally(
            "[CITATION] Journal of Imaginary Results (2021)",
            generating_model="openai/gpt-4o-mini",
        )

    assert result["status"] == status
    assert result["reason"] == f"{label}: searched the journal index"
