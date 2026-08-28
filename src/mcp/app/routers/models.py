# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Model assignment management — configure which LLM handles each task role."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from config.providers import PROVIDER_REGISTRY
from core.routing.model_catalog import (
    catalog_ids,
    diff_assignments,
    fetch_openrouter_catalog,
    resolve_assignments,
    resolve_latest,
)


# --- Response models (generated: single-return dict-literal routes) ---
class CheckModelUpdatesResponse(BaseModel):
    checked: bool
    success: bool
    new_updates: Any
    new_count: Any
    deprecated_count: int
    updates: Any
    last_checked: Any
    catalog_size: Any


class DismissModelUpdateResponse(BaseModel):
    dismissed: bool
    id: Any



router = APIRouter(prefix="/models", tags=["models"])
_logger = logging.getLogger("ai-companion.models")

# ── Paths ────────────────────────────────────────────────────────────────────

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_MODEL_CONFIG_PATH = _DATA_DIR / "model_config.json"

# Routing-tiers overlay the smart_router reads (env-overridable via config).
# Falls back to the default app/data location when the setting is absent.
import config as _config  # noqa: E402

_ROUTING_TIERS_OVERLAY_PATH = Path(
    getattr(_config, "ROUTING_TIERS_OVERLAY_PATH", str(_DATA_DIR / "routing_tiers.json"))
)

# ── Defaults ─────────────────────────────────────────────────────────────────

DEFAULT_ASSIGNMENTS: dict[str, str] = {
    "coding": "anthropic/claude-sonnet-4.6",
    "research": "x-ai/grok-4.3",
    "simple": "google/gemini-2.5-flash",
    "general": "anthropic/claude-sonnet-4.6",
    "classifier": "meta-llama/llama-3.3-70b-instruct",
    "verification": "x-ai/grok-4.3",
    # Retired upstream (404) as of 2026-08-27 — see config/settings.py
    # CATEGORIZE_MODELS for the full note.
    "categorization": "openai/gpt-4o-mini",
    "synopsis": "openai/gpt-4o-mini",
}

# ── Pydantic models ─────────────────────────────────────────────────────────


class ModelAssignments(BaseModel):
    assignments: dict[str, str] = Field(
        ..., description="Mapping of role name to model ID"
    )


class AssignmentsResponse(BaseModel):
    assignments: dict[str, str]
    source: str = Field(description="'user_config' or 'defaults'")


class UpdateResponse(BaseModel):
    success: bool
    restart_required: bool
    message: str


class AvailableModel(BaseModel):
    model_id: str
    provider: str
    display_name: str


class AvailableModelsResponse(BaseModel):
    models: list[AvailableModel]
    total: int


class ModelCatalogResponse(BaseModel):
    ids: list[str]  # currently-dispatchable openrouter/-prefixed model ids
    source: str  # "live_catalog" | "unavailable"
    count: int


# ── Persistence helpers ──────────────────────────────────────────────────────


def _load_config() -> dict:
    """Load persisted model config, returning defaults if file missing."""
    if _MODEL_CONFIG_PATH.exists():
        try:
            raw = json.loads(_MODEL_CONFIG_PATH.read_text())
            return raw
        except (json.JSONDecodeError, KeyError) as exc:
            _logger.warning("Corrupt model_config.json, using defaults: %s", exc)
    return {
        "version": 1,
        "assignments": dict(DEFAULT_ASSIGNMENTS),
        "updated_at": None,
    }


def _save_config(assignments: dict[str, str]) -> None:
    """Persist model assignments to data/model_config.json."""
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "assignments": assignments,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _MODEL_CONFIG_PATH.write_text(json.dumps(payload, indent=2) + "\n")
    _logger.info("Saved model config to %s", _MODEL_CONFIG_PATH)


def _get_all_known_models() -> set[str]:
    """Collect every model ID from the provider registry."""
    models: set[str] = set()
    for entry in PROVIDER_REGISTRY.values():
        for m in entry.get("models", []):
            # Strip the openrouter/ prefix for comparison
            models.add(m)
            if m.startswith("openrouter/"):
                models.add(m[len("openrouter/"):])
    return models


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/assignments", response_model=AssignmentsResponse)
async def get_assignments():
    """Return current model assignments for every task role."""
    config = _load_config()
    assignments = config.get("assignments", dict(DEFAULT_ASSIGNMENTS))

    # Ensure all default roles are present (forward-compat when new roles added)
    merged = dict(DEFAULT_ASSIGNMENTS)
    merged.update(assignments)

    source = "user_config" if _MODEL_CONFIG_PATH.exists() else "defaults"
    return AssignmentsResponse(assignments=merged, source=source)


