# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: BUSL-1.1

"""
Cerid AI Plugin System

Discovers and loads plugins from the configured plugin directory.
Each plugin must provide a manifest.json and a plugin.py with a register() function.

Plugin types:
  - parser: Registers file parsers via @register_parser
  - agent: Registers agent workflows
  - sync: Registers sync backends
  - tool: Registers custom MCP tools (merged into tool palette)
  - connector: Registers data source connectors (merged into DataSourceRegistry)

Usage:
    from plugins import load_plugins, get_loaded_plugins
    load_plugins()  # Called during app lifespan startup
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import logging
import sys
from pathlib import Path
from typing import Any

import config
from config.features import is_tier_met
from errors import ConfigError

logger = logging.getLogger("ai-companion.plugins")

# Global registry of loaded plugins
_loaded_plugins: dict[str, dict[str, Any]] = {}

# Plugins that FAILED to load, keyed by directory name. Until 2026-08-09 these
# were logged at ERROR and then discarded, so a paid feature could stop loading
# and nothing downstream could tell: /health looked fine and the capability map
# kept reporting the feature as entitled. Three Pro plugins were dead this way
# for an unknown length of time. Keeping the failures is what lets
# /health.pro_features and scripts/lint-pro-feature-health.py gate on
# "entitled but not loaded".
_failed_plugins: dict[str, dict[str, Any]] = {}

# Tool handlers registered by ToolPlugin instances (name -> async handler)
_plugin_tool_handlers: dict[str, Any] = {}

# Tool definitions registered by ToolPlugin instances (for MCP_TOOLS merge)
_plugin_tool_definitions: list[dict[str, Any]] = []


class PluginLoadError(Exception):
    """Raised when a plugin fails to load."""

    pass


class PluginDisabledError(PluginLoadError):
    """Excluded by ``CERID_ENABLED_PLUGINS``.

    An operator choice rather than a fault, but it still withholds whatever
    feature flags the plugin supplies, so it is recorded and reported.
    """


class MissingDependencyError(PluginLoadError):
    """A plugin's declared pip dependencies are not importable.

    Its own type name is what /health.pro_features shows the operator as
    ``blocked_reason``, so it is worth distinguishing from a generic load
    failure — "MissingDependency" is actionable, "PluginLoadError" is not.
    """


def _validate_manifest(manifest: dict[str, Any], plugin_dir: Path) -> None:
    """Validate plugin manifest has required fields."""
    required = ["name", "version", "type"]
    missing = [f for f in required if f not in manifest]
    if missing:
        raise PluginLoadError(
            f"Plugin at {plugin_dir}: manifest.json missing required fields: {missing}"
        )

    valid_types = ["parser", "agent", "sync", "middleware", "tool", "connector"]
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


def _find_plugin_class(module: Any) -> type | None:
    """Return the single concrete ``CeridPlugin`` subclass defined in a module.

    Class-based plugins (ConnectorPlugin / ParserPlugin / ToolPlugin / …)
    implement ``register`` as an instance method rather than a module-level
    function. Returns the class, or ``None`` if the module defines none.
    Raises ``PluginLoadError`` if it defines more than one (ambiguous entry).
    """
    from plugins.base import CeridPlugin

    candidates = [
        obj
        for _, obj in inspect.getmembers(module, inspect.isclass)
        if issubclass(obj, CeridPlugin)
        and obj.__module__ == module.__name__
        and not inspect.isabstract(obj)
    ]
    if len(candidates) > 1:
        raise PluginLoadError(
            "plugin.py defines multiple CeridPlugin subclasses: "
            f"{[c.__name__ for c in candidates]}"
        )
    return candidates[0] if candidates else None


def _load_single_plugin(plugin_dir: Path) -> dict[str, Any] | None:
    """
    Load a single plugin from its directory.

    Returns plugin info dict on success, None on skip.
    Raises PluginLoadError on failure.
    """
    manifest_path = plugin_dir / "manifest.json"
    plugin_module_path = plugin_dir / "plugin.py"

    if not manifest_path.exists():
        # Deliberately NOT recorded as a failure. A directory with no manifest
        # is not a plugin that failed to load — it is not a plugin at all
        # (`__pycache__` is the common case), and recording it would fill the
        # health surface with noise that hides the real entries. The other two
        # silent-skip paths (disabled, missing deps) ARE recorded, because both
        # withhold a feature flag that still reports enabled.
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
        # Recorded, not silent. Disabling a plugin is a legitimate operator
        # choice, but it does NOT switch off the feature flags that plugin
        # supplies — so the customer keeps being told the feature is enabled
        # while nothing serves it. Surfacing it as blocked-with-a-reason lets
        # /health.pro_features say which knob caused it, and makes narrowing
        # CERID_ENABLED_PLUGINS stop being a way to turn the gate green.
        _record_plugin_failure(
            plugin_dir,
            PluginDisabledError("not in CERID_ENABLED_PLUGINS"),
        )
        return None

    # Check feature tier requirement
    required_tier = manifest.get("tier", "community")
    if not is_tier_met(required_tier):
        logger.info(
            "Plugin '%s' requires '%s' tier (current: '%s')",
            name, required_tier, config.FEATURE_TIER,
        )
        return None

    # Check dependencies. Only a list of pip module specs gates loading; a
    # dict-form `requires` is declarative metadata (env vars, platform,
    # sibling services, TCC grants) validated elsewhere, not importable here.
    requires = manifest.get("requires", [])
    pip_deps = requires if isinstance(requires, list) else []
    missing_deps = []
    for dep in pip_deps:
        try:
            importlib.import_module(dep.split(">=")[0].split("==")[0].strip())
        except ImportError:
            missing_deps.append(dep)
    if missing_deps:
        logger.warning(
            f"Plugin '{name}' missing dependencies: {missing_deps}. "
            f"Install with: pip install {' '.join(missing_deps)}"
        )
        # Record it. A bare `return None` here is how the original defect hid:
        # meeting_capture declares pyannote.audio, that import broke, the
        # plugin vanished, and because nothing was recorded /health.pro_features
        # reported its three flags as "in_process_or_desktop" with an empty
        # `degraded` list — the very gate built to catch this stayed green.
        # A missing dependency on an ENTITLED plugin is a broken install, not
        # an operator choice (unlike ENABLED_PLUGINS, which is deliberate).
        _record_plugin_failure(
            plugin_dir,
            MissingDependencyError(f"missing dependencies: {', '.join(missing_deps)}"),
        )
        return None

    # Load the plugin module. `submodule_search_locations` makes the module a
    # package, so plugins may relative-import sibling modules
    # (e.g. `from .data_source import X`) — the in-tree connector/parser
    # plugins rely on this.
    mod_name = f"cerid_plugin_{name}"
    try:
        spec = importlib.util.spec_from_file_location(
            mod_name,
            str(plugin_module_path),
            submodule_search_locations=[str(plugin_dir)],
        )
        if spec is None or spec.loader is None:
            raise PluginLoadError(f"Plugin '{name}': failed to create module spec")

        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        spec.loader.exec_module(module)
    except PluginLoadError:
        raise
    except (ConfigError, ImportError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        raise PluginLoadError(f"Plugin '{name}': failed to import: {e}") from e

    # Resolve the registration entry point. Two supported authoring styles:
    #   1. Procedural — a module-level `register()` function.
    #   2. Class-based — a concrete CeridPlugin subclass with instance
    #      register(self). Instantiate it, expose it as module `_instance`
    #      (the tool-collection pass reads that), and use its register.
    register_fn = getattr(module, "register", None)
    if not callable(register_fn):
        plugin_instance = getattr(module, "_instance", None)
        if plugin_instance is None:
            plugin_cls = _find_plugin_class(module)
            if plugin_cls is not None:
                plugin_instance = plugin_cls()
                setattr(module, "_instance", plugin_instance)
        if plugin_instance is not None:
            register_fn = plugin_instance.register

    if not callable(register_fn):
        raise PluginLoadError(
            f"Plugin '{name}': plugin.py must define a register() function "
            "or a concrete CeridPlugin subclass"
        )

    try:
        register_fn()
    except (ConfigError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
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
        # Carried so /health.pro_features can attribute a flag to the plugin
        # that supplies it. Its absence here made the health block's positive
        # signal unreachable: the consumer reads `feature_flags` and fell back
        # to the plugin NAME, which matches a flag name for exactly one plugin.
        # Every other loaded Pro plugin was reported identically to a flag with
        # no implementation at all. _record_plugin_failure already recorded it.
        "feature_flags": list(manifest.get("feature_flags") or []),
        "module": module,
    }


def _scan_directory(base_dir: Path, loaded: list[str]) -> None:
    """Scan a single directory for plugins and load them."""
    if not base_dir.exists() or not base_dir.is_dir():
        return

    for entry in sorted(base_dir.iterdir()):
        if not entry.is_dir() or entry.name.startswith(("_", ".")):
            continue

        try:
            info = _load_single_plugin(entry)
            if info:
                _loaded_plugins[info["name"]] = info
                _failed_plugins.pop(entry.name, None)  # recovered on reload
                loaded.append(info["name"])
        except PluginLoadError as e:
            logger.error(str(e))
            _record_plugin_failure(entry, e)
        except (ConfigError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
            logger.error(f"Unexpected error loading plugin from {entry}: {e}")
            _record_plugin_failure(entry, e)


def _record_plugin_failure(entry: Path, exc: Exception) -> None:
    """Remember why a plugin did not load, so a gate can see it.

    The manifest is re-read best-effort: the load may have failed *because* the
    manifest is unreadable, and a bookkeeping helper must never raise into the
    scan loop. ``tier`` and ``feature_flags`` are what let the health surface
    decide whether this failure withheld something the customer paid for.
    """
    tier = "unknown"
    feature_flags: list[str] = []
    name = entry.name
    try:
        manifest = json.loads((entry / "manifest.json").read_text(encoding="utf-8"))
        if isinstance(manifest, dict):
            tier = str(manifest.get("tier", "community"))
            name = str(manifest.get("name", entry.name))
            flags = manifest.get("feature_flags")
            if isinstance(flags, list):
                feature_flags = [f for f in flags if isinstance(f, str)]
    except (OSError, ValueError):
        pass

    _failed_plugins[entry.name] = {
        "name": name,
        "path": str(entry),
        "tier": tier,
        "feature_flags": feature_flags,
        "error_type": type(exc).__name__,
        "error": str(exc)[:300],
    }


def load_plugins(plugin_dir: str | None = None) -> list[str]:
    """
    Discover and load all plugins from the plugin directory.

    Scans both the configured PLUGIN_DIR (defaults to src/mcp/plugins/)
    and the top-level plugins/ directory (BSL-1.1 commercial plugins).

    Args:
        plugin_dir: Override path to plugin directory. Defaults to config.PLUGIN_DIR.

    Returns:
        List of successfully loaded plugin names.
    """
    loaded: list[str] = []

    # Primary: configured plugin directory (in-tree at src/mcp/plugins/)
    primary = Path(plugin_dir or config.PLUGIN_DIR)
    _scan_directory(primary, loaded)

    # Secondary: top-level plugins/ directory (BSL-1.1 commercial plugins)
    # Only scan if not already covered by the primary path and no override
    if plugin_dir is None:
        repo_root = Path(config.PLUGIN_DIR).parent.parent.parent
        secondary = repo_root / "plugins"
        if secondary.exists() and secondary.resolve() != primary.resolve():
            logger.debug(f"Also scanning external plugin directory: {secondary}")
            _scan_directory(secondary, loaded)

    if loaded:
        logger.info(f"Loaded {len(loaded)} plugin(s): {', '.join(loaded)}")
    else:
        logger.debug("No plugins loaded")

    # Collect tool definitions from ToolPlugin instances
    from plugins.base import ToolPlugin

    for name, info in _loaded_plugins.items():
        module = info.get("module")
        if not module:
            continue
        instance = getattr(module, "_instance", None)
        if isinstance(instance, ToolPlugin):
            try:
                for tool_def in instance.get_tools():
                    tool_name = tool_def["name"]
                    handler = tool_def.pop("handler", None)
                    if handler and callable(handler):
                        _plugin_tool_handlers[tool_name] = handler
                        _plugin_tool_definitions.append(tool_def)
                        logger.info("Plugin '%s' registered tool: %s", name, tool_name)
            except (AttributeError, KeyError, TypeError) as e:
                logger.error("Plugin '%s': failed to collect tools: %s", name, e)

    return loaded


def get_loaded_plugins() -> dict[str, dict[str, Any]]:
    """Return info about all loaded plugins (without module references)."""
    return {
        name: {k: v for k, v in info.items() if k != "module"}
        for name, info in _loaded_plugins.items()
    }


def get_failed_plugins() -> dict[str, dict[str, Any]]:
    """Return the plugins that failed to load this process, keyed by directory.

    Consumed by ``/health.pro_features`` and
    ``scripts/lint-pro-feature-health.py`` to turn a silent ERROR log into a
    gate.
    """
    return {name: dict(info) for name, info in _failed_plugins.items()}


def discover_plugins(plugin_dir: str | None = None) -> list[dict[str, Any]]:
    """
    Scan a directory for plugin manifests without loading them.

    Returns a list of manifest dicts for each valid plugin found.
    Useful for UI display of available (but not necessarily loaded) plugins.
    """
    base_dir = Path(plugin_dir or config.PLUGIN_DIR)

    if not base_dir.exists() or not base_dir.is_dir():
        return []

    manifests = []
    for entry in sorted(base_dir.iterdir()):
        if not entry.is_dir() or entry.name.startswith(("_", ".")):
            continue

        manifest_path = entry / "manifest.json"
        if not manifest_path.exists():
            continue

        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["_dir"] = str(entry)
            manifest["_loaded"] = manifest.get("name", "") in _loaded_plugins
            manifests.append(manifest)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to read manifest at %s: %s", manifest_path, e)

    return manifests


def register_all_plugins(plugin_dir: str | None = None) -> list[str]:
    """
    Convenience function for startup — discovers and loads all plugins.

    This is the main entry point called during app lifespan startup.
    Equivalent to load_plugins() but with a clearer name for the startup path.
    """
    return load_plugins(plugin_dir)
