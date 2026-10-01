# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Plugin management endpoints — discover, enable, and disable plugins."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import config
from app.deps import get_redis
from config.features import is_tier_met
from core.utils import audit_log
from plugins import ENABLED_KEY, plugin_disabled_reason

router = APIRouter(tags=["plugins"])
logger = logging.getLogger("ai-companion.plugins")

# Redis key helpers
_KEY_ENABLED = ENABLED_KEY


# ── Pydantic models ──────────────────────────────────────────────────────────


class PluginInfo(BaseModel):
    """Public-facing plugin metadata."""

    name: str
    # Human-facing label — the manifest's ``display_name`` key, falling back to
    # a title-cased transform of ``name`` (``apple_mail`` → "Apple Mail").
    display_name: str
    version: str
    description: str = ""
    # Manifest ``type`` (connector | parser | agent | …). Lets clients
    # distinguish connector-backing packs from capability packs.
    plugin_type: str = "tool"
    tier_required: str = "community"
    enabled: bool = False
    status: str = Field(
        default="disabled",
        description="installed | active | error | disabled | requires_pro",
    )
    file_types: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    restart_required: bool = Field(
        default=False,
        description=(
            "True when `enabled` differs from what this server process loaded "
            "at start. Plugins load once, at start, so the change applies at "
            "the next restart."
        ),
    )
    # ``config.features.FEATURE_FLAGS`` keys this plugin's manifest declares.
    # Empty for manifests that declare none — a caller cannot tell "no flags"
    # from "field absent", so an empty list is the honest default.
    feature_flags: list[str] = Field(default_factory=list)


class PluginListResponse(BaseModel):
    """List of discovered plugins."""

    plugins: list[PluginInfo]
    total: int


# ── Helpers ───────────────────────────────────────────────────────────────────


def _plugin_dirs() -> list[Path]:
    """Every directory the loader installs plugins from.

    Delegates to ``plugins.plugin_search_dirs()`` so this router and
    ``load_plugins()`` cannot disagree about where plugins live — they did,
    and every community plugin under the top-level ``plugins/`` tree loaded
    and served while being 404 to this API.
    """
    from plugins import plugin_search_dirs

    return plugin_search_dirs()


def _discover_manifests() -> dict[str, dict[str, Any]]:
    """Scan the plugin directories and return name→manifest mapping."""
    results: dict[str, dict[str, Any]] = {}
    for base in _plugin_dirs():
        if not base.exists() or not base.is_dir():
            continue
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
    except Exception as exc:
        from core.utils.swallowed import log_swallowed_error
        log_swallowed_error('app.routers.plugins', exc)
        return None


def _set_plugin_enabled_redis(name: str, enabled: bool) -> None:
    """Persist plugin enabled state to Redis."""
    r = get_redis()
    r.set(_KEY_ENABLED.format(name=name), "1" if enabled else "0")


def _tier_required(manifest: dict[str, Any]) -> str:
    return str(manifest.get("tier_required", manifest.get("tier", "community")))


def _effective_enabled(manifest: dict[str, Any], name: str) -> bool:
    """Whether the loader would load this plugin at the next start.

    The same three answers the loader consults: the licence tier, the
    ``CERID_ENABLED_PLUGINS`` allowlist, and the stored choice.
    """
    if not is_tier_met(_tier_required(manifest)):
        return False
    return plugin_disabled_reason(name, _is_plugin_enabled_redis(name)) is None


def _restart_required(name: str, enabled: bool) -> bool:
    """Whether ``enabled`` is a choice this process has not applied yet."""
    from plugins import get_failed_plugins, get_loaded_plugins

    if name in get_loaded_plugins():
        return not enabled
    # Not loaded for any other reason (missing dependency, import error) is a
    # fault a restart does not clear.
    return enabled and any(
        info.get("name") == name and info.get("error_type") == "PluginDisabledError"
        for info in get_failed_plugins().values()
    )