@router.put("/assignments", response_model=UpdateResponse)
async def update_assignments(body: ModelAssignments):
    """Update model assignments and persist to disk (applied live, no restart)."""
    if not body.assignments:
        raise HTTPException(status_code=422, detail="assignments must not be empty")

    # Validate role names
    valid_roles = set(DEFAULT_ASSIGNMENTS.keys())
    unknown_roles = set(body.assignments.keys()) - valid_roles
    if unknown_roles:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown role(s): {', '.join(sorted(unknown_roles))}. "
            f"Valid roles: {', '.join(sorted(valid_roles))}",
        )

    # Validate model IDs against configured providers
    known_models = _get_all_known_models()
    for role, model_id in body.assignments.items():
        if not model_id or not model_id.strip():
            raise HTTPException(
                status_code=422, detail=f"Empty model ID for role '{role}'"
            )
        # Allow any model ID that matches known models or follows provider/model pattern
        # This is lenient: users may use models not yet in the registry
        if model_id not in known_models and "/" not in model_id:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid model ID '{model_id}' for role '{role}'. "
                f"Model IDs should use 'provider/model-name' format.",
            )

    # Merge with existing — only update provided roles
    current = _load_config().get("assignments", dict(DEFAULT_ASSIGNMENTS))
    current.update(body.assignments)

    _save_config(current)

    # Role assignments are read live via _current_assignments() (e.g. chat.py's
    # smart-route fallback + the /doctor compat report), so a saved change applies
    # to the next request — no restart needed. (The Bifrost config regeneration
    # this used to trigger was removed: Bifrost was retired 2026-04-17.)
    return UpdateResponse(
        success=True,
        restart_required=False,
        message="Assignments saved — applied immediately.",
    )


def _current_assignments() -> dict[str, str]:
    """Active role→model map: defaults overlaid with persisted user config."""
    merged = dict(DEFAULT_ASSIGNMENTS)
    merged.update(_load_config().get("assignments", {}))
    return merged


async def _compute_model_updates() -> dict:
    """Resolve the latest in-family model for every role against the live
    OpenRouter catalog. Pure read — no persistence. Empty catalog (offline /
    fetch failure) yields no updates rather than an error."""
    import config as _settings  # module-level `config` is shadowed by a param elsewhere

    current = _current_assignments()
    ids = catalog_ids(await fetch_openrouter_catalog())
    # Hardware-compatibility guard: the auto-update never adopts a model the
    # active platform can't run (e.g. a Metal-crash model on amd-mac).
    profile = getattr(_settings, "CERID_HARDWARE_PROFILE", "")
    resolved = resolve_assignments(current, ids, hardware_profile=profile) if ids else dict(current)
    updates = diff_assignments(current, resolved)
    # E1 CR-075: stamp each update with a stable id so a dismissal pins the
    # target — a later, newer target for the same role surfaces as a fresh
    # (undismissed) update. Pure metadata; apply ignores it.
    for _u in updates:
        _u["id"] = _update_id(_u)
    return {
        "updates": updates,
        "new": updates,
        "deprecated": [],
        "last_checked": datetime.now(timezone.utc).isoformat(),
        "catalog_size": len(ids),
        "resolved": resolved,
        "catalog_ids": ids,
    }


def _refresh_routing_tiers_overlay(catalog_ids_list: list[str]) -> list[dict[str, str]]:
    """Resolve every smart-router tier id through the catalog and persist the
    ``{original_id: resolved_id}`` overlay the router reads.

    Conservative semantics match the role pass: ``resolve_latest`` only upgrades
    dotted-version ids within the same family (never cross-series). Only changed
    ids are written into the map (identity entries are omitted to keep it small;
    the router treats a missing key as identity). Empty catalog → no write.
    Returns the per-id diff rows ``{id, from, to}`` for logging. Fail soft: any
    write error is logged and swallowed so the weekly job never errors on it.
    """
    if not catalog_ids_list:
        return []

    from core.routing.smart_router import tier_source_ids

    overlay: dict[str, str] = {}
    diff: list[dict[str, str]] = []
    for src in tier_source_ids():
        resolved = resolve_latest(src, catalog_ids_list)
        if resolved != src:
            overlay[src] = resolved
            diff.append({"id": src, "from": src, "to": resolved})

    try:
        _ROUTING_TIERS_OVERLAY_PATH.parent.mkdir(parents=True, exist_ok=True)
        _ROUTING_TIERS_OVERLAY_PATH.write_text(json.dumps(overlay, indent=2) + "\n")
    except OSError as exc:
        from core.utils.swallowed import log_swallowed_error

        log_swallowed_error("models.routing_tiers_overlay", exc)
        return diff

    for row in diff:
        _logger.info("routing-tiers overlay: %s -> %s", row["from"], row["to"])
    return diff


