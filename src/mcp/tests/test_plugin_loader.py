# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for plugin discovery, manifest validation, and tier gating (Phase 46)."""

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

        underscored = tmp_path / "_internal"
        underscored.mkdir()
        (underscored / "manifest.json").write_text(
            json.dumps({"name": "internal", "version": "1.0.0", "type": "parser"})
        )
        (underscored / "plugin.py").write_text("def register(): pass\n")

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

        d = tmp_path / "bad_json"
        d.mkdir()
        (d / "manifest.json").write_text("{invalid json!!!")
        (d / "plugin.py").write_text("def register(): pass\n")

        result = load_plugins(str(tmp_path))
        assert result == []


class TestTierGating:
    """Test pro/community tier gating."""

    def test_pro_plugin_blocked_in_community(self, tmp_path):
        """Pro-tier plugins are skipped in community mode."""
        from plugins import _loaded_plugins, load_plugins

        _loaded_plugins.clear()

        d = tmp_path / "pro_only"
        d.mkdir()
        (d / "manifest.json").write_text(
            json.dumps({
                "name": "pro_only",
                "version": "1.0.0",
                "type": "parser",
                "tier": "pro",
            })
        )
        (d / "plugin.py").write_text("def register(): pass\n")

        with patch("config.FEATURE_TIER", "community"), \
             patch("config.features.FEATURE_TIER", "community"):
            result = load_plugins(str(tmp_path))
        assert result == []

    @patch("config.FEATURE_TIER", "pro")
    @patch("config.features.FEATURE_TIER", "pro")
    def test_pro_plugin_loads_in_pro(self, tmp_path):
        """Pro-tier plugins load when tier is pro."""
        from plugins import _loaded_plugins, load_plugins

        _loaded_plugins.clear()

        d = tmp_path / "pro_ok"
        d.mkdir()
        (d / "manifest.json").write_text(
            json.dumps({
                "name": "pro_ok",
                "version": "1.0.0",
                "type": "parser",
                "tier": "pro",
            })
        )
        (d / "plugin.py").write_text("def register(): pass\n")

        result = load_plugins(str(tmp_path))
        assert "pro_ok" in result

    def test_community_plugin_loads_in_community(self, tmp_path):
        """Community-tier plugins load regardless of tier."""
        from plugins import _loaded_plugins, load_plugins

        _loaded_plugins.clear()

        d = tmp_path / "community_plugin"
        d.mkdir()
        (d / "manifest.json").write_text(
            json.dumps({
                "name": "community_plugin",
                "version": "1.0.0",
                "type": "parser",
            })
        )
        (d / "plugin.py").write_text("def register(): pass\n")

        with patch("config.FEATURE_TIER", "community"):
            result = load_plugins(str(tmp_path))
        assert "community_plugin" in result


class TestEnabledPluginsFilter:
    """Test ENABLED_PLUGINS allowlist."""

    def test_enabled_plugins_filter(self, tmp_path):
        """Only plugins in ENABLED_PLUGINS are loaded when set."""
        from plugins import _loaded_plugins, load_plugins

        _loaded_plugins.clear()

        for name in ["allowed", "blocked"]:
            d = tmp_path / name
            d.mkdir()
            (d / "manifest.json").write_text(
                json.dumps({"name": name, "version": "1.0.0", "type": "parser"})
            )
            (d / "plugin.py").write_text("def register(): pass\n")

        with patch("config.ENABLED_PLUGINS", ["allowed"]):
            result = load_plugins(str(tmp_path))

        assert "allowed" in result
        assert "blocked" not in result


class TestRegisterFailure:
    """Test graceful handling of register() failures."""

    def test_register_raises_error(self, tmp_path):
        """Plugin whose register() raises is logged but doesn't crash."""
        from plugins import _loaded_plugins, load_plugins

        _loaded_plugins.clear()

        d = tmp_path / "crashy"
        d.mkdir()
        (d / "manifest.json").write_text(
            json.dumps({"name": "crashy", "version": "1.0.0", "type": "parser"})
        )
        (d / "plugin.py").write_text(
            "def register():\n    raise RuntimeError('boom')\n"
        )

        result = load_plugins(str(tmp_path))
        assert result == []
