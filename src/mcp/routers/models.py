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
