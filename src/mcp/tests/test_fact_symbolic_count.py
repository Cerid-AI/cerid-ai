# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Phase F — the symbolic :Fact-count seam in pkb_answer_with_citations.

The seam's LOGIC is tested here (slug candidates, the exactly-one resolution
gate, the flag-gated fall-through); the graph readers' SEMANTICS are tested in
test_fact_queries.py. The two graph readers the seam consumes
(subjects_with_current_facts / count_facts) are stubbed so these tests pin the
seam's conservatism, not Cypher: flag OFF is byte-identical to the text path,
flag ON answers only with genuine graph support, and every ambiguous / empty /
error case falls through silently.
"""
from __future__ import annotations

from typing import get_args
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.mcp_tools import retrieval as seam
from app.mcp_tools.retrieval import (
    _ENTITY_TYPE_PREFIXES,
    _aggregation_answer,
    _candidate_slugs,
    _resolve_count_subject,
    _symbolic_count_answer,
)
from core.agents.entity_extraction import EntityType

_DRIVER = object()  # opaque non-None sentinel — graph readers are stubbed


# ---------------------------------------------------------------------------
# Type-prefix drift guard
# ---------------------------------------------------------------------------


def test_entity_type_prefixes_match_canonical_source() -> None:
    assert set(_ENTITY_TYPE_PREFIXES) == {t.lower() for t in get_args(EntityType)}


# ---------------------------------------------------------------------------
# _candidate_slugs — deterministic unigram + bigram slugging, stopwords dropped
# ---------------------------------------------------------------------------


def test_candidate_slugs_unigrams_and_bigrams() -> None:
    slugs = _candidate_slugs("How many times did I attend the yoga class?")
    assert "yoga-class" in slugs      # bigram of the content words
    assert "yoga" in slugs and "class" in slugs
    assert "attend-yoga" in slugs     # adjacent bigram
    # Interrogative / counting / function words never become subject slugs.
    for dropped in ("how", "many", "the", "did", "i", "times"):
        assert dropped not in slugs


def test_candidate_slugs_deterministic_and_deduped() -> None:
    q = "how many yoga yoga sessions"
    assert _candidate_slugs(q) == _candidate_slugs(q)          # deterministic
    assert len(_candidate_slugs(q)) == len(set(_candidate_slugs(q)))  # deduped


def test_candidate_slugs_empty_when_all_stopwords() -> None:
    assert _candidate_slugs("how many of the?") == []


# ---------------------------------------------------------------------------
# _resolve_count_subject — exactly-one gate
# ---------------------------------------------------------------------------


def _patch_subjects(monkeypatch, matched: set[str]) -> MagicMock:
    stub = MagicMock(return_value=matched)
    monkeypatch.setattr(
        "app.db.neo4j.fact_queries.subjects_with_current_facts", stub
    )
    return stub


def test_resolve_subject_exactly_one(monkeypatch) -> None:
    _patch_subjects(monkeypatch, {"other:yoga-class"})
    assert _resolve_count_subject(_DRIVER, "how many yoga class") == "other:yoga-class"


def test_resolve_subject_zero_matches_returns_none(monkeypatch) -> None:
    _patch_subjects(monkeypatch, set())
    assert _resolve_count_subject(_DRIVER, "how many yoga class") is None


def test_resolve_subject_ambiguous_returns_none(monkeypatch) -> None:
    _patch_subjects(monkeypatch, {"other:yoga-class", "other:gym"})
    assert _resolve_count_subject(_DRIVER, "how many yoga class gym") is None


def test_resolve_subject_candidate_ids_are_type_prefixed(monkeypatch) -> None:
    stub = _patch_subjects(monkeypatch, set())
    _resolve_count_subject(_DRIVER, "yoga class")
    (called_driver, candidate_ids), _ = stub.call_args
    assert called_driver is _DRIVER
    # Every candidate id is {type}:{slug}; the real subject id is a candidate.
    assert "other:yoga-class" in candidate_ids
    assert all(cid.split(":", 1)[0] in _ENTITY_TYPE_PREFIXES for cid in candidate_ids)


def test_resolve_subject_no_slugs_short_circuits(monkeypatch) -> None:
    stub = _patch_subjects(monkeypatch, {"other:x"})
    assert _resolve_count_subject(_DRIVER, "how many of the?") is None
    stub.assert_not_called()  # no candidates → never hits the graph


# ---------------------------------------------------------------------------
# _symbolic_count_answer — graph support required
# ---------------------------------------------------------------------------


def test_symbolic_count_with_graph_support(monkeypatch) -> None:
    _patch_subjects(monkeypatch, {"other:yoga-class"})
    monkeypatch.setattr("app.db.neo4j.fact_queries.count_facts", MagicMock(return_value=4))
    assert _symbolic_count_answer(_DRIVER, "how many yoga class") == "4"


def test_symbolic_count_none_driver() -> None:
    assert _symbolic_count_answer(None, "how many yoga class") is None


def test_symbolic_count_unresolved_subject(monkeypatch) -> None:
    _patch_subjects(monkeypatch, set())  # no match → resolution None
    count = MagicMock(return_value=9)
    monkeypatch.setattr("app.db.neo4j.fact_queries.count_facts", count)
    assert _symbolic_count_answer(_DRIVER, "how many yoga class") is None
    count.assert_not_called()  # never counts an unresolved subject


def test_symbolic_count_zero_facts_falls_through(monkeypatch) -> None:
    _patch_subjects(monkeypatch, {"other:yoga-class"})
    monkeypatch.setattr("app.db.neo4j.fact_queries.count_facts", MagicMock(return_value=0))
    assert _symbolic_count_answer(_DRIVER, "how many yoga class") is None


def test_symbolic_count_swallows_store_error(monkeypatch) -> None:
    _patch_subjects(monkeypatch, {"other:yoga-class"})
    monkeypatch.setattr(
        "app.db.neo4j.fact_queries.count_facts",
        MagicMock(side_effect=RuntimeError("neo4j down")),
    )
    # Any store error → None (best-effort; text path is the fallback).
    assert _symbolic_count_answer(_DRIVER, "how many yoga class") is None


# ---------------------------------------------------------------------------
# _aggregation_answer — flag gate + byte-identical fall-through
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aggregation_flag_off_is_text_path(monkeypatch) -> None:
    monkeypatch.setattr("config.features.ENABLE_FACT_SYMBOLIC_COUNT", False)
    symbolic_spy = MagicMock(return_value="999")
    monkeypatch.setattr(seam, "_symbolic_count_answer", symbolic_spy)
    text = AsyncMock(return_value="text-answer")
    monkeypatch.setattr("core.agents.analytical_ops.compute_count_answer", text)

    out = await _aggregation_answer(_DRIVER, "how many yoga class", "CTX")

    assert out == "text-answer"                      # text operator result
    symbolic_spy.assert_not_called()                 # symbolic path never entered
    text.assert_awaited_once_with("how many yoga class", "CTX")  # called as before


@pytest.mark.asyncio
async def test_aggregation_flag_on_symbolic_short_circuits(monkeypatch) -> None:
    monkeypatch.setattr("config.features.ENABLE_FACT_SYMBOLIC_COUNT", True)
    monkeypatch.setattr(seam, "_symbolic_count_answer", MagicMock(return_value="4"))
    text = AsyncMock(return_value="text-answer")
    monkeypatch.setattr("core.agents.analytical_ops.compute_count_answer", text)

    out = await _aggregation_answer(_DRIVER, "how many yoga class", "CTX")

    assert out == "4"                 # symbolic answer wins
    text.assert_not_awaited()         # text operator not consulted


@pytest.mark.asyncio
async def test_aggregation_flag_on_no_support_falls_through(monkeypatch) -> None:
    monkeypatch.setattr("config.features.ENABLE_FACT_SYMBOLIC_COUNT", True)
    monkeypatch.setattr(seam, "_symbolic_count_answer", MagicMock(return_value=None))
    text = AsyncMock(return_value="text-answer")
    monkeypatch.setattr("core.agents.analytical_ops.compute_count_answer", text)

    out = await _aggregation_answer(_DRIVER, "how many yoga class", "CTX")

    assert out == "text-answer"       # no graph support → text operator
    text.assert_awaited_once_with("how many yoga class", "CTX")
