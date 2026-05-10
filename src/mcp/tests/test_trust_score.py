# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for app/services/trust_score.py.

Covers the compositor with mocked baseline files and a fake Neo4j driver.
Does not require the live stack — the readers are file-based and the
Neo4j coverage component is mockable.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.trust_score import (
    TrustComponent,
    TrustScore,
    _band_for,
    _classify_status,
    _normalize,
    compute_trust_score,
    trust_score_24h_summary,
)

# ---------------------------------------------------------------------- normalize


@pytest.mark.parametrize(
    "value,target,expected",
    [
        (0.95, 0.90, 1.0),  # above target -> capped at 1.0
        (0.90, 0.90, 1.0),
        (0.45, 0.90, 0.5),
        (0.0, 0.90, 0.0),
        (0.85, None, 0.85),  # no target -> pass through
        (None, 0.90, None),
        (-0.1, 0.90, 0.0),  # below zero -> clamped
        (1.5, None, 1.0),  # above one with no target -> clamped
    ],
)
def test_normalize_boundaries(
    value: float | None, target: float | None, expected: float | None
) -> None:
    result = _normalize(value, target)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


# ---------------------------------------------------------------------- classify_status


@pytest.mark.parametrize(
    "value,target,expected",
    [
        (None, 0.90, "not_available"),
        (0.95, 0.90, "ok"),
        (0.90, 0.90, "ok"),
        (0.88, 0.90, "warn"),  # within 5% below target
        (0.86, 0.90, "warn"),  # ≥ target * 0.95 (0.855)
        (0.84, 0.90, "fail"),  # < target * 0.95
        (0.80, 0.90, "fail"),  # > 5% below
        (0.50, None, "ok"),  # no target
    ],
)
def test_classify_status(value: float | None, target: float | None, expected: str) -> None:
    assert _classify_status(value, target) == expected


# ---------------------------------------------------------------------- band_for


@pytest.mark.parametrize(
    "score,expected",
    [
        (None, None),
        (0, "low"),
        (69, "low"),
        (70, "medium"),
        (84, "medium"),
        (85, "high"),
        (100, "high"),
    ],
)
def test_band_for(score: int | None, expected: str | None) -> None:
    assert _band_for(score) == expected


# ---------------------------------------------------------------------- compute_trust_score


def test_compute_trust_score_no_baselines(tmp_path: Path) -> None:
    """No baseline files and no Neo4j → all components not_available, score None."""
    with patch("app.services.trust_score._BASELINES_DIR", tmp_path), \
         patch("app.services.trust_score._RAGAS_PATH", tmp_path / "ragas.json"), \
         patch("app.services.trust_score._RETRIEVAL_PATH", tmp_path / "retrieval.json"), \
         patch("app.services.trust_score._LONGMEMEVAL_PATH", tmp_path / "longmemeval.json"), \
         patch("app.services.trust_score._PRESERVATION_PATH", tmp_path / "preservation.json"):
        ts = compute_trust_score(neo4j_driver=None)
    assert ts.score is None
    assert ts.band is None
    assert all(c.status == "not_available" for c in ts.components)
    assert len(ts.components) == 6  # five original + user_agreement (R.1)


def test_compute_trust_score_with_full_baselines(tmp_path: Path) -> None:
    """All baselines present → score computes from normalized mean."""
    (tmp_path / "ragas.json").write_text(json.dumps({
        "faithfulness": 0.93,
        "last_updated": "2026-05-10",
    }))
    (tmp_path / "retrieval.json").write_text(json.dumps({
        "metrics": {"avg_ndcg_10": 0.88},
        "last_updated": "2026-05-10",
    }))
    (tmp_path / "longmemeval.json").write_text(json.dumps({
        "result": {"recall_score": 0.84},
        "last_run_at": "2026-05-09",
    }))
    (tmp_path / "preservation.json").write_text(json.dumps({
        "passed": 38, "total": 38, "last_run_at": "2026-05-10",
    }))

    with patch("app.services.trust_score._RAGAS_PATH", tmp_path / "ragas.json"), \
         patch("app.services.trust_score._RETRIEVAL_PATH", tmp_path / "retrieval.json"), \
         patch("app.services.trust_score._LONGMEMEVAL_PATH", tmp_path / "longmemeval.json"), \
         patch("app.services.trust_score._PRESERVATION_PATH", tmp_path / "preservation.json"):
        ts = compute_trust_score(neo4j_driver=None)

    # 4 of 5 components contribute (verification_coverage skipped — no driver)
    available = [c for c in ts.components if c.status != "not_available"]
    assert len(available) == 4
    assert ts.score is not None
    assert ts.score >= 80
    assert ts.band in ("medium", "high")


