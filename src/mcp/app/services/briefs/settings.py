# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Brief scheduler settings — vault-write toggle and target vault (RAG C3.4).

Stores the operator-level toggle that decides whether the cron-scheduled
``BriefGenerationJob`` / ``WeeklySynthesisJob`` write their generated
brief markdown back to a user vault.  The default is OFF — matching
Cerid's "no surprise side effects" principle from the C3.4 design.

Storage shape (Redis, single key)
---------------------------------
Key: ``cerid:briefs:settings``
Value (JSON):
    {
      "write_to_vault": bool,
      "vault_id": str | None,
      "vault_folder": str | None,
    }

The scheduler reads these via :func:`load_brief_settings` at enqueue
time so the toggle takes effect on the next scheduled run without a
process restart.  The router at :mod:`app.routers.brief_settings`
writes via :func:`save_brief_settings`.

This module deliberately keeps the schema tiny — three fields — because
any larger surface should grow into a richer brief-configuration UI
rather than expanding this opt-in toggle.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("ai-companion.briefs.settings")

_REDIS_KEY = "cerid:briefs:settings"

__all__ = [
    "BriefSettings",
    "DEFAULT_VAULT_FOLDER",
    "load_brief_settings",
    "save_brief_settings",
]


# Mirror brief_generation / weekly_synthesis default — keep them in sync.
DEFAULT_VAULT_FOLDER = "_briefs"


@dataclass(frozen=True, slots=True)
class BriefSettings:
    """Operator-controlled toggles for the brief scheduler.

    Attributes:
        write_to_vault: When True, scheduled briefs write their markdown
            back to ``vault_id`` after persistence to Neo4j.  Default
            False — must be explicitly enabled by the operator.
        vault_id: Watched-folder ID for the target vault.  Required when
            ``write_to_vault`` is True; ignored otherwise.
        vault_folder: Path prefix under the vault root.  Default
            ``"_briefs"`` so Cerid-authored notes stay segregated.
    """

    write_to_vault: bool = False
    vault_id: str | None = None
    vault_folder: str = DEFAULT_VAULT_FOLDER

    def to_payload_fields(self) -> dict[str, Any]:
        """Serialise into the dict the scheduler merges into JobRecord payloads.

        The shape matches the ``__init__`` kwargs on
        :class:`BriefGenerationJob` / :class:`WeeklySynthesisJob`, so the
        scheduler can splat them in directly.
        """
        return {
            "write_to_vault": bool(self.write_to_vault),
            "vault_id": self.vault_id,
            "vault_folder": self.vault_folder or DEFAULT_VAULT_FOLDER,
        }


def load_brief_settings(redis_client: Any) -> BriefSettings:
    """Read brief settings from Redis, returning defaults on any failure.

    Read failures (Redis down, JSON corrupt) intentionally fall back to
    the safe default (vault-write OFF) — the scheduler must continue
    running even when Redis hiccups.
    """
    try:
        raw = redis_client.get(_REDIS_KEY)
    except Exception as exc:  # noqa: BLE001 — observability boundary
        from core.utils.swallowed import log_swallowed_error

        log_swallowed_error("briefs.settings.load", exc)
        return BriefSettings()

    if not raw:
        return BriefSettings()
    try:
        data = json.loads(raw) if isinstance(raw, (bytes, str)) else raw
    except (TypeError, ValueError) as exc:
        from core.utils.swallowed import log_swallowed_error

        log_swallowed_error("briefs.settings.parse", exc)
        return BriefSettings()
    if not isinstance(data, dict):
        return BriefSettings()

    return BriefSettings(
        write_to_vault=bool(data.get("write_to_vault", False)),
        vault_id=(
            str(data["vault_id"])
            if data.get("vault_id")
            else None
        ),
        vault_folder=(
            str(data["vault_folder"]).strip()
            if data.get("vault_folder")
            else DEFAULT_VAULT_FOLDER
        ),
    )


def save_brief_settings(redis_client: Any, settings: BriefSettings) -> None:
    """Persist brief settings to Redis.

    Validation is the router's responsibility — by the time this is
    called the inputs have already been checked.  Writes are single-key,
    so no atomicity concerns.
    """
    payload = {
        "write_to_vault": bool(settings.write_to_vault),
        "vault_id": settings.vault_id,
        "vault_folder": settings.vault_folder or DEFAULT_VAULT_FOLDER,
    }
    redis_client.set(_REDIS_KEY, json.dumps(payload))
    logger.info(
        "brief_settings saved write_to_vault=%s vault_id=%s vault_folder=%s",
        payload["write_to_vault"], payload["vault_id"], payload["vault_folder"],
    )
