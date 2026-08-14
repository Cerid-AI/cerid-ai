# Copyright 2026 Cerid AI. Apache-2.0 license.
"""Tests for class-based plugin loading + the Pro-connector boot guard.

The loader historically only resolved a *module-level* ``register()``. The
in-tree ``src/mcp/plugins/`` connector + parser plugins are ``CeridPlugin``
subclasses with an *instance* ``register(self)``, declare ``requires`` as a
dict of declarative metadata, and use relative imports of sibling modules.
All three traits silently prevented them from loading. These tests lock in
the fix and guard the Pro connectors against regressing.
"""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestClassBasedPluginLoading:
    """The loader must support plugins authored as CeridPlugin subclasses."""

    def test_instance_register_runs(self, tmp_path):
        """A plugin defining only an instance register() loads and runs it."""
        from parsers import PARSER_REGISTRY
        from plugins import _loaded_plugins, load_plugins

        _loaded_plugins.clear()
        d = tmp_path / "classy"
        d.mkdir()
        (d / "manifest.json").write_text(
            json.dumps({"name": "classy", "version": "1.0.0", "type": "parser"})
        )
        (d / "plugin.py").write_text(textwrap.dedent("""
            from plugins.base import ParserPlugin

            def _parse(path):
                return {"text": "", "file_type": "classy", "page_count": None}

            class ClassyPlugin(ParserPlugin):
                @property
                def name(self):
                    return "classy"

                @property
                def version(self):
                    return "1.0.0"

                def get_parsers(self):
                    return {".classyext": _parse}
        """))

        PARSER_REGISTRY.pop(".classyext", None)
        result = load_plugins(str(tmp_path))

        assert "classy" in result
        # register() actually ran — the parser is wired into the registry.
        assert ".classyext" in PARSER_REGISTRY
        PARSER_REGISTRY.pop(".classyext", None)

    def test_relative_import_resolves(self, tmp_path):
        """plugin.py may relative-import a sibling module within its package."""
        from plugins import _loaded_plugins, load_plugins

        _loaded_plugins.clear()
        d = tmp_path / "relimp"
        d.mkdir()
        (d / "manifest.json").write_text(
            json.dumps({"name": "relimp", "version": "1.0.0", "type": "parser"})
        )
        (d / "helper.py").write_text(
            "def parse(path):\n    return {'text': '', 'file_type': 'r', 'page_count': None}\n"
        )
        (d / "plugin.py").write_text(textwrap.dedent("""
            from plugins.base import ParserPlugin
            from .helper import parse

            class RelPlugin(ParserPlugin):
                @property
                def name(self):
                    return "relimp"

                @property
                def version(self):
                    return "1.0.0"

                def get_parsers(self):
                    return {".relext": parse}
        """))

        result = load_plugins(str(tmp_path))
        assert "relimp" in result

    def test_module_level_register_still_works(self, tmp_path):
        """Back-compat: procedural module-level register() still loads."""
        from plugins import _loaded_plugins, load_plugins

        _loaded_plugins.clear()
        d = tmp_path / "proc"
        d.mkdir()
        (d / "manifest.json").write_text(
            json.dumps({"name": "proc", "version": "1.0.0", "type": "parser"})
        )
        (d / "plugin.py").write_text("def register():\n    pass\n")

        result = load_plugins(str(tmp_path))
        assert "proc" in result


