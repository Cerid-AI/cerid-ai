# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""TrustScore — system evaluation posture compositor.

Surfaces Cerid's eval rigor as a single chip backed by transparent
component scores. The score is a straight mean of normalized component
values — no learned weights, no proprietary formula. Honesty over
cleverness.

This is **pure presentation** — the TrustScore does not affect retrieval,
generation, or any model decision. It reports; it does not act.

Component sources (read-only):

- Faithfulness ............ tests/eval/baselines/ragas.json
- Retrieval (NDCG@10) ..... tests/eval/baselines/retrieval.json
- Memory recall .......... tests/eval/baselines/longmemeval.json
- Verification coverage .. Neo4j rolling 7d count
- Preservation health .... tests/eval/baselines/preservation.json
- User agreement ......... Neo4j rolling 7d (R.1, future)

Missing component files yield ``value=None`` + ``status='not_available'``
and are excluded from the mean. The score self-documents which components
contributed.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from core.utils.swallowed import log_swallowed_error
from core.utils.time import utcnow_iso

logger = logging.getLogger("ai-companion.trust_score")


# Repo-relative baseline locations.
#
# __file__ is ``src/mcp/app/services/trust_score.py``. Walking up:
#   parents[0] = src/mcp/app/services/
#   parents[1] = src/mcp/app/
#   parents[2] = src/mcp/          ← the package root
#   parents[3] = src/              ← TOO FAR (the path bug pre-2026-05-15)
#
# Inside the docker image ``__file__`` is ``/app/app/services/...`` so the
# package root is ``/app``. ``parents[2]`` yields ``/app`` correctly;
# ``parents[3]`` yielded ``/`` and made every baseline lookup miss. That
# bug shipped silently for months: ``ragas.json missing`` + ``retrieval.json
# missing`` notes in /health were the symptom but the files actually existed.
_BASELINES_DIR = Path(__file__).resolve().parents[2] / "tests" / "eval" / "baselines"
_RAGAS_PATH = _BASELINES_DIR / "ragas.json"
_RETRIEVAL_PATH = _BASELINES_DIR / "retrieval.json"
_LONGMEMEVAL_PATH = _BASELINES_DIR / "longmemeval.json"
_PRESERVATION_PATH = _BASELINES_DIR / "preservation.json"


ComponentStatus = Literal["ok", "warn", "fail", "not_available"]
ScoreBand = Literal["high", "medium", "low"]


class TrustComponent(BaseModel):
    """One component of the composite TrustScore.

    ``value`` is the raw measurement (e.g. ``0.93`` for faithfulness).
    ``normalized`` is mapped to [0, 1] for averaging. ``target`` documents
    the minimum acceptable value; ``status`` summarizes whether the
    component is meeting it.
    """

    id: str
    label: str
    value: float | None
    target: float | None
    normalized: float | None
    status: ComponentStatus
    source: str
    last_updated_at: str | None = None
    note: str | None = None


class TrustScore(BaseModel):
    """System-level evaluation posture, 0–100.

    Score is the simple mean of normalized component values, scaled to
    [0, 100]. Components with ``status='not_available'`` are excluded.
    A score of 0 with no components is reported as ``score=None``.
    """

    score: int | None
    band: ScoreBand | None
    updated_at: str
    components: list[TrustComponent]
    note: str = Field(
        default=(
            "Score is the straight mean of normalized component values. "
            "No learned weights. Components with 'not_available' status "
            "are excluded from the mean."
        ),
    )


@dataclass(slots=True)
class _ComponentSpec:
    """Static description of one TrustScore component.

    Pulled out of the compositor to keep the assembly loop one function.
    """

    id: str
    label: str
    target: float | None
    source: str
    reader: Any  # callable returning (value, last_updated_at, note)


