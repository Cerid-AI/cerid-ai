# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Daily digest REST surface — Phase K Day 2.

Four endpoints, API-only by design — there is no dedicated in-app digest
view or "Run now" button. A user reads generated digest text as a raw
artifact under the "digests" domain in the Knowledge pane instead; this
surface exists for external/SDK consumers and for triggering a pass outside
the scheduled cadence:

  GET    /digests/latest         → most recent digest as JSON
  GET    /digests/recent         → last N digest summaries
  GET    /digests/{date}         → digest for a specific ISO date
  POST   /digests/run-now        → queue a fresh digest pass (202)

Pro-tier gated. Reads from KB artifacts in domain="digests" written
by `core.agents.daily_digest.generate_daily_digest`.

Run-now is a queued processor job (``DigestRunJob``). It used to run
the full digest inline — minutes on a populated corpus, which timed
out clients during beta (2026-07-12 triage) — so the endpoint now
returns 202 with a ``job_id`` and clients poll ``GET /digests/latest``
until ``generated_at`` advances.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.utils.artifact_tags import parse_tag_object

logger = logging.getLogger("ai-companion.digests")

router = APIRouter(prefix="/digests", tags=["digests"])


# ── response shapes ───────────────────────────────────────────────────

class DigestSummary(BaseModel):
    digest_id: str
    generated_at: str
    window_hours: int
    artifact_count: int
    flagged_count: int
    inbox_urgent_count: int
    # ``None`` means the digest that produced this artifact did not record
    # the field — NOT "this digest had no categories / no action items".
    # Both used to be hard-coded constants here, so a client could not tell
    # a measurement from a code default.
    top_categories: list[dict[str, Any]] | None
    has_urgent: bool
    has_action_items: bool | None
    persisted_artifact_id: str | None = None


class DigestQueuedResponse(BaseModel):
    """202 body — digest generation now runs as a background processor job."""

    job_id: str
    status: str = "queued"


# ── helpers ───────────────────────────────────────────────────────────

def _feature_on() -> bool:
    try:
        from config.features import is_feature_enabled
        return bool(is_feature_enabled("daily_digest"))
    except ImportError:
        return False


def _list_digest_artifacts(driver: Any, limit: int = 30) -> list[dict[str, Any]]:
    """Pull recent digest artifacts from the 'digests' domain.

    Raises on a failed read rather than returning ``[]`` — an empty list is
    the caller's "no digest has been generated yet" answer and must not
    double as "the graph store is down".
    """
    from app.db import neo4j as graph_db
    return graph_db.list_artifacts(driver, domain="digests", limit=limit) or []


def _digest_driver_or_503() -> Any:
    try:
        from app.deps import get_neo4j
        return get_neo4j()
    except Exception as exc:  # noqa: BLE001
        logger.warning("digests: neo4j unavailable: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Digest store unavailable — the graph store could not be reached.",
        ) from exc