class TestAgentPluginRouteCollection:
    """RA-66: AgentPlugin.get_routes() must be collected and mounted."""

    def test_routes_mounted_on_app_when_provided(self, tmp_path):
        from plugins import _loaded_plugins, load_plugins

        _loaded_plugins.clear()
        d = tmp_path / "routey"
        d.mkdir()
        (d / "manifest.json").write_text(
            json.dumps({"name": "routey", "version": "1.0.0", "type": "agent"})
        )
        (d / "plugin.py").write_text(textwrap.dedent("""
            from fastapi import APIRouter
            from plugins.base import AgentPlugin

            router = APIRouter()

            @router.get("/routey/ping")
            def _ping():
                return {"ok": True}

            class RouteyPlugin(AgentPlugin):
                @property
                def name(self):
                    return "routey"

                @property
                def version(self):
                    return "1.0.0"

                def get_routes(self):
                    return [router]
        """))

        fake_app = type("FakeApp", (), {"included": []})()
        fake_app.include_router = lambda r: fake_app.included.append(r)

        result = load_plugins(str(tmp_path), app=fake_app)

        assert "routey" in result
        assert len(fake_app.included) == 1

    def test_routes_collected_without_app_does_not_raise(self, tmp_path):
        """No app instance (e.g. discovery-only callers) — collection is a no-op, not an error."""
        from plugins import _loaded_plugins, load_plugins

        _loaded_plugins.clear()
        d = tmp_path / "routeynoapp"
        d.mkdir()
        (d / "manifest.json").write_text(
            json.dumps({"name": "routeynoapp", "version": "1.0.0", "type": "agent"})
        )
        (d / "plugin.py").write_text(textwrap.dedent("""
            from fastapi import APIRouter
            from plugins.base import AgentPlugin

            router = APIRouter()

            class RouteyNoAppPlugin(AgentPlugin):
                @property
                def name(self):
                    return "routeynoapp"

                @property
                def version(self):
                    return "1.0.0"

                def get_routes(self):
                    return [router]
        """))

        result = load_plugins(str(tmp_path))
        assert "routeynoapp" in result


class TestSyncBackendPluginCollection:
    """RA-66: SyncBackendPlugin.get_backend_class() must be collected into a lookup table."""

    def test_backend_registered_by_name(self, tmp_path):
        from plugins import _loaded_plugins, get_registered_sync_backends, load_plugins

        _loaded_plugins.clear()
        d = tmp_path / "s3sync"
        d.mkdir()
        (d / "manifest.json").write_text(
            json.dumps({"name": "s3sync", "version": "1.0.0", "type": "sync"})
        )
        (d / "plugin.py").write_text(textwrap.dedent("""
            from plugins.base import SyncBackendPlugin

            class S3Backend:
                pass

            class S3SyncPlugin(SyncBackendPlugin):
                @property
                def name(self):
                    return "s3sync"

                @property
                def version(self):
                    return "1.0.0"

                def get_backend_class(self):
                    return S3Backend

                def get_backend_name(self):
                    return "s3"
        """))

        result = load_plugins(str(tmp_path))

        assert "s3sync" in result
        backends = get_registered_sync_backends()
        assert "s3" in backends
        assert backends["s3"].__name__ == "S3Backend"


class TestToolPluginCollection:
    """RA-63: ToolPlugin.get_tools() must be merged into
    ``app.tools.get_all_tools()`` and routable via ``execute_tool()`` —
    previously collected into ``_plugin_tool_definitions`` /
    ``_plugin_tool_handlers`` with zero readers, so a conforming plugin
    loaded, logged "registered tool", and was absent from tools/list while
    tools/call raised "Unknown tool"."""

    async def test_tool_registered_appears_in_palette_and_dispatches(self, tmp_path):
        import plugins as plugins_mod
        from app.tools import execute_tool, get_all_tools
        from plugins import _loaded_plugins, load_plugins

        _loaded_plugins.clear()
        # _plugin_tool_definitions / _plugin_tool_handlers are module globals
        # appended to by every load_plugins() call in the process — snapshot
        # and restore so this test doesn't leak a tool into unrelated tests
        # that assert an exact get_all_tools() set (e.g.
        # test_external_mcp_dispatch.py::test_get_all_tools_returns_only_built_ins_when_no_external).
        original_defs = list(plugins_mod._plugin_tool_definitions)
        original_handlers = dict(plugins_mod._plugin_tool_handlers)

        d = tmp_path / "toolplug"
        d.mkdir()
        (d / "manifest.json").write_text(
            json.dumps({"name": "toolplug", "version": "1.0.0", "type": "tool"})
        )
        (d / "plugin.py").write_text(textwrap.dedent("""
            from plugins.base import ToolPlugin

            async def _handle_ping(arguments):
                return {"pong": arguments.get("x")}

            class ToolPlugPlugin(ToolPlugin):
                @property
                def name(self):
                    return "toolplug"

                @property
                def version(self):
                    return "1.0.0"

                def get_tools(self):
                    return [{
                        "name": "plg_toolplug_ping",
                        "description": "Ping",
                        "inputSchema": {"type": "object", "properties": {}},
                        "handler": _handle_ping,
                    }]
        """))

        try:
            result = load_plugins(str(tmp_path))
            assert "toolplug" in result

            names = {t["name"] for t in get_all_tools()}
            assert "plg_toolplug_ping" in names

            outcome = await execute_tool("plg_toolplug_ping", {"x": 42})
            assert outcome == {"pong": 42}
        finally:
            plugins_mod._plugin_tool_definitions.clear()
            plugins_mod._plugin_tool_definitions.extend(original_defs)
            plugins_mod._plugin_tool_handlers.clear()
            plugins_mod._plugin_tool_handlers.update(original_handlers)


