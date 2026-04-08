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
