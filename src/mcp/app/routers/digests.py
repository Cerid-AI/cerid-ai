# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Daily digest REST surface — Phase K Day 2.

Three endpoints for the chat UI + notification surfaces:

  GET    /digests/latest         → most recent digest as JSON
  GET    /digests/{date}         → digest for a specific ISO date
  POST   /digests/run-now        → trigger a fresh digest pass

Pro-tier gated. Reads from KB artifacts in domain="digests" written
by `core.agents.daily_digest.generate_daily_digest`.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

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
    top_categories: list[dict[str, Any]]
    has_urgent: bool
    has_action_items: bool
    persisted_artifact_id: str | None = None


class DigestFull(BaseModel):
    digest_id: str
    generated_at: str
    window_hours: int
    artifact_count: int
    flagged_count: int
    inbox_urgent_count: int
    top_categories: list[dict[str, Any]]
    key_threads: list[dict[str, Any]]
    urgent: list[dict[str, Any]]
    action_items: list[str]
    quality_alerts: list[dict[str, Any]]
    persisted_artifact_id: str | None = None


# ── helpers ───────────────────────────────────────────────────────────

def _feature_on() -> bool:
    try:
        from config.features import is_feature_enabled
        return bool(is_feature_enabled("daily_digest"))
    except ImportError:
        return False


def _list_digest_artifacts(driver: Any, limit: int = 30) -> list[dict[str, Any]]:
    """Pull recent digest artifacts from the 'digests' domain."""
    try:
        from app.db import neo4j as graph_db
        return graph_db.list_artifacts(driver, domain="digests", limit=limit) or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("digests list_artifacts failed: %s", exc)
        return []


def _artifact_to_summary(a: dict[str, Any]) -> DigestSummary:
    tags = a.get("tags") or {}
    return DigestSummary(
        digest_id=tags.get("digest_id", a.get("id", "")),
        generated_at=tags.get("generated_at", ""),
        window_hours=int(tags.get("window_hours", "24") or "24"),
        artifact_count=int(tags.get("artifact_count", "0") or "0"),
        flagged_count=int(tags.get("flagged_count", "0") or "0"),
        inbox_urgent_count=int(tags.get("inbox_urgent_count", "0") or "0"),
        top_categories=[],  # summary omits the full payload to stay light
        has_urgent=int(tags.get("inbox_urgent_count", "0") or "0") > 0,
        has_action_items=False,  # action_items aren't in artifact tags
        persisted_artifact_id=a.get("id"),
    )


# ── endpoints ─────────────────────────────────────────────────────────

@router.get("/latest", response_model=DigestSummary | None)
async def get_latest_digest() -> DigestSummary | None:
    """Most recent digest summary. Returns None when no digests have
    been generated yet — UI renders an empty state."""
    if not _feature_on():
        raise HTTPException(
            status_code=403,
            detail="daily_digest is Pro-tier. Upgrade to enable scheduled digests.",
        )
    try:
        from app.deps import get_neo4j
        driver = get_neo4j()
    except Exception as exc:  # noqa: BLE001
        logger.warning("digests latest: neo4j unavailable: %s", exc)
        return None

    artifacts = _list_digest_artifacts(driver, limit=1)
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
    try:
        from app.deps import get_neo4j
        driver = get_neo4j()
    except Exception as exc:  # noqa: BLE001
        logger.warning("digests recent: neo4j unavailable: %s", exc)
        return []
    artifacts = _list_digest_artifacts(driver, limit=limit)
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

    try:
        from app.deps import get_neo4j
        driver = get_neo4j()
    except Exception as exc:  # noqa: BLE001
        logger.warning("digests by-date: neo4j unavailable: %s", exc)
        return None

    artifacts = _list_digest_artifacts(driver, limit=60)  # ~2-month window
    target_date = parsed.date().isoformat()
    for a in artifacts:
        tags = a.get("tags") or {}
        if tags.get("generated_at", "").startswith(target_date):
            return _artifact_to_summary(a)
    return None


@router.post("/run-now", response_model=DigestFull)
async def run_digest_now() -> DigestFull:
    """Trigger a digest pass immediately. Bypasses the
    CERID_DAILY_DIGEST_ENABLED toggle (the user explicitly opted in
    by hitting this endpoint) but still honors the feature flag."""
    if not _feature_on():
        raise HTTPException(status_code=403, detail="daily_digest is Pro-tier.")

    from core.agents.daily_digest import generate_daily_digest

    result = await generate_daily_digest(persist=True)
    return DigestFull(
        digest_id=result.digest_id,
        generated_at=result.generated_at,
        window_hours=result.window_hours,
        artifact_count=result.artifact_count,
        flagged_count=result.flagged_count,
        inbox_urgent_count=result.inbox_urgent_count,
        top_categories=result.top_categories,
        key_threads=[{
            "title": s.title, "body": s.body, "artifact_ids": s.artifact_ids,
        } for s in result.key_threads],
        urgent=[{
            "title": s.title, "body": s.body, "artifact_ids": s.artifact_ids,
        } for s in result.urgent],
        action_items=result.action_items,
        quality_alerts=[{
            "title": s.title, "body": s.body, "artifact_ids": s.artifact_ids,
        } for s in result.quality_alerts],
        persisted_artifact_id=result.persisted_artifact_id,
    )