class TestOnStartupHook:
    """RA-64: the loader must call CeridPlugin.on_startup() after every
    plugin in the pass has registered — previously nothing ever called it."""

    def test_on_startup_called_after_load(self, tmp_path):
        from plugins import _loaded_plugins, load_plugins

        _loaded_plugins.clear()
        d = tmp_path / "startupy"
        d.mkdir()
        (d / "manifest.json").write_text(
            json.dumps({"name": "startupy", "version": "1.0.0", "type": "parser"})
        )
        (d / "plugin.py").write_text(textwrap.dedent("""
            from plugins.base import ParserPlugin

            def _parse(path):
                return {"text": "", "file_type": "startupy", "page_count": None}

            class StartupyPlugin(ParserPlugin):
                started = False

                @property
                def name(self):
                    return "startupy"

                @property
                def version(self):
                    return "1.0.0"

                def get_parsers(self):
                    return {".startupyext": _parse}

                def on_startup(self):
                    StartupyPlugin.started = True
        """))

        result = load_plugins(str(tmp_path))
        assert "startupy" in result

        info = _loaded_plugins["startupy"]
        instance = info["module"]._instance
        assert type(instance).started is True

    def test_on_startup_failure_does_not_break_load_plugins(self, tmp_path):
        """A raising on_startup() is caught and logged, not propagated."""
        from plugins import _loaded_plugins, load_plugins

        _loaded_plugins.clear()
        d = tmp_path / "boomy"
        d.mkdir()
        (d / "manifest.json").write_text(
            json.dumps({"name": "boomy", "version": "1.0.0", "type": "parser"})
        )
        (d / "plugin.py").write_text(textwrap.dedent("""
            from plugins.base import ParserPlugin

            def _parse(path):
                return {"text": "", "file_type": "boomy", "page_count": None}

            class BoomyPlugin(ParserPlugin):
                @property
                def name(self):
                    return "boomy"

                @property
                def version(self):
                    return "1.0.0"

                def get_parsers(self):
                    return {".boomyext": _parse}

                def on_startup(self):
                    raise RuntimeError("boom")
        """))

        # register() itself succeeded (the plugin loaded); only its
        # on_startup() hook blows up, and that must not propagate out of
        # load_plugins() or take down every other plugin's startup pass.
        result = load_plugins(str(tmp_path))
        assert "boomy" in result

    def test_procedural_plugin_has_no_instance_to_call(self, tmp_path):
        """Module-level register() plugins have no _instance — skipped, not errored."""
        from plugins import _loaded_plugins, load_plugins

        _loaded_plugins.clear()
        d = tmp_path / "proc_startup"
        d.mkdir()
        (d / "manifest.json").write_text(
            json.dumps({"name": "proc_startup", "version": "1.0.0", "type": "parser"})
        )
        (d / "plugin.py").write_text("def register():\n    pass\n")

        result = load_plugins(str(tmp_path))
        assert "proc_startup" in result


