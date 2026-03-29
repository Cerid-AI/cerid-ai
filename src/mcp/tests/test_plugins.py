# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Cerid AI plugin system (Phase 8A)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure src/mcp is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestPluginLoader:
    """Test plugin discovery and loading."""

    def test_load_plugins_empty_directory(self, tmp_path):
        """Empty plugin directory returns no plugins."""
        from plugins import _loaded_plugins, load_plugins
        _loaded_plugins.clear()

        result = load_plugins(str(tmp_path))
        assert result == []

    def test_load_plugins_missing_directory(self):
        """Non-existent directory returns no plugins."""
        from plugins import _loaded_plugins, load_plugins
        _loaded_plugins.clear()

        result = load_plugins("/nonexistent/path/plugins")
        assert result == []

    def test_load_valid_plugin(self, tmp_path):
        """Valid plugin with manifest.json and plugin.py loads successfully."""
        from plugins import _loaded_plugins, load_plugins
        _loaded_plugins.clear()

        # Create a test plugin
        plugin_dir = tmp_path / "test_plugin"
        plugin_dir.mkdir()

        manifest = {
            "name": "test_plugin",
            "version": "1.0.0",
            "type": "parser",
            "description": "A test plugin",
        }
        (plugin_dir / "manifest.json").write_text(json.dumps(manifest))
        (plugin_dir / "plugin.py").write_text(
            "REGISTERED = False\n"
            "def register():\n"
            "    global REGISTERED\n"
            "    REGISTERED = True\n"
        )

        result = load_plugins(str(tmp_path))
        assert "test_plugin" in result

    def test_load_plugin_missing_plugin_py(self, tmp_path):
        """Plugin with manifest but no plugin.py fails gracefully."""
        from plugins import _loaded_plugins, load_plugins
        _loaded_plugins.clear()

        plugin_dir = tmp_path / "bad_plugin"
        plugin_dir.mkdir()

        manifest = {"name": "bad_plugin", "version": "1.0.0", "type": "parser"}
        (plugin_dir / "manifest.json").write_text(json.dumps(manifest))

        result = load_plugins(str(tmp_path))
        assert result == []

    def test_load_plugin_invalid_manifest(self, tmp_path):
        """Plugin with invalid manifest fails gracefully."""
        from plugins import _loaded_plugins, load_plugins
        _loaded_plugins.clear()

        plugin_dir = tmp_path / "invalid_plugin"
        plugin_dir.mkdir()

        # Missing required 'type' field
        manifest = {"name": "invalid_plugin", "version": "1.0.0"}
        (plugin_dir / "manifest.json").write_text(json.dumps(manifest))
        (plugin_dir / "plugin.py").write_text("def register(): pass\n")

        result = load_plugins(str(tmp_path))
        assert result == []

    def test_load_plugin_pro_tier_blocked(self, tmp_path):
        """Pro-tier plugin skipped when running community tier."""
        from plugins import _loaded_plugins, load_plugins
        _loaded_plugins.clear()

        plugin_dir = tmp_path / "pro_plugin"
        plugin_dir.mkdir()

        manifest = {
            "name": "pro_plugin",
            "version": "1.0.0",
            "type": "parser",
            "tier": "pro",
        }
        (plugin_dir / "manifest.json").write_text(json.dumps(manifest))
        (plugin_dir / "plugin.py").write_text("def register(): pass\n")

        with patch("config.FEATURE_TIER", "community"):
            result = load_plugins(str(tmp_path))
            assert result == []

    @patch("config.features.FEATURE_TIER", "pro")
    @patch("config.FEATURE_TIER", "pro")
    def test_load_plugin_pro_tier_allowed(self, tmp_path):
        """Pro-tier plugin loads when running pro tier."""
        from plugins import _loaded_plugins, load_plugins
        _loaded_plugins.clear()

        plugin_dir = tmp_path / "pro_plugin"
        plugin_dir.mkdir()

        manifest = {
            "name": "pro_plugin",
            "version": "1.0.0",
            "type": "parser",
            "tier": "pro",
        }
        (plugin_dir / "manifest.json").write_text(json.dumps(manifest))
        (plugin_dir / "plugin.py").write_text("def register(): pass\n")

        result = load_plugins(str(tmp_path))
        assert "pro_plugin" in result

    def test_load_plugin_missing_dependency(self, tmp_path):
        """Plugin with missing Python dependency skipped gracefully."""
        from plugins import _loaded_plugins, load_plugins
        _loaded_plugins.clear()

        plugin_dir = tmp_path / "dep_plugin"
        plugin_dir.mkdir()

        manifest = {
            "name": "dep_plugin",
            "version": "1.0.0",
            "type": "parser",
            "requires": ["nonexistent_package_xyz_123"],
        }
        (plugin_dir / "manifest.json").write_text(json.dumps(manifest))
        (plugin_dir / "plugin.py").write_text("def register(): pass\n")

        result = load_plugins(str(tmp_path))
        assert result == []

    def test_get_loaded_plugins(self, tmp_path):
        """get_loaded_plugins returns info without module references."""
        from plugins import _loaded_plugins, get_loaded_plugins, load_plugins
        _loaded_plugins.clear()

        plugin_dir = tmp_path / "info_plugin"
        plugin_dir.mkdir()

        manifest = {
            "name": "info_plugin",
            "version": "2.0.0",
            "type": "agent",
            "description": "Test info",
        }
        (plugin_dir / "manifest.json").write_text(json.dumps(manifest))
        (plugin_dir / "plugin.py").write_text("def register(): pass\n")

        load_plugins(str(tmp_path))
        info = get_loaded_plugins()

        assert "info_plugin" in info
        assert info["info_plugin"]["version"] == "2.0.0"
        assert info["info_plugin"]["type"] == "agent"
        assert "module" not in info["info_plugin"]