def _read_json_safely(path: Path, *, module: str) -> dict[str, Any] | None:
    """Read a JSON file, returning ``None`` if missing or malformed."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        log_swallowed_error(module, exc, context={"path": str(path)})
        return None


def _read_faithfulness() -> tuple[float | None, str | None, str | None]:
    data = _read_json_safely(_RAGAS_PATH, module="trust_score.faithfulness")
    if data is None:
        return None, None, "ragas.json missing"
    # ragas.json shape: ``{metrics: {faithfulness, context_precision, ...}}``.
    # Earlier code looked at top-level ``data.get("faithfulness")`` — wrong
    # shape, so even when the file existed and was populated, the reader
    # returned None.
    metrics = data.get("metrics") or {}
    value = metrics.get("faithfulness")
    last = data.get("last_updated") or data.get("last_run_at")
    if value is None:
        return None, last, "faithfulness baseline not yet established (run RAGAS)"
    return float(value), last, None


def _read_retrieval_ndcg() -> tuple[float | None, str | None, str | None]:
    data = _read_json_safely(_RETRIEVAL_PATH, module="trust_score.retrieval")
    if data is None:
        return None, None, "retrieval.json missing"
    metrics = data.get("metrics") or {}
    value = metrics.get("avg_ndcg_10")
    last = data.get("last_updated") or data.get("last_run_at")
    if value is None:
        return None, last, "avg_ndcg_10 missing"
    return float(value), last, None


def _read_longmemeval() -> tuple[float | None, str | None, str | None]:
    data = _read_json_safely(_LONGMEMEVAL_PATH, module="trust_score.longmemeval")
    if data is None:
        return None, None, "longmemeval.json not yet generated"
    result = data.get("result") or {}
    value = result.get("recall_score")
    last = data.get("last_run_at")
    if value is None:
        return None, last, "recall_score missing"
    return float(value), last, None


def _read_preservation() -> tuple[float | None, str | None, str | None]:
    data = _read_json_safely(_PRESERVATION_PATH, module="trust_score.preservation")
    if data is None:
        return None, None, "preservation.json not yet written by CI"
    passed = data.get("passed")
    failed = data.get("failed", 0)
    last = data.get("last_run_at")
    if passed is None:
        return None, last, "passed missing"
    # Denominator: passed + failed (exclude skipped). A skipped invariant is
    # not a regression — counting it against the rate would penalise the
    # honest case where the harness chose to skip (e.g. env-gated tests).
    attempted = int(passed) + int(failed)
    if attempted == 0:
        return None, last, "no invariants attempted"
    return float(passed) / float(attempted), last, f"{passed}/{attempted}"


def _read_user_agreement(
    neo4j_driver: Any | None,
) -> tuple[float | None, str | None, str | None]:
    """Rolling 7-day user agreement rate from Phase R.1 feedback graph.

    Reads via the feedback service's Neo4j adapter to avoid duplicating
    the Cypher query.  Returns ``(value, last_updated_at, note)``.
    """
    if neo4j_driver is None:
        return None, None, "neo4j driver not provided"
    try:
        from app.db.neo4j.feedback import claim_accuracy_rolling
        stats = claim_accuracy_rolling(neo4j_driver, domain=None, window_hours=168)
        if stats.total_rated == 0:
            return None, stats.as_of_iso, "no ratings in last 7 days"
        return stats.agreement_rate, stats.as_of_iso, f"{stats.positive}/{stats.total_rated} positive"
    except Exception as exc:  # noqa: BLE001 — neo4j connection failure is observable
        log_swallowed_error("trust_score.user_agreement", exc)
        return None, None, "neo4j query failed"


_VERIFICATION_COVERAGE_WINDOW_HOURS = 168  # rolling 7d


def _read_verification_coverage(
    neo4j_driver: Any | None,
) -> tuple[float | None, str | None, str | None]:
    """Rolling 7-day fraction of verified claims across recent reports.

    Source of truth: ``(:VerificationReport)`` nodes written by
    ``app.db.neo4j.artifacts.save_verification_report`` after each
    hallucination check / verify-stream run. Each report carries
    aggregate ``verified``, ``unverified``, ``uncertain``, and ``total``
    counters; coverage = sum(verified) / sum(total) over the window.

    Window widened from 24 h → 7 d (v0.95.7) so the component lights up
    on developer machines and quiet-tenant deployments where 24 h of
    verification traffic is often empty. Aligns with user_agreement,
    which already uses a 7-day window.

    Earlier versions of this reader targeted ``(:Claim)`` nodes with a
    ``detected_at`` property. Neither artifact exists in the live
    schema — the verification pipeline persists reports, not standalone
    Claim nodes, and Claim instances created downstream (briefs, ratings)
    carry ``created_at`` not ``detected_at``. The component reported
    "no claims in last 24h" forever as a result.
    """
    if neo4j_driver is None:
        return None, None, "neo4j driver not provided"
    try:
        with neo4j_driver.session() as session:
            since_iso = (
                datetime.now(timezone.utc)
                - timedelta(hours=_VERIFICATION_COVERAGE_WINDOW_HOURS)
            ).isoformat()
            result = session.run(
                """
                MATCH (r:VerificationReport)
                WHERE r.created_at >= $since
                WITH
                    sum(coalesce(r.total, 0))    AS total,
                    sum(coalesce(r.verified, 0)) AS verified
                RETURN total, verified AS covered
                """,
                since=since_iso,
            )
            row = result.single()
            if row is None or row["total"] == 0:
                return None, utcnow_iso(), "no verification reports in last 7d"
            total = int(row["total"])
            covered = int(row["covered"])
            return covered / total, utcnow_iso(), f"{covered}/{total} verified"
    except Exception as exc:  # noqa: BLE001 — neo4j connection failure is observable
        log_swallowed_error("trust_score.verification_coverage", exc)
        return None, None, "neo4j query failed"


def _classify_status(
    value: float | None, target: float | None
) -> ComponentStatus:
    if value is None:
        return "not_available"
    if target is None:
        return "ok"
    if value >= target:
        return "ok"
    # Within 5% below target is "warn"; further below is "fail".
    if value >= target * 0.95:
        return "warn"
    return "fail"


def _normalize(value: float | None, target: float | None) -> float | None:
    """Map a component value to [0, 1] for averaging.

    Default normalization: ``min(1.0, value / target)``. When target is
    None or zero, pass value through assuming it's already in [0, 1].
    """
    if value is None:
        return None
    if target is None or target == 0:
        return max(0.0, min(1.0, float(value)))
    return max(0.0, min(1.0, float(value) / float(target)))


def _band_for(score: int | None) -> ScoreBand | None:
    if score is None:
        return None
    if score >= 85:
        return "high"
    if score >= 70:
        return "medium"
    return "low"


def compute_trust_score(neo4j_driver: Any | None = None) -> TrustScore:
    """Read each component and compose the system trust score.

    All component readers are best-effort; missing component data is
    excluded from the mean. The score is the simple arithmetic mean of
    normalized component values, scaled to 0..100 and rounded.

    Pass a Neo4j driver to populate verification coverage. Pass None to
    skip that component (e.g., for unit tests).
    """
    specs: list[_ComponentSpec] = [
        _ComponentSpec(
            id="faithfulness",
            label="Faithfulness",
            target=0.90,
            source="nightly RAGAS",
            reader=_read_faithfulness,
        ),
        _ComponentSpec(
            id="retrieval_ndcg10",
            label="Retrieval (NDCG@10)",
            target=0.85,
            source="nightly IR baseline",
            reader=_read_retrieval_ndcg,
        ),
        _ComponentSpec(
            id="memory_recall",
            label="Memory recall (LongMemEval)",
            target=0.80,
            source="weekly LongMemEval run",
            reader=_read_longmemeval,
        ),
        _ComponentSpec(
            id="verification_coverage",
            label="Verification coverage",
            target=0.95,
            source="Neo4j rolling 7d",
            reader=lambda: _read_verification_coverage(neo4j_driver),
        ),
        _ComponentSpec(
            id="preservation_health",
            label="Preservation health",
            target=1.0,
            source="last main CI",
            reader=_read_preservation,
        ),
        _ComponentSpec(
            id="user_agreement",
            label="User agreement",
            target=0.80,
            source="Neo4j rolling 7d (R.1)",
            reader=lambda: _read_user_agreement(neo4j_driver),
        ),
    ]

    components: list[TrustComponent] = []
    normalized_values: list[float] = []

    for spec in specs:
        value, last_updated, note = spec.reader()
        normalized = _normalize(value, spec.target)
        status = _classify_status(value, spec.target)
        components.append(
            TrustComponent(
                id=spec.id,
                label=spec.label,
                value=value,
                target=spec.target,
                normalized=normalized,
                status=status,
                source=spec.source,
                last_updated_at=last_updated,
                note=note,
            )
        )
        if normalized is not None:
            normalized_values.append(normalized)

    if normalized_values:
        mean = sum(normalized_values) / len(normalized_values)
        score: int | None = int(round(mean * 100))
    else:
        score = None

    return TrustScore(
        score=score,
        band=_band_for(score),
        updated_at=utcnow_iso(),
        components=components,
    )


def trust_score_24h_summary(neo4j_driver: Any | None = None) -> dict[str, Any]:
    """Compact summary for `/health.invariants`. Stable shape.

    Returns ``{score, band, available_components, total_components}``.
    """
    ts = compute_trust_score(neo4j_driver)
    available = sum(1 for c in ts.components if c.status != "not_available")
    return {
        "score": ts.score,
        "band": ts.band,
        "available_components": available,
        "total_components": len(ts.components),
        "updated_at": ts.updated_at,
    }
