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

    CONNECTORS = [
        "gmail",
        "outlook",
        "google_calendar",
        "outlook_calendar",
        "apple_calendar",
        "apple_photos",
    ]

    def _plugin_root(self) -> Path:
        import plugins

        return Path(plugins.__file__).resolve().parent

    def test_each_pro_connector_loads_when_tier_met(self):
        from plugins import _load_single_plugin, _loaded_plugins

        root = self._plugin_root()
        with patch("plugins.is_tier_met", return_value=True):
            for name in self.CONNECTORS:
                _loaded_plugins.clear()
                info = _load_single_plugin(root / name)
                assert info is not None, f"connector {name!r} failed to load"
                assert info["type"] == "connector"

    def test_connector_registers_data_source_when_enabled(self):
        from app.data_sources import registry
        from plugins import _load_single_plugin, _loaded_plugins

        root = self._plugin_root()
        with patch("plugins.is_tier_met", return_value=True), patch(
            "config.features.is_feature_enabled", return_value=True
        ):
            _loaded_plugins.clear()
            info = _load_single_plugin(root / "gmail")
            assert info is not None

        assert registry.get("gmail") is not None

    def test_full_boot_loads_connectors_and_keeps_existing_plugins(self):
        """Real load_plugins() over both trees: connectors load, no regression."""
        from plugins import _loaded_plugins, load_plugins

        _loaded_plugins.clear()
        with patch("plugins.is_tier_met", return_value=True):
            loaded = load_plugins()

        for name in self.CONNECTORS:
            assert name in loaded, f"{name!r} did not load at boot"
        # A previously-working top-level plugin still loads (regression guard).
        assert "github-issues" in loaded