class TestFeatureFlags:
    """Test feature flag system."""

    def test_is_feature_enabled_community(self):
        """Community features are always enabled."""
        from utils.features import is_feature_enabled

        assert is_feature_enabled("hierarchical_taxonomy") is True
        assert is_feature_enabled("file_upload_gui") is True
        assert is_feature_enabled("encryption_at_rest") is True

    def test_is_feature_enabled_unknown_defaults_disabled(self):
        """Unknown feature names default to disabled (fail-closed for safety)."""
        from utils.features import is_feature_enabled

        assert is_feature_enabled("nonexistent_feature") is False

    @patch("config.FEATURE_TIER", "community")
    @patch("config.FEATURE_FLAGS", {"ocr_parsing": False, "semantic_dedup": False})
    def test_pro_features_disabled_in_community(self):
        """Pro features are disabled in community tier."""
        from utils.features import is_feature_enabled

        assert is_feature_enabled("ocr_parsing") is False
        assert is_feature_enabled("semantic_dedup") is False

    def test_get_feature_status(self):
        """get_feature_status returns tier and all flags."""
        from utils.features import get_feature_status

        status = get_feature_status()
        assert "tier" in status
        assert "features" in status
        assert isinstance(status["features"], dict)

        for name, info in status["features"].items():
            assert "enabled" in info
            assert "tier_required" in info

    @pytest.mark.asyncio
    async def test_require_feature_blocks_disabled(self):
        """require_feature raises 403 for disabled features."""
        # Stub fastapi.HTTPException if fastapi not installed
        try:
            from fastapi import HTTPException
        except ImportError:
            from types import ModuleType
            if "fastapi" not in sys.modules:
                _stub = ModuleType("fastapi")

                class _HTTPException(Exception):
                    def __init__(self, status_code=500, detail=""):
                        self.status_code = status_code
                        self.detail = detail

                _stub.HTTPException = _HTTPException
                sys.modules["fastapi"] = _stub
            HTTPException = sys.modules["fastapi"].HTTPException

        from utils.features import require_feature

        @require_feature("test_blocked_feature")
        async def dummy_endpoint():
            return {"ok": True}

        with patch("config.FEATURE_FLAGS", {"test_blocked_feature": False}):
            with pytest.raises(HTTPException) as exc_info:
                await dummy_endpoint()
            assert exc_info.value.status_code == 403
            assert "tier" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_require_feature_allows_enabled(self):
        """require_feature allows through for enabled features."""
        from utils.features import require_feature

        @require_feature("test_allowed_feature")
        async def dummy_endpoint():
            return {"ok": True}

        with patch("config.FEATURE_FLAGS", {"test_allowed_feature": True}), \
             patch("config.features.FEATURE_FLAGS", {"test_allowed_feature": True}):
            result = await dummy_endpoint()
            assert result == {"ok": True}
