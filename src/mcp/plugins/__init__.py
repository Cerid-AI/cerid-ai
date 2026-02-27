# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Cerid AI Plugin System

Discovers and loads plugins from the configured plugin directory.
Each plugin must provide a manifest.json and a plugin.py with a register() function.

Plugin types:
  - parser: Registers file parsers via @register_parser
  - agent: Registers agent workflows
  - sync: Registers sync backends

Usage:
    from plugins import load_plugins, get_loaded_plugins
    load_plugins()  # Called during app lifespan startup
"""

from __future__ import annotations

import importlib.util
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import config

logger = logging.getLogger("ai-companion.plugins")

# Global registry of loaded plugins
_loaded_plugins: Dict[str, Dict[str, Any]] = {}


class PluginLoadError(Exception):
    """Raised when a plugin fails to load."""

    pass


def _validate_manifest(manifest: Dict[str, Any], plugin_dir: Path) -> None:
    """Validate plugin manifest has required fields."""
    required = ["name", "version", "type"]
    missing = [f for f in required if f not in manifest]
    if missing:
        raise PluginLoadError(
            f"Plugin at {plugin_dir}: manifest.json missing required fields: {missing}"
        )

    valid_types = ["parser", "agent", "sync", "middleware"]
    if manifest["type"] not in valid_types:
        raise PluginLoadError(
            f"Plugin '{manifest['name']}': invalid type '{manifest['type']}'. "
            f"Must be one of: {valid_types}"
        )


def _is_plugin_enabled(name: str) -> bool:
    """Check if a plugin is enabled via config."""
    # If ENABLED_PLUGINS is set, only those plugins are loaded
    enabled = config.ENABLED_PLUGINS
    if enabled:
        return name in enabled
    # Otherwise auto-discover all plugins in the directory
    return True


def _load_single_plugin(plugin_dir: Path) -> Optional[Dict[str, Any]]:
    """
    Load a single plugin from its directory.

    Returns plugin info dict on success, None on skip.
    Raises PluginLoadError on failure.
    """
    manifest_path = plugin_dir / "manifest.json"
    plugin_module_path = plugin_dir / "plugin.py"

    if not manifest_path.exists():
        logger.debug(f"Skipping {plugin_dir.name}: no manifest.json")
        return None

    if not plugin_module_path.exists():
        raise PluginLoadError(
            f"Plugin at {plugin_dir}: manifest.json found but no plugin.py"
        )

    # Load and validate manifest
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise PluginLoadError(
            f"Plugin at {plugin_dir}: invalid manifest.json: {e}"
        ) from e

    _validate_manifest(manifest, plugin_dir)
    name = manifest["name"]

    # Check if enabled
    if not _is_plugin_enabled(name):
        logger.info(f"Plugin '{name}' skipped (not in ENABLED_PLUGINS)")
        return None

    # Check feature tier requirement
    required_tier = manifest.get("tier", "community")
    if required_tier == "pro" and config.FEATURE_TIER != "pro":
        logger.info(
            f"Plugin '{name}' requires 'pro' tier (current: '{config.FEATURE_TIER}')"
        )
        return None

    # Check dependencies
    requires = manifest.get("requires", [])
    missing_deps = []
    for dep in requires:
        try:
            importlib.import_module(dep.split(">=")[0].split("==")[0].strip())
        except ImportError:
            missing_deps.append(dep)
    if missing_deps:
        logger.warning(
            f"Plugin '{name}' missing dependencies: {missing_deps}. "
            f"Install with: pip install {' '.join(missing_deps)}"
        )
        return None

    # Load the plugin module
    try:
        spec = importlib.util.spec_from_file_location(
            f"cerid_plugin_{name}", str(plugin_module_path)
        )
        if spec is None or spec.loader is None:
            raise PluginLoadError(f"Plugin '{name}': failed to create module spec")

        module = importlib.util.module_from_spec(spec)
        sys.modules[f"cerid_plugin_{name}"] = module
        spec.loader.exec_module(module)
    except PluginLoadError:
        raise
    except Exception as e:
        raise PluginLoadError(f"Plugin '{name}': failed to import: {e}") from e

    # Call register()
    register_fn = getattr(module, "register", None)
    if not callable(register_fn):
        raise PluginLoadError(
            f"Plugin '{name}': plugin.py must define a register() function"
        )

    try:
        register_fn()
    except Exception as e:
        raise PluginLoadError(
            f"Plugin '{name}': register() failed: {e}"
        ) from e

    logger.info(
        f"Plugin loaded: {name} v{manifest['version']} (type: {manifest['type']})"
    )

    return {
        "name": name,
        "version": manifest["version"],
        "type": manifest["type"],
        "description": manifest.get("description", ""),
        "tier": required_tier,
        "module": module,
    }


def load_plugins(plugin_dir: Optional[str] = None) -> List[str]:
    """
    Discover and load all plugins from the plugin directory.

    Args:
        plugin_dir: Override path to plugin directory. Defaults to config.PLUGIN_DIR.

    Returns:
        List of successfully loaded plugin names.
    """
    base_dir = Path(plugin_dir or config.PLUGIN_DIR)

    if not base_dir.exists():
        logger.debug(f"Plugin directory does not exist: {base_dir}")
        return []

    if not base_dir.is_dir():
        logger.warning(f"Plugin path is not a directory: {base_dir}")
        return []

    loaded = []

    for entry in sorted(base_dir.iterdir()):
        if not entry.is_dir() or entry.name.startswith(("_", ".")):
            continue

        try:
            info = _load_single_plugin(entry)
            if info:
                _loaded_plugins[info["name"]] = info
                loaded.append(info["name"])
        except PluginLoadError as e:
            logger.error(str(e))
        except Exception as e:
            logger.error(f"Unexpected error loading plugin from {entry}: {e}")

    if loaded:
        logger.info(f"Loaded {len(loaded)} plugin(s): {', '.join(loaded)}")
    else:
        logger.debug("No plugins loaded")

    return loaded


def get_loaded_plugins() -> Dict[str, Dict[str, Any]]:
    """Return info about all loaded plugins (without module references)."""
    return {
        name: {k: v for k, v in info.items() if k != "module"}
        for name, info in _loaded_plugins.items()
    }


def get_plugin(name: str) -> Optional[Dict[str, Any]]:
    """Get a specific loaded plugin by name."""
    return _loaded_plugins.get(name)