async def apply_latest_assignments() -> dict:
    """Fetch the catalog, resolve the latest in-family model per role, and —
    if anything changed — persist the new assignments. Returns the applied diff.
    Used by the scheduler auto-update job and the ``POST /models/updates/apply``
    endpoint. Changes apply immediately (assignments are read live), so no restart
    is required."""
    result = await _compute_model_updates()
    applied: list[dict[str, str]] = result["updates"]

    # Tier-overlay refresh runs every pass, independent of the role diff: the
    # smart-router tier tables upgrade on their own cadence, so an empty role
    # diff must NOT skip the tier refresh.
    tier_diff = _refresh_routing_tiers_overlay(result.get("catalog_ids", []))

    if not applied:
        return {
            "applied": [],
            "restart_required": False,
            "catalog_size": result["catalog_size"],
            "tier_updates": tier_diff,
        }

    _save_config(result["resolved"])
    for row in applied:
        _logger.info("model auto-update: %s %s -> %s", row["role"], row["from"], row["to"])
    return {
        "applied": applied,
        "restart_required": False,
        "catalog_size": result["catalog_size"],
        "tier_updates": tier_diff,
    }


# E1 CR-075: dismissals are persisted to a global Redis set so a dismissed
# update notification stays dismissed across polls, instead of the old
# stateless no-op that let _compute_model_updates re-surface it every time.
# Only the notification surfaces (list/check) filter; the explicit apply path is
# deliberately unaffected (dismissing hides a nag, it does not veto adoption).
_DISMISSED_UPDATES_KEY = "cerid:model_updates:dismissed"


def _update_id(update: dict) -> str:
    """Stable id for a role's in-family update — pins the target model."""
    return f"{update['role']}:{update['to']}"


def _dismissed_update_ids() -> set[str]:
    """The set of dismissed update ids. Fail open (empty) so a Redis outage
    shows all updates rather than silently hiding them."""
    try:
        from app.deps import get_redis
        raw = get_redis().smembers(_DISMISSED_UPDATES_KEY)
    except Exception as exc:  # noqa: BLE001 — best-effort; fail open
        from core.utils.swallowed import log_swallowed_error
        log_swallowed_error("models.dismissed_update_ids", exc)
        return set()
    return {m.decode() if isinstance(m, bytes) else m for m in raw}


def _drop_dismissed(updates: list[dict]) -> list[dict]:
    dismissed = _dismissed_update_ids()
    return [u for u in updates if u.get("id") not in dismissed]


@router.get("/updates")  # response-model-allowed: dynamic response (shape varies)
async def list_model_updates():
    """Latest in-family model updates available per role (live OpenRouter check)."""
    result = await _compute_model_updates()
    result.pop("resolved", None)
    visible = _drop_dismissed(result["updates"])
    result["updates"] = visible
    result["new"] = visible
    return result


@router.post("/updates/check", response_model=CheckModelUpdatesResponse)
async def check_model_updates():
    """Check OpenRouter for newer in-family models per role (dry-run, no apply)."""
    result = await _compute_model_updates()
    visible = _drop_dismissed(result["updates"])
    return {
        "checked": True,
        "success": True,
        "new_updates": len(visible),
        "new_count": len(visible),
        "deprecated_count": 0,
        "updates": visible,
        "last_checked": result["last_checked"],
        "catalog_size": result["catalog_size"],
    }


@router.post("/updates/apply")  # response-model-allowed: dynamic response (shape varies)
async def apply_model_updates():
    """Adopt the latest in-family model for every role + regenerate Bifrost."""
    return await apply_latest_assignments()


