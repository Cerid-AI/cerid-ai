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

router = APIRouter(prefix="/models", tags=["models"])
_logger = logging.getLogger("ai-companion.models")

# ── Paths ────────────────────────────────────────────────────────────────────
# In Docker the MCP server runs from /app (= src/mcp/), so the repo-relative
# path ../../stacks/bifrost doesn't exist.  Use BIFROST_CONFIG_DIR env var
# when running in a container, or fall back to repo-relative resolution.

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_MODEL_CONFIG_PATH = _DATA_DIR / "model_config.json"

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
    "research": "x-ai/grok-4.1-fast",
    "simple": "google/gemini-2.5-flash",
    "general": "openai/gpt-4o-mini",
    "classifier": "meta-llama/llama-3.3-70b-instruct",
    "verification": "x-ai/grok-4.1-fast",
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


class ModelUpdateItem(BaseModel):
    update_id: str
    model_id: str
    update_type: str = Field(description="'new', 'deprecated', or 'price_change'")
    details: dict = Field(default_factory=dict)
    detected_at: str


class ModelUpdatesFullResponse(BaseModel):
    updates: list[ModelUpdateItem]
    last_checked: str | None
    catalog_size: int


class ModelComparisonResponse(BaseModel):
    current: dict
    candidate: dict
    recommendation: str


# ── Deprecation metadata ────────────────────────────────────────────────────

DEPRECATED_MODELS: dict[str, dict[str, str]] = {
    "openai/gpt-4-turbo": {
        "successor": "openai/gpt-4o",
        "reason": "Superseded by GPT-4o",
        "deprecated_date": "2025-06",
    },
    "openai/gpt-4-turbo-preview": {
        "successor": "openai/gpt-4o",
        "reason": "Preview model retired",
        "deprecated_date": "2025-03",
    },
    "anthropic/claude-3-opus": {
        "successor": "anthropic/claude-opus-4",
        "reason": "Superseded by Claude Opus 4",
        "deprecated_date": "2025-09",
    },
    "anthropic/claude-3-sonnet": {
        "successor": "anthropic/claude-sonnet-4",
        "reason": "Superseded by Claude Sonnet 4",
        "deprecated_date": "2025-06",
    },
    "anthropic/claude-3-haiku": {
        "successor": "anthropic/claude-sonnet-4",
        "reason": "Superseded by Claude Sonnet 4",
        "deprecated_date": "2025-06",
    },
    "google/gemini-pro": {
        "successor": "google/gemini-2.5-flash",
        "reason": "Superseded by Gemini 2.5 series",
        "deprecated_date": "2025-04",
    },
}


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


@router.get("/updates", response_model=ModelUpdatesFullResponse)
async def get_model_updates():
    """Return pending model updates (new, deprecated, price changes)."""
    from deps import get_redis

    redis = get_redis()
    dismissed_raw = redis.get("cerid:model_updates:dismissed")
    dismissed: set[str] = set()
    if dismissed_raw:
        try:
            dismissed = set(json.loads(dismissed_raw))
        except (ValueError, TypeError):
            pass  # JSON decode: use empty set

    updates: list[ModelUpdateItem] = []
    last_checked: str | None = None
    catalog_size = 0

    raw = redis.get("cerid:models:updates")
    if raw:
        data = json.loads(raw)
        last_checked = data.get("last_checked")
        catalog_size = data.get("catalog_size", 0)

        for m in data.get("new", []):
            uid = f"new:{m['id']}"
            if uid in dismissed:
                continue
            pricing = m.get("pricing", {})
            updates.append(ModelUpdateItem(
                update_id=uid,
                model_id=m["id"],
                update_type="new",
                details={
                    "name": m.get("name", m["id"]),
                    "context_length": m.get("context_length"),
                    "input_cost": float(pricing.get("prompt", 0)) * 1e6,
                    "output_cost": float(pricing.get("completion", 0)) * 1e6,
                },
                detected_at=last_checked or "",
            ))

        for m in data.get("deprecated", []):
            uid = f"deprecated:{m['id']}"
            if uid in dismissed:
                continue
            dep_info = DEPRECATED_MODELS.get(m["id"], {})
            updates.append(ModelUpdateItem(
                update_id=uid,
                model_id=m["id"],
                update_type="deprecated",
                details={
                    "successor": dep_info.get("successor"),
                    "reason": dep_info.get("reason", "Removed from catalog"),
                    "deprecated_date": dep_info.get("deprecated_date"),
                },
                detected_at=last_checked or "",
            ))

    # Add deprecation warnings for models currently in use
    config = _load_config()
    assignments = config.get("assignments", {})
    for _role, model_id in assignments.items():
        stripped = model_id.split(":")[0]
        if stripped in DEPRECATED_MODELS:
            uid = f"in_use_deprecated:{stripped}"
            if uid in dismissed:
                continue
            dep = DEPRECATED_MODELS[stripped]
            if not any(u.update_id == uid for u in updates):
                updates.append(ModelUpdateItem(
                    update_id=uid,
                    model_id=stripped,
                    update_type="deprecated",
                    details={
                        "successor": dep.get("successor"),
                        "reason": dep.get("reason"),
                        "deprecated_date": dep.get("deprecated_date"),
                        "in_use": True,
                    },
                    detected_at=last_checked or "",
                ))

    return ModelUpdatesFullResponse(
        updates=updates, last_checked=last_checked, catalog_size=catalog_size,
    )


