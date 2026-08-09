# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Brief scheduler settings router (RAG C3.4).

Routes
------
GET  /briefs/settings    Return the current operator-level brief settings.
PUT  /briefs/settings    Replace the brief settings document.

Why a dedicated router?
-----------------------
The settings are read by ``app/services/briefs/scheduler.py`` on every
cron firing, so they live separately from the per-user settings endpoint
at ``/settings`` — those are user-scoped state, this is a single
process-wide knob.  Surfacing it under ``/briefs/*`` keeps the URL space
honest: the scheduler is the consumer, and the writeback target is a
vault registered via ``/watched-folders``.

Validation
----------
``write_to_vault=True`` requires a non-empty ``vault_id`` referring to a
watched-folder that exists AND has ``is_vault=True``.  The folder
existence check uses the same Redis prefix the watched-folders router
writes to, so an in-flight delete is detected at PUT time rather than
silently degrading at the next cron firing.
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.briefs.settings import (
    DEFAULT_VAULT_FOLDER,
    BriefSettings,
    load_brief_settings,
    save_brief_settings,
)
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.briefs.settings")

router = APIRouter(prefix="/briefs", tags=["briefs"])

# Kept in sync with app/services/vault_write.py — duplication is
# deliberate (services don't import from routers) but bounded.
_WATCHED_FOLDERS_PREFIX = "cerid:watched_folders"

__all__ = ["router"]


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class BriefSettingsModel(BaseModel):
    """Operator-controlled brief scheduler settings."""

    write_to_vault: bool = Field(
        default=False,
        description=(
            "When True, the daily-brief and weekly-synthesis jobs write "
            "their generated markdown back to the vault identified by "
            "``vault_id`` after persisting to Neo4j.  Default False — "
            "opt-in per-operator side effect."
        ),
    )
    vault_id: str | None = Field(
        default=None,
        description=(
            "Watched-folder ID for the target vault.  Required when "
            "``write_to_vault`` is True; ignored otherwise.  The folder "
            "must already exist via ``POST /watched-folders`` with "
            "``is_vault=True``."
        ),
    )
    vault_folder: str = Field(
        default=DEFAULT_VAULT_FOLDER,
        description=(
            "Path prefix under the vault root where brief notes land.  "
            "Defaults to ``_briefs`` so Cerid-authored notes are kept "
            "segregated from user content."
        ),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _assert_vault_exists(redis_client: object, vault_id: str) -> None:
    """Raise HTTP 400 if vault_id is not a registered vault folder."""
    try:
        raw = redis_client.get(  # type: ignore[attr-defined]
            f"{_WATCHED_FOLDERS_PREFIX}:{vault_id}",
        )
    except Exception as exc:  # noqa: BLE001 — surfaces below
        log_swallowed_error("brief_settings.redis_get", exc)
        raise HTTPException(
            status_code=500, detail="Failed to validate vault_id",
        ) from exc

    if not raw:
        raise HTTPException(
            status_code=400, detail=f"Unknown vault_id: {vault_id!r}",
        )
    try:
        record = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400, detail="Vault record corrupt in Redis",
        ) from exc
    if not isinstance(record, dict) or not record.get("is_vault"):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Watched folder {vault_id!r} is not registered as a vault "
                "(is_vault=True required)."
            ),
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/settings",
    response_model=BriefSettingsModel,
    summary="Get the current brief scheduler settings",
)
async def get_brief_settings() -> BriefSettingsModel:
    from app.deps import get_redis

    settings = load_brief_settings(get_redis())
    return BriefSettingsModel(
        write_to_vault=settings.write_to_vault,
        vault_id=settings.vault_id,
        vault_folder=settings.vault_folder,
    )


@router.put(
    "/settings",
    response_model=BriefSettingsModel,
    summary="Update the brief scheduler settings (RAG C3.4 vault-write toggle)",
    description=(
        "Replaces the brief settings document.  When "
        "``write_to_vault=true`` the supplied ``vault_id`` must reference "
        "a watched-folder with ``is_vault=true`` — the endpoint validates "
        "this before persisting, so an invalid configuration is rejected "
        "at PUT time rather than silently degrading at the next cron run."
    ),
)
async def put_brief_settings(body: BriefSettingsModel) -> BriefSettingsModel:
    from app.deps import get_redis

    redis_client = get_redis()

    if body.write_to_vault:
        if not body.vault_id:
            raise HTTPException(
                status_code=400,
                detail="vault_id is required when write_to_vault=true",
            )
        _assert_vault_exists(redis_client, body.vault_id)

    folder = (body.vault_folder or DEFAULT_VAULT_FOLDER).strip() or DEFAULT_VAULT_FOLDER
    settings = BriefSettings(
        write_to_vault=bool(body.write_to_vault),
        vault_id=body.vault_id if body.write_to_vault else None,
        vault_folder=folder,
    )
    save_brief_settings(redis_client, settings)
    return BriefSettingsModel(
        write_to_vault=settings.write_to_vault,
        vault_id=settings.vault_id,
        vault_folder=settings.vault_folder,
    )
