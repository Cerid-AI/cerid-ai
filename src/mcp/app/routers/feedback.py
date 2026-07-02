# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Per-claim user feedback router (Phase R.1).

Exposes a single endpoint under the stable SDK v1 prefix:

    POST /sdk/v1/feedback
        Submit a thumbs-up / thumbs-down / neutral rating for a single
        verified claim.  Idempotent per (claim_id, user_id/session_id).

The endpoint is additive — it does not modify any existing routes and
lives in the ``/sdk/v1/`` namespace per the additive-evolution rule.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.services.feedback import ClaimFeedback, submit_feedback
from core.utils.swallowed import log_swallowed_error


# --- Response models (generated: single-return dict-literal routes) ---
class SubmitClaimFeedbackResponse(BaseModel):
    ok: bool
    rating_id: Any



logger = logging.getLogger("ai-companion.feedback")

router = APIRouter(prefix="/sdk/v1", tags=["feedback"])


@router.post("/feedback", status_code=status.HTTP_201_CREATED, response_model=SubmitClaimFeedbackResponse)
async def submit_claim_feedback(body: ClaimFeedback) -> dict:
    """Submit user feedback for a single verified claim.

    Body: :class:`~app.services.feedback.ClaimFeedback`

    Returns ``{"ok": true, "rating_id": "<id>"}`` on success.

    Idempotency: if the same ``(claim_id, user_id)`` or
    ``(claim_id, session_id)`` pair already exists in the graph, the
    existing rating edge is updated (sentiment + timestamp overwritten).
    This prevents thumbs-toggling from flooding the graph with duplicate
    edges.

    Sentiment values:
    - ``1``  — positive / the claim is correct
    - ``0``  — neutral
    - ``-1`` — negative / the claim is incorrect
    """
    try:
        rating_id = await submit_feedback(body)
        return {"ok": True, "rating_id": rating_id}
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        log_swallowed_error("feedback.submit_claim_feedback", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Feedback could not be persisted; please retry.",
        ) from exc
