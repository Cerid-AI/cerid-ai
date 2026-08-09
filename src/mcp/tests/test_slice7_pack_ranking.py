# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Slice 7 — personal-first knowledge-pack ranking.

7.2: pack chunks (those carrying a ``pack_id``) are down-weighted by
``PACK_RELEVANCE_WEIGHT`` in the rerank blend, AFTER the cross-encoder score —
so personal/KB data wins ties while packs still surface when clearly best.
"""
from __future__ import annotations

from typing import Any

import pytest

from core.retrieval import reranker


@pytest.fixture
def mock_score_pairs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deterministic cross-encoder: returns a flat 1.0 for every document so
    the blended score is driven purely by ORIGINAL_WEIGHT * relevance (we set
    CE_WEIGHT=0 in tests), isolating the pack down-weight effect."""

    def _fake(query: str, documents: list[str]) -> list[float]:
        return [1.0 for _ in documents]

    monkeypatch.setattr(reranker, "_score_pairs", _fake)


def _result(content: str, relevance: float, pack_id: str = "") -> dict[str, Any]:
    return {"content": content, "relevance": relevance, "pack_id": pack_id}


def _blend_only(monkeypatch: pytest.MonkeyPatch, weight: float = 0.7) -> None:
    """Configure the blend so relevance == original (CE weight 0), with the
    pack multiplier under test."""
    monkeypatch.setattr(reranker.config, "ENABLE_CASCADE_RERANK", False, raising=False)
    monkeypatch.setattr(reranker.config, "QUERY_RERANK_CANDIDATES", 10, raising=False)
    monkeypatch.setattr(reranker.config, "RERANK_CE_WEIGHT", 0.0, raising=False)
    monkeypatch.setattr(reranker.config, "RERANK_ORIGINAL_WEIGHT", 1.0, raising=False)
    monkeypatch.setattr(reranker.config, "PACK_RELEVANCE_WEIGHT", weight, raising=False)


def test_pack_chunk_is_downweighted(
    monkeypatch: pytest.MonkeyPatch, mock_score_pairs: None
) -> None:
    """A chunk with a pack_id has its blended score multiplied by the weight.
    (Two chunks so the reranker actually blends — it short-circuits on ≤1.)"""
    _blend_only(monkeypatch, weight=0.7)
    out = reranker.rerank(
        "q",
        [_result("pack doc", 0.8, pack_id="pack-abc"), _result("personal doc", 0.6)],
    )
    pack = next(r for r in out if r["content"] == "pack doc")
    assert pack["relevance"] == round(0.8 * 0.7, 4)  # 0.56


def test_personal_chunk_not_downweighted(
    monkeypatch: pytest.MonkeyPatch, mock_score_pairs: None
) -> None:
    """A chunk with an empty pack_id keeps its blended score (no multiplier)."""
    _blend_only(monkeypatch, weight=0.7)
    out = reranker.rerank(
        "q",
        [_result("personal doc", 0.8), _result("other", 0.6)],
    )
    personal = next(r for r in out if r["content"] == "personal doc")
    assert personal["relevance"] == 0.8


def test_personal_wins_tie_against_pack(
    monkeypatch: pytest.MonkeyPatch, mock_score_pairs: None
) -> None:
    """Identical-relevance personal vs pack chunk → personal ranks first."""
    _blend_only(monkeypatch, weight=0.7)
    results = [
        _result("pack doc", 0.8, pack_id="pack-abc"),
        _result("personal doc", 0.8, pack_id=""),
    ]
    out = reranker.rerank("q", results)
    assert out[0]["content"] == "personal doc"
    assert out[0]["relevance"] > out[1]["relevance"]


def test_weight_one_is_a_noop(
    monkeypatch: pytest.MonkeyPatch, mock_score_pairs: None
) -> None:
    """PACK_RELEVANCE_WEIGHT=1.0 leaves pack scores unchanged (neutral knob)."""
    _blend_only(monkeypatch, weight=1.0)
    out = reranker.rerank(
        "q",
        [_result("pack doc", 0.8, pack_id="pack-abc"), _result("other", 0.6)],
    )
    pack = next(r for r in out if r["content"] == "pack doc")
    assert pack["relevance"] == 0.8


def test_strong_pack_still_outranks_weak_personal(
    monkeypatch: pytest.MonkeyPatch, mock_score_pairs: None
) -> None:
    """The down-weight is a soft policy, not a hard exclusion: a clearly
    stronger pack chunk still beats a much weaker personal one."""
    _blend_only(monkeypatch, weight=0.7)
    results = [
        _result("strong pack", 0.95, pack_id="pack-abc"),  # 0.95*0.7 = 0.665
        _result("weak personal", 0.5, pack_id=""),         # 0.5
    ]
    out = reranker.rerank("q", results)
    assert out[0]["content"] == "strong pack"


# ---------------------------------------------------------------------------
# 7.3 — pack_relevance_weight settings entry (advanced / SERVER scope)
# ---------------------------------------------------------------------------


@pytest.fixture
def settings_client(monkeypatch: pytest.MonkeyPatch):
    """Settings router client; restores PACK_RELEVANCE_WEIGHT after each test
    (the PATCH handler mutates config directly)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import config
    from app.routers.settings import router

    monkeypatch.setattr(config, "SYNC_DIR", "", raising=False)
    monkeypatch.setattr(config, "PACK_RELEVANCE_WEIGHT", 0.7, raising=False)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_get_settings_includes_pack_relevance_weight(settings_client) -> None:
    body = settings_client.get("/settings").json()
    assert body["pack_relevance_weight"] == 0.7


def test_patch_accepts_pack_relevance_weight(settings_client) -> None:
    import config

    r = settings_client.patch("/settings", json={"pack_relevance_weight": 0.5})
    assert r.status_code == 200
    assert r.json()["updated"] == {"pack_relevance_weight": 0.5}
    assert config.PACK_RELEVANCE_WEIGHT == 0.5


def test_patch_rejects_out_of_range_pack_weight(settings_client) -> None:
    # ge=0.0, le=2.0 — 3.0 is out of range
    r = settings_client.patch("/settings", json={"pack_relevance_weight": 3.0})
    assert r.status_code in (400, 422)