class TestDictRequires:
    """`requires` as a dict is declarative metadata, not pip dependencies."""

    def test_dict_requires_does_not_skip(self, tmp_path):
        from plugins import _loaded_plugins, load_plugins

        _loaded_plugins.clear()
        d = tmp_path / "dictreq"
        d.mkdir()
        (d / "manifest.json").write_text(json.dumps({
            "name": "dictreq",
            "version": "1.0.0",
            "type": "parser",
            "requires": {"env": ["SOME_VAR"], "sibling_services": ["svc"]},
        }))
        (d / "plugin.py").write_text("def register():\n    pass\n")

        result = load_plugins(str(tmp_path))
        assert "dictreq" in result

    def test_list_requires_still_gates_on_missing_pip_dep(self, tmp_path):
        """A list of pip specs still skips the plugin when a module is absent."""
        from plugins import _loaded_plugins, load_plugins

        _loaded_plugins.clear()
        d = tmp_path / "listreq"
        d.mkdir()
        (d / "manifest.json").write_text(json.dumps({
            "name": "listreq",
            "version": "1.0.0",
            "type": "parser",
            "requires": ["a_module_that_does_not_exist_xyz"],
        }))
        (d / "plugin.py").write_text("def register():\n    pass\n")

        result = load_plugins(str(tmp_path))
        assert result == []


class TestProConnectorBoot:
    """Boot guard: the in-tree Pro connectors load when their tier is met."""

    # apple_reminders is deliberately absent: its backend plugin was removed
    # 2026-08-12 (the Linux MCP container can never run ceridreminders); the
    # feature is desktop-implemented and declared in NON_PLUGIN_IMPLEMENTATIONS.
    CONNECTORS = [
        "gmail",
        "outlook",
        "google_calendar",
        "outlook_calendar",
        "apple_calendar",
        "apple_photos",
        "apple_mail",
    ]

    def _plugin_root(self) -> Path:
        import plugins

        return Path(plugins.__file__).resolve().parent

    def test_each_pro_connector_loads_when_tier_met(self):
        from plugins import _load_single_plugin, _loaded_plugins

        root = self._plugin_root()
        # Both tier AND each connector's declared feature_flags (e.g.
        # gmail_connector) must be simulated — the real FEATURE_FLAGS dict
        # still reflects the test process's actual community tier, and the
        # admission check now reads it independently of is_tier_met.
        with (
            patch("plugins.is_tier_met", return_value=True),
            patch("plugins.is_feature_enabled", return_value=True),
        ):
            for name in self.CONNECTORS:
                _loaded_plugins.clear()
                info = _load_single_plugin(root / name)
                assert info is not None, f"connector {name!r} failed to load"
                assert info["type"] == "connector"

    def test_connector_registers_data_source_when_enabled(self):
        from app.data_sources import registry
        from plugins import _load_single_plugin, _loaded_plugins

        root = self._plugin_root()
        # Two separate call sites need two separate patches: the admission
        # check added to plugins/__init__.py captured its own module-level
        # `from config.features import is_feature_enabled` binding (patched
        # via "plugins.is_feature_enabled", same convention as is_tier_met
        # above); gmail/plugin.py's own register() does its own *lazy*
        # `from config.features import is_feature_enabled` inside the
        # function, which resolves the name fresh against config.features
        # at call time, so it needs the patch on the source module instead.
        with (
            patch("plugins.is_tier_met", return_value=True),
            patch("plugins.is_feature_enabled", return_value=True),
            patch("config.features.is_feature_enabled", return_value=True),
        ):
            _loaded_plugins.clear()
            info = _load_single_plugin(root / "gmail")
            assert info is not None

        assert registry.get("gmail") is not None

    def test_full_boot_loads_connectors_and_keeps_existing_plugins(self):
        """Real load_plugins() over both trees: connectors load, no regression."""
        from plugins import _loaded_plugins, load_plugins

        _loaded_plugins.clear()
        with (
            patch("plugins.is_tier_met", return_value=True),
            patch("plugins.is_feature_enabled", return_value=True),
        ):
            loaded = load_plugins()

        for name in self.CONNECTORS:
            assert name in loaded, f"{name!r} did not load at boot"
        # A previously-working top-level plugin still loads (regression guard).
        assert "github-issues" in loaded