@router.post("/updates/check")
async def trigger_model_update_check():
    """Trigger a manual check for model updates against OpenRouter catalog."""
    from utils.model_registry import fetch_and_compare_models

    try:
        result = await fetch_and_compare_models()
        return {
            "success": True,
            "new_count": len(result.get("new", [])),
            "deprecated_count": len(result.get("deprecated", [])),
            "catalog_size": result.get("catalog_size", 0),
            "last_checked": result.get("last_checked"),
        }
    except (OSError, RuntimeError, ValueError) as exc:
        _logger.warning("Model update check failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Update check failed: {exc}")


@router.post("/updates/dismiss/{update_id:path}")
async def dismiss_model_update(update_id: str):
    """Dismiss a model update notification."""
    from deps import get_redis

    redis = get_redis()
    dismissed_raw = redis.get("cerid:model_updates:dismissed")
    dismissed: list[str] = []
    if dismissed_raw:
        try:
            dismissed = json.loads(dismissed_raw)
        except (ValueError, TypeError):
            pass  # JSON decode: use empty list

    if update_id not in dismissed:
        dismissed.append(update_id)

    redis.set("cerid:model_updates:dismissed", json.dumps(dismissed))
    return {"success": True, "dismissed": update_id}


@router.get("/compare", response_model=ModelComparisonResponse)
async def compare_models(current_model: str, candidate_model: str):
    """Compare two models on capability and cost."""
    import httpx as _httpx

    from utils.model_registry import get_pricing

    async with _httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get("https://openrouter.ai/api/v1/models")
        resp.raise_for_status()
        catalog = {m["id"]: m for m in resp.json().get("data", [])}

    def _build_info(model_id: str) -> dict:
        stripped = model_id.removeprefix("openrouter/").split(":")[0]
        entry = catalog.get(stripped, {})
        pricing = get_pricing(f"openrouter/{stripped}")
        raw_pricing = entry.get("pricing", {})
        return {
            "model_id": model_id,
            "name": entry.get("name", model_id),
            "context_length": entry.get("context_length"),
            "input_cost_per_1m": pricing[0] if pricing[0] else float(raw_pricing.get("prompt", 0)) * 1e6,
            "output_cost_per_1m": pricing[1] if pricing[1] else float(raw_pricing.get("completion", 0)) * 1e6,
            "top_provider": entry.get("top_provider", {}),
            "architecture": entry.get("architecture", {}),
            "deprecated": stripped in DEPRECATED_MODELS,
            "deprecation_info": DEPRECATED_MODELS.get(stripped),
        }

    current_info = _build_info(current_model)
    candidate_info = _build_info(candidate_model)

    # Simple recommendation logic
    rec_parts: list[str] = []
    c_cost = current_info["input_cost_per_1m"] + current_info["output_cost_per_1m"]
    n_cost = candidate_info["input_cost_per_1m"] + candidate_info["output_cost_per_1m"]

    if current_info["deprecated"]:
        rec_parts.append(f"{current_model} is deprecated")
    if n_cost < c_cost:
        savings = ((c_cost - n_cost) / c_cost * 100) if c_cost > 0 else 0
        rec_parts.append(f"Candidate is {savings:.0f}% cheaper")
    elif n_cost > c_cost:
        increase = ((n_cost - c_cost) / c_cost * 100) if c_cost > 0 else 0
        rec_parts.append(f"Candidate is {increase:.0f}% more expensive")

    c_ctx = current_info.get("context_length") or 0
    n_ctx = candidate_info.get("context_length") or 0
    if n_ctx > c_ctx:
        rec_parts.append(f"Candidate has {n_ctx // 1000}K context (vs {c_ctx // 1000}K)")

    recommendation = ". ".join(rec_parts) + "." if rec_parts else "Models are comparable."

    return ModelComparisonResponse(
        current=current_info, candidate=candidate_info, recommendation=recommendation,
    )
