# Copyright 2026 Cerid AI. Apache-2.0 license.
"""Tests for plugin discovery, manifest validation, and tier gating."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestPluginDiscovery:
    """Test that the loader discovers plugins from directory structure."""

    def test_discover_skips_hidden_dirs(self, tmp_path):
        """Hidden directories (starting with . or _) are skipped."""
        from plugins import _loaded_plugins, load_plugins

        _loaded_plugins.clear()

        hidden = tmp_path / ".hidden_plugin"
        hidden.mkdir()
        (hidden / "manifest.json").write_text(
            json.dumps({"name": "hidden", "version": "1.0.0", "type": "parser"})
        )
        (hidden / "plugin.py").write_text("def register(): pass\n")

        result = load_plugins(str(tmp_path))
        assert result == []

    def test_discover_multiple_plugins(self, tmp_path):
        """Multiple valid plugins in one directory all load."""
        from plugins import _loaded_plugins, load_plugins

        _loaded_plugins.clear()

        for name in ["alpha", "beta", "gamma"]:
            d = tmp_path / name
            d.mkdir()
            (d / "manifest.json").write_text(
                json.dumps({"name": name, "version": "1.0.0", "type": "parser"})
            )
            (d / "plugin.py").write_text("def register(): pass\n")

        result = load_plugins(str(tmp_path))
        assert sorted(result) == ["alpha", "beta", "gamma"]


class TestManifestValidation:
    """Test manifest.json schema validation."""

    def test_missing_name_field(self, tmp_path):
        """Manifest without 'name' fails validation."""
        from plugins import _loaded_plugins, load_plugins

        _loaded_plugins.clear()

        d = tmp_path / "bad"
        d.mkdir()
        (d / "manifest.json").write_text(
            json.dumps({"version": "1.0.0", "type": "parser"})
        )
        (d / "plugin.py").write_text("def register(): pass\n")

        result = load_plugins(str(tmp_path))
        assert result == []

    def test_invalid_type_field(self, tmp_path):
        """Manifest with unknown type fails validation."""
        from plugins import _loaded_plugins, load_plugins

        _loaded_plugins.clear()

        d = tmp_path / "bad_type"
        d.mkdir()
        (d / "manifest.json").write_text(
            json.dumps({"name": "bad_type", "version": "1.0.0", "type": "unknown"})
        )
        (d / "plugin.py").write_text("def register(): pass\n")

        result = load_plugins(str(tmp_path))
        assert result == []

    def test_malformed_json(self, tmp_path):
        """Malformed JSON in manifest fails gracefully."""
        from plugins import _loaded_plugins, load_plugins

        _loaded_plugins.clear()

        d = tmp_path / "malformed"
        d.mkdir()
        (d / "manifest.json").write_text("{not valid json")
        (d / "plugin.py").write_text("def register(): pass\n")

        result = load_plugins(str(tmp_path))
        assert result == []


class TestTierGating:
    """Test that plugins with tier requirements are skipped when tier is too low."""

    def test_pro_plugin_skipped_on_community(self, tmp_path):
        """A pro-tier plugin should not load on community tier."""
        from plugins import _loaded_plugins, load_plugins

        _loaded_plugins.clear()

        d = tmp_path / "pro_plugin"
        d.mkdir()
        (d / "manifest.json").write_text(
            json.dumps({"name": "pro_plugin", "version": "1.0.0", "type": "parser", "tier": "pro"})
        )
        (d / "plugin.py").write_text("def register(): pass\n")

        with patch("plugins.is_tier_met", return_value=False):
            result = load_plugins(str(tmp_path))
        assert result == []

    def test_pro_plugin_loads_on_pro_tier(self, tmp_path):
        """A pro-tier plugin should load when tier is met."""
        from plugins import _loaded_plugins, load_plugins

        _loaded_plugins.clear()

        d = tmp_path / "pro_plugin"
        d.mkdir()
        (d / "manifest.json").write_text(
            json.dumps({"name": "pro_plugin", "version": "1.0.0", "type": "parser", "tier": "pro"})
        )
        (d / "plugin.py").write_text("def register(): pass\n")

        with patch("plugins.is_tier_met", return_value=True):
            result = load_plugins(str(tmp_path))
        assert "pro_plugin" in result


class TestFeatureFlagGating:
    """A plugin's declared manifest.feature_flags must gate loading, not just
    get collected for health-surface reporting (AF-081)."""

    def test_plugin_skipped_when_declared_flag_disabled(self, tmp_path):
        from plugins import _failed_plugins, _loaded_plugins, load_plugins

        _loaded_plugins.clear()
        _failed_plugins.clear()

        d = tmp_path / "flagged_plugin"
        d.mkdir()
        (d / "manifest.json").write_text(
            json.dumps({
                "name": "flagged_plugin", "version": "1.0.0", "type": "parser",
                "feature_flags": ["some_disabled_flag"],
            })
        )
        (d / "plugin.py").write_text("def register(): pass\n")

        with patch("plugins.is_feature_enabled", return_value=False):
            result = load_plugins(str(tmp_path))
        assert result == []
        assert "flagged_plugin" in _failed_plugins
        assert _failed_plugins["flagged_plugin"]["error_type"] == "FeatureFlagDisabledError"

    def test_plugin_loads_when_declared_flag_enabled(self, tmp_path):
        from plugins import _loaded_plugins, load_plugins

        _loaded_plugins.clear()

        d = tmp_path / "flagged_plugin"
        d.mkdir()
        (d / "manifest.json").write_text(
            json.dumps({
                "name": "flagged_plugin", "version": "1.0.0", "type": "parser",
                "feature_flags": ["some_enabled_flag"],
            })
        )
        (d / "plugin.py").write_text("def register(): pass\n")

        with patch("plugins.is_feature_enabled", return_value=True):
            result = load_plugins(str(tmp_path))
        assert "flagged_plugin" in result

    def test_plugin_with_no_declared_flags_is_unaffected(self, tmp_path):
        """Absence of `feature_flags` in the manifest must not gate anything."""
        from plugins import _loaded_plugins, load_plugins

        _loaded_plugins.clear()

        d = tmp_path / "unflagged_plugin"
        d.mkdir()
        (d / "manifest.json").write_text(
            json.dumps({"name": "unflagged_plugin", "version": "1.0.0", "type": "parser"})
        )
        (d / "plugin.py").write_text("def register(): pass\n")

        result = load_plugins(str(tmp_path))
        assert "unflagged_plugin" in result

    def test_real_voice_memos_manifest_flag_now_gates_loading(self):
        """AF-081: `voice_memos_watch` was declared in the real manifest
        (src/mcp/plugins/voice_memos/manifest.json) but had zero readers
        anywhere — flipping it had no effect. It's now read by the same
        admission check exercised above; this pins that against the real
        plugin directory, not just a synthetic one."""
        import plugins as plugins_mod
        from plugins import _load_single_plugin, _loaded_plugins
        root = Path(plugins_mod.__file__).resolve().parent / "voice_memos"

        _loaded_plugins.clear()
        with patch("plugins.is_feature_enabled", return_value=False):
            info = _load_single_plugin(root)
        assert info is None