def _resolve_status(manifest: dict[str, Any], enabled: bool) -> str:
    """Determine the display status for a plugin."""
    tier_required = manifest.get("tier_required", manifest.get("tier", "community"))
    if tier_required == "pro" and not is_tier_met("pro"):
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
    from plugins import manifest_display_name

    name = manifest.get("name", "unknown")
    tier_required = manifest.get("tier_required", manifest.get("tier", "community"))
    status = _resolve_status(manifest, enabled)
    return PluginInfo(
        name=name,
        display_name=manifest_display_name(manifest),
        version=manifest.get("version", "0.0.0"),
        description=manifest.get("description", ""),
        plugin_type=manifest.get("type", "tool"),
        tier_required=tier_required,
        enabled=enabled,
        status=status,
        file_types=manifest.get("file_types", []),
        capabilities=manifest.get("capabilities", []),
        restart_required=_restart_required(name, enabled),
        feature_flags=list(manifest.get("feature_flags") or []),
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/plugins", response_model=PluginListResponse)
def list_plugins() -> PluginListResponse:
    """List all discovered plugins with their status."""
    manifests = _discover_manifests()
    plugins: list[PluginInfo] = []
    for name, manifest in manifests.items():
        plugins.append(_manifest_to_info(manifest, _effective_enabled(manifest, name)))
    return PluginListResponse(plugins=plugins, total=len(plugins))


@router.get("/plugins/{name}", response_model=PluginInfo)
def get_plugin(name: str) -> PluginInfo:
    """Get detailed info for a single plugin."""
    manifests = _discover_manifests()
    if name not in manifests:
        raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found")
    manifest = manifests[name]
    return _manifest_to_info(manifest, _effective_enabled(manifest, name))


@router.post("/plugins/{name}/enable", response_model=PluginInfo)
def enable_plugin(name: str) -> PluginInfo:
    """Enable a plugin. Returns 403 if the tier or the allowlist forbids it."""
    manifests = _discover_manifests()
    if name not in manifests:
        raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found")
    manifest = manifests[name]
    tier_required = _tier_required(manifest)
    if not is_tier_met(tier_required):
        raise HTTPException(
            status_code=403,
            detail=(
                f"Plugin '{name}' requires '{tier_required}' tier "
                f"(current: '{config.FEATURE_TIER}')"
            ),
        )
    refused = plugin_disabled_reason(name, True)
    if refused:
        raise HTTPException(
            status_code=403,
            detail=f"Plugin '{name}' cannot be enabled here: {refused}",
        )
    _set_plugin_enabled_redis(name, True)
    logger.info("Plugin '%s' enabled", name)
    audit_log.audit("plugin.enable", target=name, detail={"tier_required": tier_required})
    return _manifest_to_info(manifest, True)


@router.post("/plugins/{name}/disable", response_model=PluginInfo)
def disable_plugin(name: str) -> PluginInfo:
    """Disable a plugin."""
    manifests = _discover_manifests()
    if name not in manifests:
        raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found")
    _set_plugin_enabled_redis(name, False)
    logger.info("Plugin '%s' disabled", name)
    audit_log.audit("plugin.disable", target=name)
    return _manifest_to_info(manifests[name], False)


@router.post("/plugins/scan", response_model=PluginListResponse)
def scan_plugins() -> PluginListResponse:
    """Re-scan plugin directories and return updated list."""
    logger.info(
        "Rescanning plugin directories: %s",
        ", ".join(str(d) for d in _plugin_dirs()),
    )
    manifests = _discover_manifests()
    plugins: list[PluginInfo] = []
    for name, manifest in manifests.items():
        plugins.append(_manifest_to_info(manifest, _effective_enabled(manifest, name)))
    logger.info("Scan complete: %d plugin(s) found", len(plugins))
    return PluginListResponse(plugins=plugins, total=len(plugins))
