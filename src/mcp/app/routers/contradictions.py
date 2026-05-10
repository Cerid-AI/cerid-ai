# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Contradiction ledger API endpoints (Phase W.4).

Routes
------
GET /wiki/contradictions
    Paginated list of contradiction findings. Supports ``entity_slug``,
    ``since``, and ``limit`` query parameters.

GET /wiki/contradictions/{finding_id}
    Single finding by stable identifier. Returns 404 if not found.

Both endpoints return :class:`ContradictionFindingResponse` which mirrors
the :class:`~app.services.contradiction_log.ContradictionFinding` model.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.services.contradiction_log import (
    ContradictionFinding,
    get_by_id,
    list_recent,
)
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.contradictions")

router = APIRouter(prefix="/wiki", tags=["wiki"])


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class ContradictionFindingResponse(BaseModel):
    """Wire-format for a single contradiction finding.

    Matches the shape documented in the W.4 plan. ``entity_slug`` and
    ``query_ctx_id`` are nullable; ``source_artifacts`` is always a list
    (may be empty).
    """

    finding_id: str
    claim_a_id: str
    claim_b_id: str
    claim_a_text: str
    claim_b_text: str
    entity_slug: str | None
    severity: str
    detected_at: str
    query_ctx_id: str | None
    source_artifacts: list[str]

    @classmethod
    def from_finding(cls, f: ContradictionFinding) -> "ContradictionFindingResponse":
        return cls(
            finding_id=f.finding_id,
            claim_a_id=f.claim_a_id,
            claim_b_id=f.claim_b_id,
            claim_a_text=f.claim_a_text,
            claim_b_text=f.claim_b_text,
            entity_slug=f.entity_slug,
            severity=f.severity,
            detected_at=f.detected_at,
            query_ctx_id=f.query_ctx_id,
            source_artifacts=f.source_artifacts,
        )


class ContradictionListResponse(BaseModel):
    """Paginated list response for :func:`list_contradictions`."""

    total: int
    limit: int
    findings: list[ContradictionFindingResponse]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/contradictions",
    response_model=ContradictionListResponse,
    summary="List contradiction findings",
    description=(
        "Returns contradiction findings detected by the NLI guard, ordered "
        "by detection time (newest first). Optionally filter by entity slug "
        "or a lower-bound timestamp."
    ),
)
async def list_contradictions(
    entity_slug: str | None = Query(
        default=None,
        description="Filter by associated entity slug (e.g. 'elon-musk').",
    ),
    since: str | None = Query(
        default=None,
        description="ISO-8601 lower bound on detected_at (inclusive).",
    ),
    limit: int = Query(
        default=100,
        ge=1,
        le=1000,
        description="Maximum number of findings to return (1–1000).",
    ),
) -> ContradictionListResponse:
    try:
        findings = await list_recent(
            entity_slug=entity_slug,
            since=since,
            limit=limit,
        )
    except Exception as exc:
        log_swallowed_error("contradiction_log", exc)
        raise HTTPException(status_code=500, detail="Failed to retrieve contradiction findings") from exc

    return ContradictionListResponse(
        total=len(findings),
        limit=limit,
        findings=[ContradictionFindingResponse.from_finding(f) for f in findings],
    )


@router.get(
    "/contradictions/{finding_id}",
    response_model=ContradictionFindingResponse,
    summary="Get a single contradiction finding",
    description="Retrieve a contradiction finding by its stable identifier.",
)
async def get_contradiction(finding_id: str) -> ContradictionFindingResponse:
    try:
        finding = await get_by_id(finding_id)
    except Exception as exc:
        log_swallowed_error("contradiction_log", exc, context={"finding_id": finding_id})
        raise HTTPException(status_code=500, detail="Failed to retrieve contradiction finding") from exc

    if finding is None:
        raise HTTPException(status_code=404, detail=f"Contradiction finding {finding_id!r} not found")

    return ContradictionFindingResponse.from_finding(finding)