def _digest_artifacts_or_503(driver: Any, limit: int) -> list[dict[str, Any]]:
    try:
        return _list_digest_artifacts(driver, limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning("digests list_artifacts failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Digest store unavailable — the digest read failed.",
        ) from exc


def _tag_top_categories(tags: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Ranked categories the digest recorded, or None when it recorded none.

    ``/ingest/structured`` metadata values are strings, so the list arrives
    JSON-encoded; accept a real list too for in-process callers.
    """
    raw = tags.get("top_categories")
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    if not isinstance(raw, list):
        return None
    return [c for c in raw if isinstance(c, dict)]


def _tag_has_action_items(tags: dict[str, Any]) -> bool | None:
    raw = tags.get("action_item_count")
    if raw is None:
        return None
    try:
        return int(raw) > 0
    except (TypeError, ValueError):
        return None


def _artifact_to_summary(a: dict[str, Any]) -> DigestSummary:
    tags = parse_tag_object(a.get("tags"))
    return DigestSummary(
        digest_id=tags.get("digest_id", a.get("id", "")),
        generated_at=tags.get("generated_at", ""),
        window_hours=int(tags.get("window_hours", "24") or "24"),
        artifact_count=int(tags.get("artifact_count", "0") or "0"),
        flagged_count=int(tags.get("flagged_count", "0") or "0"),
        inbox_urgent_count=int(tags.get("inbox_urgent_count", "0") or "0"),
        top_categories=_tag_top_categories(tags),
        has_urgent=int(tags.get("inbox_urgent_count", "0") or "0") > 0,
        has_action_items=_tag_has_action_items(tags),
        persisted_artifact_id=a.get("id"),
    )


# ── endpoints ─────────────────────────────────────────────────────────

@router.get("/latest", response_model=DigestSummary | None)
async def get_latest_digest() -> DigestSummary | None:
    """Most recent digest summary. Returns None when no digests have
    been generated yet — UI renders an empty state. 503 when the digest
    store cannot be read, so an outage never renders as that empty state."""
    if not _feature_on():
        raise HTTPException(
            status_code=403,
            detail="daily_digest is Pro-tier. Upgrade to enable scheduled digests.",
        )
    driver = _digest_driver_or_503()
    artifacts = _digest_artifacts_or_503(driver, limit=1)
    if not artifacts:
        return None
    return _artifact_to_summary(artifacts[0])


@router.get("/recent", response_model=list[DigestSummary])
async def list_recent_digests(limit: int = 7) -> list[DigestSummary]:
    """Last N digest summaries — for the Subjects pane's
    digest-strip UI affordance."""
    if not _feature_on():
        raise HTTPException(status_code=403, detail="daily_digest is Pro-tier.")
    limit = max(1, min(30, limit))
    driver = _digest_driver_or_503()
    artifacts = _digest_artifacts_or_503(driver, limit=limit)
    return [_artifact_to_summary(a) for a in artifacts]


@router.get("/{date}", response_model=DigestSummary | None)
async def get_digest_by_date(date: str) -> DigestSummary | None:
    """Digest for a specific ISO date (YYYY-MM-DD). Returns None when
    no digest exists for that date."""
    if not _feature_on():
        raise HTTPException(status_code=403, detail="daily_digest is Pro-tier.")
    # Lightweight validation — strict ISO date prevents arbitrary
    # tag lookup
    try:
        parsed = datetime.fromisoformat(date)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid ISO date: {date}")

    driver = _digest_driver_or_503()
    artifacts = _digest_artifacts_or_503(driver, limit=60)  # ~2-month window
    target_date = parsed.date().isoformat()
    for a in artifacts:
        tags = parse_tag_object(a.get("tags"))
        if tags.get("generated_at", "").startswith(target_date):
            return _artifact_to_summary(a)
    return None


@router.post("/run-now", status_code=202, response_model=DigestQueuedResponse)
async def run_digest_now() -> DigestQueuedResponse:
    """Queue a digest pass. Bypasses the CERID_DAILY_DIGEST_ENABLED
    toggle (the user explicitly opted in by hitting this endpoint) but
    still honors the feature flag.

    * 202 ``{"job_id": ..., "status": "queued"}`` — job enqueued (or a
      digest pass is already queued/running; its job_id is returned).

    Clients poll ``GET /digests/latest`` until ``generated_at`` advances
    past the pre-trigger value, then render.
    """
    if not _feature_on():
        raise HTTPException(status_code=403, detail="daily_digest is Pro-tier.")

    from app.processor.jobs.digest_run import (
        active_digest_run_jobs,
        enqueue_digest_run_job,
    )

    try:
        active = await asyncio.to_thread(active_digest_run_jobs)
        if active:
            return DigestQueuedResponse(job_id=active[0])
        job_id = await asyncio.to_thread(enqueue_digest_run_job)
    except Exception as exc:
        logger.exception("digest run-now enqueue failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Digest enqueue failed: {exc}")
    return DigestQueuedResponse(job_id=job_id)
