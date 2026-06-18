# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Model assignment management — configure which LLM handles each task role."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from jinja2 import Environment, FileSystemLoader, TemplateNotFound
from pydantic import BaseModel, Field

from config.providers import PROVIDER_REGISTRY
from core.routing.model_catalog import (
    catalog_ids,
    diff_assignments,
    fetch_openrouter_catalog,
    resolve_assignments,
    resolve_latest,
)

router = APIRouter(prefix="/models", tags=["models"])
_logger = logging.getLogger("ai-companion.models")

# ── Paths ────────────────────────────────────────────────────────────────────
# In Docker the MCP server runs from /app (= src/mcp/), so the repo-relative
# path ../../stacks/bifrost doesn't exist.  Use BIFROST_CONFIG_DIR env var
# when running in a container, or fall back to repo-relative resolution.

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_MODEL_CONFIG_PATH = _DATA_DIR / "model_config.json"

# Routing-tiers overlay the smart_router reads (env-overridable via config).
# Falls back to the default app/data location when the setting is absent.
import config as _config  # noqa: E402

_ROUTING_TIERS_OVERLAY_PATH = Path(
    getattr(_config, "ROUTING_TIERS_OVERLAY_PATH", str(_DATA_DIR / "routing_tiers.json"))
)

_TEMPLATE_DIR = Path(
    os.getenv(
        "BIFROST_CONFIG_DIR",
        str(Path(__file__).resolve().parent.parent.parent.parent / "stacks" / "bifrost"),
    )
)
_BIFROST_CONFIG_PATH = _TEMPLATE_DIR / "config.yaml"
_TEMPLATE_NAME = "config.yaml.template"

# ── Defaults ─────────────────────────────────────────────────────────────────

DEFAULT_ASSIGNMENTS: dict[str, str] = {
    "coding": "anthropic/claude-sonnet-4.6",
    "research": "x-ai/grok-4.3",
    "simple": "google/gemini-2.5-flash",
    "general": "anthropic/claude-sonnet-4.6",
    "classifier": "meta-llama/llama-3.3-70b-instruct",
    "verification": "x-ai/grok-4.3",
    "categorization": "meta-llama/llama-3.3-70b-instruct:free",
    "synopsis": "meta-llama/llama-3.3-70b-instruct:free",
}

DEFAULT_FALLBACK_MODELS: list[str] = ["openai/gpt-4o-mini", "google/gemini-2.5-flash"]
DEFAULT_MONTHLY_BUDGET: float = 20.0


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


# ── Bifrost config generation ────────────────────────────────────────────────


def generate_bifrost_config(assignments: dict[str, str]) -> str:
    """Render the Bifrost config.yaml from the Jinja2 template.

    Falls back to defaults for any missing assignment keys.
    Returns the rendered YAML string.
    """
    merged = dict(DEFAULT_ASSIGNMENTS)
    merged.update(assignments)

    template_vars = {
        "coding_model": merged.get("coding", DEFAULT_ASSIGNMENTS["coding"]),
        "research_model": merged.get("research", DEFAULT_ASSIGNMENTS["research"]),
        "simple_model": merged.get("simple", DEFAULT_ASSIGNMENTS["simple"]),
        "general_model": merged.get("general", DEFAULT_ASSIGNMENTS["general"]),
        "classifier_model": merged.get("classifier", DEFAULT_ASSIGNMENTS["classifier"]),
        "fallback_models": json.dumps(DEFAULT_FALLBACK_MODELS),
        "monthly_budget": DEFAULT_MONTHLY_BUDGET,
    }

    try:
        env = Environment(  # nosec B701 — YAML config template, not HTML
            loader=FileSystemLoader(str(_TEMPLATE_DIR)),
            autoescape=False,  # YAML template, XSS not applicable
            keep_trailing_newline=True,
        )
        template = env.get_template(_TEMPLATE_NAME)
    except TemplateNotFound:
        raise FileNotFoundError(
            f"Bifrost template not found at {_TEMPLATE_DIR / _TEMPLATE_NAME}"
        )

    rendered = template.render(**template_vars)

    _BIFROST_CONFIG_PATH.write_text(rendered)
    _logger.info("Generated Bifrost config at %s", _BIFROST_CONFIG_PATH)

    return rendered


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
    """Update model assignments, persist to disk, and regenerate Bifrost config."""
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

    # Regenerate Bifrost config
    try:
        generate_bifrost_config(current)
    except FileNotFoundError as exc:
        _logger.warning("Could not regenerate Bifrost config: %s", exc)
        return UpdateResponse(
            success=True,
            restart_required=True,
            message="Assignments saved but Bifrost template not found. "
            "Config will apply on next stack rebuild.",
        )

    return UpdateResponse(
        success=True,
        restart_required=True,
        message="Assignments saved and Bifrost config regenerated. "
        "Restart Bifrost to apply changes.",
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
    if anything changed — persist the new assignments and regenerate the
    Bifrost config. Returns the applied diff. Used by the scheduler auto-update
    job and the ``POST /models/updates/apply`` endpoint. (Bifrost restart still
    required for the change to take effect, as with manual assignment edits.)"""
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
    try:
        generate_bifrost_config(result["resolved"])
    except FileNotFoundError as exc:
        _logger.warning("Bifrost config regen skipped (template missing): %s", exc)
    for row in applied:
        _logger.info("model auto-update: %s %s -> %s", row["role"], row["from"], row["to"])
    return {
        "applied": applied,
        "restart_required": True,
        "catalog_size": result["catalog_size"],
        "tier_updates": tier_diff,
    }


@router.get("/updates")
async def list_model_updates():
    """Latest in-family model updates available per role (live OpenRouter check)."""
    result = await _compute_model_updates()
    result.pop("resolved", None)
    return result


@router.post("/updates/check")
async def check_model_updates():
    """Check OpenRouter for newer in-family models per role (dry-run, no apply)."""
    result = await _compute_model_updates()
    return {
        "checked": True,
        "success": True,
        "new_updates": len(result["updates"]),
        "new_count": len(result["updates"]),
        "deprecated_count": 0,
        "updates": result["updates"],
        "last_checked": result["last_checked"],
        "catalog_size": result["catalog_size"],
    }


@router.post("/updates/apply")
async def apply_model_updates():
    """Adopt the latest in-family model for every role + regenerate Bifrost."""
    return await apply_latest_assignments()


@router.post("/updates/dismiss/{update_id}")
async def dismiss_model_update(update_id: str):
    """Dismiss a model update notification."""
    return {"dismissed": True, "id": update_id}


@router.get("/available", response_model=AvailableModelsResponse)
async def list_available_models():
    """List all models available from configured providers."""
    import os

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


@router.get("/doctor")
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
