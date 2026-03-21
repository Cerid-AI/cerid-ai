# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Plugin management endpoints — discover, enable, configure, and disable plugins."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import config
from deps import get_redis

router = APIRouter(tags=["plugins"])
logger = logging.getLogger("ai-companion.plugins")

# Redis key helpers
_KEY_ENABLED = "cerid:plugins:{name}:enabled"
_KEY_CONFIG = "cerid:plugins:{name}:config"


# ── Pydantic models ──────────────────────────────────────────────────────────


class PluginInfo(BaseModel):
    """Public-facing plugin metadata."""

    name: str
    version: str
    description: str = ""
    tier_required: str = "community"
    enabled: bool = False
    status: str = Field(
        default="disabled",
        description="installed | active | error | disabled | requires_pro",
    )
    file_types: list[str] = Field(default_factory=list)
    config_schema: dict[str, Any] | None = None
    capabilities: list[str] = Field(default_factory=list)


class PluginConfig(BaseModel):
    """Arbitrary key-value configuration for a plugin."""

    values: dict[str, Any] = Field(default_factory=dict)


class PluginListResponse(BaseModel):
    """List of discovered plugins."""

    plugins: list[PluginInfo]
    total: int


# ── Helpers ───────────────────────────────────────────────────────────────────


def _plugin_dir() -> Path:
    """Resolve the plugin directory."""
    return Path(config.PLUGIN_DIR)


def _discover_manifests() -> dict[str, dict[str, Any]]:
    """Scan the plugin directory and return name→manifest mapping."""
    base = _plugin_dir()
    if not base.exists() or not base.is_dir():
        return {}

    results: dict[str, dict[str, Any]] = {}
    for entry in sorted(base.iterdir()):
        if not entry.is_dir() or entry.name.startswith(("_", ".")):
            continue
        manifest_path = entry / "manifest.json"
        if not manifest_path.exists():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            name = manifest.get("name", entry.name)
            manifest["_dir"] = str(entry)
            results[name] = manifest
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read manifest at %s: %s", manifest_path, exc)
    return results


def _is_plugin_enabled_redis(name: str) -> bool | None:
    """Check Redis for explicit enabled/disabled state. Returns None if unset."""
    try:
        r = get_redis()
        val = r.get(_KEY_ENABLED.format(name=name))
        if val is None:
            return None
        return val.decode() == "1" if isinstance(val, bytes) else str(val) == "1"
    except Exception:
        return None


def _set_plugin_enabled_redis(name: str, enabled: bool) -> None:
    """Persist plugin enabled state to Redis."""
    r = get_redis()
    r.set(_KEY_ENABLED.format(name=name), "1" if enabled else "0")


def _get_plugin_config_redis(name: str) -> dict[str, Any]:
    """Read plugin configuration from Redis."""
    try:
        r = get_redis()
        raw = r.get(_KEY_CONFIG.format(name=name))
        if raw is None:
            return {}
        return json.loads(raw.decode() if isinstance(raw, bytes) else str(raw))
    except Exception:
        return {}


def _set_plugin_config_redis(name: str, cfg: dict[str, Any]) -> None:
    """Write plugin configuration to Redis."""
    r = get_redis()
    r.set(_KEY_CONFIG.format(name=name), json.dumps(cfg))


def _resolve_status(manifest: dict[str, Any], enabled: bool) -> str:
    """Determine the display status for a plugin."""
    tier_required = manifest.get("tier_required", manifest.get("tier", "community"))
    if tier_required == "pro" and config.FEATURE_TIER != "pro":
        return "requires_pro"
    if not enabled:
        return "disabled"
    # Check if the plugin is actually loaded in memory
    from plugins import get_loaded_plugins

    loaded = get_loaded_plugins()
    if manifest.get("name") in loaded:
        return "active"
    return "installed"