def test_compute_trust_score_partial_baselines(tmp_path: Path) -> None:
    """Subset of baselines present → score still computes from available."""
    (tmp_path / "ragas.json").write_text(json.dumps({"faithfulness": 0.95}))

    with patch("app.services.trust_score._RAGAS_PATH", tmp_path / "ragas.json"), \
         patch("app.services.trust_score._RETRIEVAL_PATH", tmp_path / "missing.json"), \
         patch("app.services.trust_score._LONGMEMEVAL_PATH", tmp_path / "missing.json"), \
         patch("app.services.trust_score._PRESERVATION_PATH", tmp_path / "missing.json"):
        ts = compute_trust_score(neo4j_driver=None)

    available = [c for c in ts.components if c.status != "not_available"]
    assert len(available) == 1
    assert ts.score is not None  # at least one component → score exists
    # Single 0.95/0.90 normalizes to 1.0 → score == 100
    assert ts.score == 100


def test_compute_trust_score_with_neo4j_coverage() -> None:
    """Neo4j coverage component computes from rolling 24h fraction."""
    fake_driver = MagicMock()
    fake_session = MagicMock()
    fake_result = MagicMock()
    fake_result.single.return_value = {"total": 100, "covered": 97}
    fake_session.run.return_value = fake_result
    fake_driver.session.return_value.__enter__ = MagicMock(return_value=fake_session)
    fake_driver.session.return_value.__exit__ = MagicMock(return_value=False)

    ts = compute_trust_score(neo4j_driver=fake_driver)
    coverage = next(c for c in ts.components if c.id == "verification_coverage")
    assert coverage.value == pytest.approx(0.97)
    assert coverage.status == "ok"


def test_compute_trust_score_handles_neo4j_failure() -> None:
    """Neo4j connection failure → coverage component is not_available, score still computes."""
    fake_driver = MagicMock()
    fake_driver.session.side_effect = RuntimeError("connection refused")

    ts = compute_trust_score(neo4j_driver=fake_driver)
    coverage = next(c for c in ts.components if c.id == "verification_coverage")
    assert coverage.status == "not_available"
    # Other components remain valid (or also not_available depending on baselines)


# ---------------------------------------------------------------------- pydantic shape


def test_trust_score_serializes_cleanly() -> None:
    """TrustScore + TrustComponent both Pydantic, both round-trip."""
    ts = compute_trust_score(neo4j_driver=None)
    dumped = ts.model_dump()
    assert "score" in dumped
    assert "band" in dumped
    assert "updated_at" in dumped
    assert "components" in dumped
    assert isinstance(dumped["components"], list)
    for comp in dumped["components"]:
        assert "id" in comp
        assert "label" in comp
        assert "status" in comp


def test_trust_score_24h_summary_shape() -> None:
    """The /health.invariants summary is a stable compact dict."""
    summary = trust_score_24h_summary(neo4j_driver=None)
    assert set(summary.keys()) == {
        "score", "band", "available_components", "total_components", "updated_at"
    }
    assert summary["total_components"] == 6  # five original + user_agreement (R.1)
    assert isinstance(summary["available_components"], int)


# ---------------------------------------------------------------------- user_agreement component (R.1)