@router.post("/updates/dismiss/{update_id}", response_model=DismissModelUpdateResponse)
async def dismiss_model_update(update_id: str):
    """Dismiss a model update notification — persisted so it stays dismissed
    across polls (E1 CR-075; formerly a stateless no-op)."""
    try:
        from app.deps import get_redis
        get_redis().sadd(_DISMISSED_UPDATES_KEY, update_id)
    except Exception as exc:  # noqa: BLE001 — best-effort persistence
        from core.utils.swallowed import log_swallowed_error
        log_swallowed_error("models.dismiss_model_update", exc)
    return {"dismissed": True, "id": update_id}


@router.get("/available", response_model=AvailableModelsResponse)
async def list_available_models():
    """List all models available from configured providers."""

    models: list[AvailableModel] = []
    seen: set[str] = set()

    for name, entry in PROVIDER_REGISTRY.items():
        # Ollama: include when enabled (no API key needed)
        if name == "ollama":
            ollama_on = os.getenv("OLLAMA_ENABLED", "false").lower() in ("true", "1")
            if not ollama_on:
                continue
        else:
            env_var = entry.get("env_var", "")
            api_key = os.getenv(env_var, "") if env_var else ""
            key_set = bool(api_key) or not entry.get("requires_api_key", True)
            if not key_set:
                continue

        for model_id in entry.get("models", []):
            if model_id in seen:
                continue
            seen.add(model_id)
            models.append(
                AvailableModel(
                    model_id=model_id,
                    provider=entry["display_name"],
                    display_name=model_id.split("/")[-1] if "/" in model_id else model_id,
                )
            )

    return AvailableModelsResponse(models=models, total=len(models))


@router.get("/catalog", response_model=ModelCatalogResponse)
async def get_model_catalog():
    """Authoritative set of currently-dispatchable model ids, validated against
    the live OpenRouter catalog (its public ``/models`` endpoint — no key).

    The FE filters its static display catalog against this set so a delisted id
    (E1 CR-004) cannot render as selectable — structural drift prevention rather
    than a hardcoded list that silently rots. Fail-soft: an unreachable/empty
    catalog returns ``source="unavailable"`` with no ids, and the FE then shows
    its full catalog rather than over-filtering to nothing.
    """
    catalog = await fetch_openrouter_catalog()
    if not catalog:
        return ModelCatalogResponse(ids=[], source="unavailable", count=0)
    ids = sorted(f"openrouter/{cid}" for cid in catalog_ids(catalog))
    return ModelCatalogResponse(ids=ids, source="live_catalog", count=len(ids))


@router.get("/doctor")  # response-model-allowed: dynamic response (shape varies)
async def model_doctor():
    """Audit the live model config against the active hardware profile + the
    OpenRouter catalog.

    Surfaces: hardware-incompatible pins (error), dead remote pins (warn), and
    local-model currency vs the known-good set (info), plus validate-on-device
    upgrade candidates. Consumed by the setup wizard + Settings → Models UX so
    the operator always sees whether the configured models are the most capable
    ones compatible with their hardware. Read-only; never mutates config.
    """
    import config as _settings
    from core.routing.model_compat import build_compat_report

    profile = getattr(_settings, "CERID_HARDWARE_PROFILE", "")
    provider = getattr(_settings, "INTERNAL_LLM_PROVIDER", "")

    configured: dict[str, str] = dict(_current_assignments())  # OpenRouter roles
    local_roles: dict[str, str] = {}

    internal_model = getattr(_settings, "INTERNAL_LLM_MODEL", "")
    if internal_model:
        configured["INTERNAL_LLM_MODEL"] = internal_model
        # Only a local provider makes INTERNAL_LLM_MODEL a local "chat" pin;
        # under openrouter it's a remote id (dead-pin/incompat checks apply).
        if provider in ("quenchforge", "ollama"):
            local_roles["INTERNAL_LLM_MODEL"] = "chat"
    for var, role in (
        ("OLLAMA_DEFAULT_MODEL", "chat"),
        ("QUENCHFORGE_EMBED_MODEL", "embed"),
        ("QUENCHFORGE_RERANK_MODEL", "rerank"),
    ):
        val = getattr(_settings, var, "")
        if val:
            configured[var] = val
            local_roles[var] = role

    ids = catalog_ids(await fetch_openrouter_catalog())
    return build_compat_report(
        configured=configured,
        hardware_profile=profile,
        catalog_ids=ids,
        local_roles=local_roles,
    )