def _manifest_to_info(manifest: dict[str, Any], enabled: bool) -> PluginInfo:
    """Convert a raw manifest + enabled flag to PluginInfo."""
    name = manifest.get("name", "unknown")
    tier_required = manifest.get("tier_required", manifest.get("tier", "community"))
    status = _resolve_status(manifest, enabled)
    return PluginInfo(
        name=name,
        version=manifest.get("version", "0.0.0"),
        description=manifest.get("description", ""),
        tier_required=tier_required,
        enabled=enabled,
        status=status,
        file_types=manifest.get("file_types", []),
        config_schema=manifest.get("config_schema"),
        capabilities=manifest.get("capabilities", []),
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/plugins", response_model=PluginListResponse)
def list_plugins() -> PluginListResponse:
    """List all discovered plugins with their status."""
    manifests = _discover_manifests()
    plugins: list[PluginInfo] = []
    for name, manifest in manifests.items():
        redis_enabled = _is_plugin_enabled_redis(name)
        enabled = redis_enabled if redis_enabled is not None else False
        plugins.append(_manifest_to_info(manifest, enabled))
    return PluginListResponse(plugins=plugins, total=len(plugins))


@router.get("/plugins/{name}", response_model=PluginInfo)
def get_plugin(name: str) -> PluginInfo:
    """Get detailed info for a single plugin."""
    manifests = _discover_manifests()
    if name not in manifests:
        raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found")
    manifest = manifests[name]
    redis_enabled = _is_plugin_enabled_redis(name)
    enabled = redis_enabled if redis_enabled is not None else False
    return _manifest_to_info(manifest, enabled)


@router.post("/plugins/{name}/enable", response_model=PluginInfo)
def enable_plugin(name: str) -> PluginInfo:
    """Enable a plugin. Returns 403 if the tier requirement is not met."""
    manifests = _discover_manifests()
    if name not in manifests:
        raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found")
    manifest = manifests[name]
    tier_required = manifest.get("tier_required", manifest.get("tier", "community"))
    if tier_required == "pro" and config.FEATURE_TIER != "pro":
        raise HTTPException(
            status_code=403,
            detail=f"Plugin '{name}' requires 'pro' tier (current: '{config.FEATURE_TIER}')",
        )
    _set_plugin_enabled_redis(name, True)
    logger.info("Plugin '%s' enabled", name)
    return _manifest_to_info(manifest, True)


@router.post("/plugins/{name}/disable", response_model=PluginInfo)
def disable_plugin(name: str) -> PluginInfo:
    """Disable a plugin."""
    manifests = _discover_manifests()
    if name not in manifests:
        raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found")
    _set_plugin_enabled_redis(name, False)
    logger.info("Plugin '%s' disabled", name)
    return _manifest_to_info(manifests[name], False)


@router.get("/plugins/{name}/config", response_model=PluginConfig)
def get_plugin_config(name: str) -> PluginConfig:
    """Get the configuration for a plugin."""
    manifests = _discover_manifests()
    if name not in manifests:
        raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found")
    return PluginConfig(values=_get_plugin_config_redis(name))


@router.put("/plugins/{name}/config", response_model=PluginConfig)
def update_plugin_config(name: str, body: PluginConfig) -> PluginConfig:
    """Update the configuration for a plugin."""
    manifests = _discover_manifests()
    if name not in manifests:
        raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found")
    _set_plugin_config_redis(name, body.values)
    logger.info("Plugin '%s' config updated", name)
    return PluginConfig(values=_get_plugin_config_redis(name))


@router.post("/plugins/scan", response_model=PluginListResponse)
def scan_plugins() -> PluginListResponse:
    """Re-scan plugin directories and return updated list."""
    logger.info("Rescanning plugin directory: %s", _plugin_dir())
    manifests = _discover_manifests()
    plugins: list[PluginInfo] = []
    for name, manifest in manifests.items():
        redis_enabled = _is_plugin_enabled_redis(name)
        enabled = redis_enabled if redis_enabled is not None else False
        plugins.append(_manifest_to_info(manifest, enabled))
    logger.info("Scan complete: %d plugin(s) found", len(plugins))
    return PluginListResponse(plugins=plugins, total=len(plugins))
