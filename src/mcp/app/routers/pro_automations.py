# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Pro-tier feature automation REST surface (UX consolidation pass).

Distinct from `automations.py` (user-defined scheduled knowledge tasks).
This router exposes the OS-level Pro features that ship with their
own cron + opt-in pattern: inbox triage (Phase J) and daily digest
(Phase K).

Prefix: /settings/pro-automations

Endpoints:
    GET    /settings/pro-automations               → full list + metadata
    GET    /settings/pro-automations/{name}        → single state
    PUT    /settings/pro-automations/{name}        → set enabled + schedule
    POST   /settings/pro-automations/{name}/run-now → trigger immediately
    DELETE /settings/pro-automations/{name}        → clear Redis override

Backed by `utils.pro_automations` — Redis overrides take precedence
over the env defaults (CERID_INBOX_TRIAGE_ENABLED etc.) so the
Settings UI can change runtime state without operator env edits.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import utils.pro_automations as automations

logger = logging.getLogger("ai-companion.pro_automations_router")

router = APIRouter(prefix="/settings/pro-automations", tags=["pro-automations"])


class CadencePreset(BaseModel):
    label: str
    cron: str


class AutomationState(BaseModel):
    feature: str
    display_name: str
    description: str
    feature_flag: str
    feature_flag_enabled: bool
    enabled: bool
    schedule: str
    default_schedule: str
    cadence_presets: list[CadencePreset]


class AutomationListResponse(BaseModel):
    automations: list[AutomationState]


class ProAutomationUpdate(BaseModel):
    enabled: bool | None = Field(default=None)
    schedule: str | None = Field(
        default=None,
        description="Cron expression; empty disables.",
    )


class RunNowResponse(BaseModel):
    feature: str
    triggered: bool
    detail: str
    result: dict[str, Any] | None = None


def _require_feature(spec: dict[str, Any]) -> None:
    try:
        from config.features import is_feature_enabled
        if not is_feature_enabled(spec["feature_flag"]):
            raise HTTPException(
                status_code=403,
                detail=f"{spec['feature_flag']} feature flag is off (Pro tier).",
            )
    except ImportError:
        pass


@router.get("", response_model=AutomationListResponse)
async def list_automations() -> AutomationListResponse:
    return AutomationListResponse(
        automations=[AutomationState(**s) for s in automations.list_states()],
    )


@router.get("/{name}", response_model=AutomationState)
async def get_automation(name: str) -> AutomationState:
    try:
        return AutomationState(**automations.get_state(name))
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown automation: {name}")


@router.put("/{name}", response_model=AutomationState)
async def put_automation(name: str, req: ProAutomationUpdate) -> AutomationState:
    if name not in automations.AUTOMATIONS:
        raise HTTPException(status_code=404, detail=f"unknown automation: {name}")
    spec = automations.AUTOMATIONS[name]
    # Enabling requires Pro flag on; disabling/scheduling don't.
    if req.enabled is True:
        _require_feature(spec)
    try:
        if req.schedule is not None:
            automations.set_schedule(name, req.schedule)
        if req.enabled is not None:
            automations.set_enabled(name, req.enabled)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except (RuntimeError, KeyError) as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return AutomationState(**automations.get_state(name))


@router.delete("/{name}", response_model=AutomationState)
async def reset_automation(name: str) -> AutomationState:
    try:
        automations.reset(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown automation: {name}")
    return AutomationState(**automations.get_state(name))


@router.post("/{name}/run-now", response_model=RunNowResponse)
async def run_automation_now(name: str) -> RunNowResponse:
    """Trigger an automation immediately. Honors the feature flag but
    bypasses the env/Redis enabled toggle (user explicitly opted in by
    hitting this endpoint)."""
    if name not in automations.AUTOMATIONS:
        raise HTTPException(status_code=404, detail=f"unknown automation: {name}")
    spec = automations.AUTOMATIONS[name]
    _require_feature(spec)

    if name == "inbox_triage":
        from core.agents.inbox_triage import triage_inboxes
        triage = await triage_inboxes(persist=True)
        return RunNowResponse(
            feature=name,
            triggered=True,
            detail=f"triaged {len(triage.threads)} threads",
            result=triage.to_dict(),
        )
    if name == "daily_digest":
        from app.routers.license import current_license_watermark
        from core.agents.daily_digest import generate_daily_digest
        digest = await generate_daily_digest(
            persist=True, license_notice=current_license_watermark(),
        )
        return RunNowResponse(
            feature=name,
            triggered=True,
            detail=(
                f"{digest.artifact_count} artifacts, "
                f"{digest.flagged_count} flagged, "
                f"{digest.inbox_urgent_count} urgent"
            ),
            result=digest.to_dict(),
        )
    raise HTTPException(
        status_code=501,
        detail=f"no run-now handler wired for automation: {name}",
    )
