# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Enterprise audit-log surface (``audit_logging``).

Prefix: /audit-log

    GET /audit-log         → records, newest first, filterable
    GET /audit-log/verify  → walk the hash chain and report the first break

Distinct from `/agent/audit`, which audits knowledge *quality* (hallucination
and contradiction checks). This is the security log: who did what, to what, and
whether it worked.

Both endpoints are gated. The events are recorded regardless of tier — a log
that only starts when someone buys it is not a log, and an Enterprise customer
would have nothing to read for the period before purchase — but *reading* them
back is the paid surface. That split is deliberate and is why the recording
call sites carry no feature check.
"""
from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from core.utils import audit_log

logger = logging.getLogger("ai-companion.audit_log_router")

router = APIRouter(prefix="/audit-log", tags=["audit-log"])

FEATURE_FLAG = "audit_logging"


class AuditRecordsResponse(BaseModel):
    records: list[dict[str, Any]]
    total: int
    limit: int
    offset: int


class AuditVerifyResponse(BaseModel):
    ok: bool
    checked: int
    records: int
    broken_at: int | None = None
    reason: str | None = None


def _require_feature() -> None:
    """Refuse when the flag is off, and refuse when it cannot be evaluated.

    Fail CLOSED on the import, matching `pro_automations._require_feature`: a
    gate that cannot answer must not serve a paid surface on the way past. That
    exact bug shipped here once already — `except ImportError: pass` wrapped
    around the whole check.
    """
    try:
        from config.features import is_feature_enabled
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail="Feature gating is unavailable; refusing the request.",
        ) from exc

    if not is_feature_enabled(FEATURE_FLAG):
        raise HTTPException(
            status_code=403,
            detail=f"{FEATURE_FLAG} feature flag is off (Enterprise tier).",
        )


@router.get("", response_model=AuditRecordsResponse)
async def list_records(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    action_prefix: str | None = Query(None, description="e.g. 'license.' or 'artifact.'"),
    outcome: Literal["success", "failure", "denied"] | None = Query(None),
) -> AuditRecordsResponse:
    _require_feature()
    records = audit_log.read(
        limit=limit,
        offset=offset,
        action_prefix=action_prefix,
        outcome=outcome,
    )
    return AuditRecordsResponse(
        records=records,
        total=audit_log.count(),
        limit=limit,
        offset=offset,
    )


@router.get("/verify", response_model=AuditVerifyResponse)
async def verify_chain() -> AuditVerifyResponse:
    """Report whether the log has been altered since it was written.

    A 200 with ``ok: false`` is the interesting answer, not an error — the
    endpoint worked; the log did not. Returning a 5xx here would make "the
    check could not run" and "the check failed" the same HTTP status, which is
    the substitution this whole subsystem exists to avoid.
    """
    _require_feature()
    result = audit_log.verify()
    return AuditVerifyResponse(**result)
