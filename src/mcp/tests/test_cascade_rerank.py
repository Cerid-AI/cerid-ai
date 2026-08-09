# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for ENABLE_CASCADE_RERANK — the pre-filter wrapper in rerank().

The cascade saves cross-encoder calls when the hybrid-search distribution is
long-tailed: anything below ``CASCADE_RERANK_PRE_THRESHOLD`` skips the CE
and lands below the cross-encoded survivors. With the flag off, behavior is
identical to pre-PR-4.
"""
from __future__ import annotations

from typing import Any

import pytest

from core.retrieval import reranker


@pytest.fixture
def mock_score_pairs(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, list[str]]]:
    """Replace _score_pairs with a deterministic, call-recording fake.

    The fake returns descending scores [0.99, 0.95, ..., 0.50] for whatever
    documents it was passed. Tests assert on which documents were fed to it.
    """
    calls: list[tuple[str, list[str]]] = []

    def _fake(query: str, documents: list[str]) -> list[float]:
        calls.append((query, list(documents)))
        return [0.99 - 0.05 * i for i in range(len(documents))]

    monkeypatch.setattr(reranker, "_score_pairs", _fake)
    return calls


def _result(content: str, relevance: float) -> dict[str, Any]:
    return {"content": content, "relevance": relevance}


# ---------------------------------------------------------------------------
# Default behavior (flag off) — pre-PR-4 baseline must be preserved
# ---------------------------------------------------------------------------


def test_cascade_off_runs_ce_on_all_top_candidates(
    monkeypatch: pytest.MonkeyPatch,
    mock_score_pairs: list[tuple[str, list[str]]],
) -> None:
    monkeypatch.setattr(reranker.config, "ENABLE_CASCADE_RERANK", False, raising=False)
    monkeypatch.setattr(reranker.config, "QUERY_RERANK_CANDIDATES", 10, raising=False)
    monkeypatch.setattr(reranker.config, "RERANK_CE_WEIGHT", 1.0, raising=False)
    monkeypatch.setattr(reranker.config, "RERANK_ORIGINAL_WEIGHT", 0.0, raising=False)

    results = [
        _result("a", 0.9),
        _result("b", 0.7),
        _result("c", 0.2),  # would be cascade-dropped if flag were on
        _result("d", 0.1),
    ]
    reranker.rerank("query", results)
    assert len(mock_score_pairs) == 1
    # All four are fed to the cross-encoder when cascade is off.
    assert mock_score_pairs[0][1] == ["a", "b", "c", "d"]


# ---------------------------------------------------------------------------
# Cascade on — pre-filter must drop low-signal candidates
# ---------------------------------------------------------------------------


def test_cascade_on_skips_low_signal_candidates(
    monkeypatch: pytest.MonkeyPatch,
    mock_score_pairs: list[tuple[str, list[str]]],
) -> None:
    monkeypatch.setattr(reranker.config, "ENABLE_CASCADE_RERANK", True, raising=False)
    monkeypatch.setattr(reranker.config, "CASCADE_RERANK_PRE_THRESHOLD", 0.3, raising=False)
    monkeypatch.setattr(reranker.config, "QUERY_RERANK_CANDIDATES", 10, raising=False)
    monkeypatch.setattr(reranker.config, "RERANK_CE_WEIGHT", 1.0, raising=False)
    monkeypatch.setattr(reranker.config, "RERANK_ORIGINAL_WEIGHT", 0.0, raising=False)

    results = [
        _result("a", 0.9),
        _result("b", 0.7),
        _result("c", 0.2),  # below threshold
        _result("d", 0.1),  # below threshold
    ]
    out = reranker.rerank("query", results)

    # Cross-encoder saw only the two high-signal candidates
    assert mock_score_pairs[0][1] == ["a", "b"]
    # All four are returned, with low-signal preserved at the end
    assert {r["content"] for r in out} == {"a", "b", "c", "d"}
    # Low-signal appear after the cross-encoded ones
    contents = [r["content"] for r in out]
    assert contents.index("c") > contents.index("a")
    assert contents.index("d") > contents.index("b")


def test_cascade_skips_ce_when_at_most_one_high_signal(
    monkeypatch: pytest.MonkeyPatch,
    mock_score_pairs: list[tuple[str, list[str]]],
) -> None:
    """Cross-encoder over one document is pointless; skip it."""
    monkeypatch.setattr(reranker.config, "ENABLE_CASCADE_RERANK", True, raising=False)
    monkeypatch.setattr(reranker.config, "CASCADE_RERANK_PRE_THRESHOLD", 0.5, raising=False)
    monkeypatch.setattr(reranker.config, "QUERY_RERANK_CANDIDATES", 10, raising=False)

    results = [
        _result("a", 0.9),  # only one above the high threshold
        _result("b", 0.2),
        _result("c", 0.1),
    ]
    out = reranker.rerank("query", results)

    assert mock_score_pairs == []  # CE never invoked
    # Original ordering preserved
    assert [r["content"] for r in out] == ["a", "b", "c"]


def test_cascade_threshold_inclusive(
    monkeypatch: pytest.MonkeyPatch,
    mock_score_pairs: list[tuple[str, list[str]]],
) -> None:
    """A candidate exactly at the threshold should count as high-signal."""
    monkeypatch.setattr(reranker.config, "ENABLE_CASCADE_RERANK", True, raising=False)
    monkeypatch.setattr(reranker.config, "CASCADE_RERANK_PRE_THRESHOLD", 0.3, raising=False)
    monkeypatch.setattr(reranker.config, "QUERY_RERANK_CANDIDATES", 10, raising=False)
    monkeypatch.setattr(reranker.config, "RERANK_CE_WEIGHT", 1.0, raising=False)
    monkeypatch.setattr(reranker.config, "RERANK_ORIGINAL_WEIGHT", 0.0, raising=False)

    results = [_result("a", 0.5), _result("b", 0.3), _result("c", 0.29)]
    reranker.rerank("query", results)
    assert mock_score_pairs[0][1] == ["a", "b"]


def test_rerank_short_circuits_single_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """A one-item list never touches the cross-encoder, cascade flag aside."""
    monkeypatch.setattr(reranker.config, "ENABLE_CASCADE_RERANK", True, raising=False)
    out = reranker.rerank("query", [_result("a", 0.9)])
    assert out == [_result("a", 0.9)]