def test_user_agreement_not_available_without_neo4j() -> None:
    """Without a Neo4j driver the user_agreement component is not_available."""
    ts = compute_trust_score(neo4j_driver=None)
    ua = next(c for c in ts.components if c.id == "user_agreement")
    assert ua.status == "not_available"
    assert ua.value is None


def test_user_agreement_not_available_with_zero_ratings() -> None:
    """When no ratings exist in the window, component is not_available."""
    from app.db.neo4j.feedback import ClaimAccuracyStats as RawStats
    from core.utils.time import utcnow_iso

    fake_stats = RawStats(
        total_rated=0,
        positive=0,
        negative=0,
        neutral=0,
        agreement_rate=0.0,
        domain=None,
        window_hours=168,
        as_of_iso=utcnow_iso(),
    )

    # Patch at the adapter module since _read_user_agreement lazily imports it
    with patch("app.db.neo4j.feedback.claim_accuracy_rolling", return_value=fake_stats):
        fake_driver = MagicMock()
        ts = compute_trust_score(neo4j_driver=fake_driver)

    ua = next(c for c in ts.components if c.id == "user_agreement")
    assert ua.status == "not_available"
    assert ua.value is None


def test_user_agreement_ok_when_ratings_exist() -> None:
    """When ratings exist with high agreement rate, component is ok."""
    from app.db.neo4j.feedback import ClaimAccuracyStats as RawStats
    from core.utils.time import utcnow_iso

    fake_stats = RawStats(
        total_rated=50,
        positive=45,
        negative=3,
        neutral=2,
        agreement_rate=0.90,
        domain=None,
        window_hours=168,
        as_of_iso=utcnow_iso(),
    )

    with patch("app.db.neo4j.feedback.claim_accuracy_rolling", return_value=fake_stats):
        fake_driver = MagicMock()
        ts = compute_trust_score(neo4j_driver=fake_driver)

    ua = next(c for c in ts.components if c.id == "user_agreement")
    assert ua.status == "ok"
    assert ua.value == pytest.approx(0.90)
    assert ua.target == pytest.approx(0.80)
    assert ua.normalized == pytest.approx(1.0)  # 0.90 / 0.80 capped at 1.0


def test_user_agreement_fail_when_low_agreement() -> None:
    """When agreement rate is well below target, component status is fail."""
    from app.db.neo4j.feedback import ClaimAccuracyStats as RawStats
    from core.utils.time import utcnow_iso

    fake_stats = RawStats(
        total_rated=20,
        positive=10,
        negative=8,
        neutral=2,
        agreement_rate=0.50,
        domain=None,
        window_hours=168,
        as_of_iso=utcnow_iso(),
    )

    with patch("app.db.neo4j.feedback.claim_accuracy_rolling", return_value=fake_stats):
        fake_driver = MagicMock()
        ts = compute_trust_score(neo4j_driver=fake_driver)

    ua = next(c for c in ts.components if c.id == "user_agreement")
    assert ua.status == "fail"
    assert ua.value == pytest.approx(0.50)


def test_compute_trust_score_has_six_components() -> None:
    """Post-R.1, the score always has exactly 6 component specs."""
    ts = compute_trust_score(neo4j_driver=None)
    assert len(ts.components) == 6
    component_ids = {c.id for c in ts.components}
    assert "user_agreement" in component_ids


def test_trust_score_model_validation_rejects_bad_band() -> None:
    """band must be 'high' | 'medium' | 'low' | None — Pydantic enforces."""
    with pytest.raises(Exception):  # pydantic.ValidationError
        TrustScore(
            score=50,
            band="garbage",  # type: ignore[arg-type]
            updated_at="2026-05-10T00:00:00Z",
            components=[],
        )


def test_trust_component_model_validation_rejects_bad_status() -> None:
    with pytest.raises(Exception):
        TrustComponent(
            id="x", label="X", value=0.5, target=0.5, normalized=1.0,
            status="garbage",  # type: ignore[arg-type]
            source="t",
        )
