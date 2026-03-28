# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the plugin management router."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_plugin_dir(tmp_path: Path) -> Path:
    """Create a temp plugin directory with sample manifests."""
    # OCR plugin (pro tier)
    ocr_dir = tmp_path / "ocr"
    ocr_dir.mkdir()
    (ocr_dir / "manifest.json").write_text(json.dumps({
        "name": "cerid-plugin-ocr",
        "version": "0.1.0",
        "description": "OCR parsing plugin",
        "type": "parser",
        "tier_required": "pro",
        "capabilities": ["parser"],
        "file_types": [".pdf", ".tiff"],
    }))

    # Analytics plugin (community tier)
    analytics_dir = tmp_path / "analytics"
    analytics_dir.mkdir()
    (analytics_dir / "manifest.json").write_text(json.dumps({
        "name": "cerid-plugin-analytics",
        "version": "0.2.0",
        "description": "Advanced analytics plugin",
        "type": "middleware",
        "tier_required": "community",
        "capabilities": ["analytics"],
    }))

    return tmp_path


def _mock_redis() -> MagicMock:
    """Return a mock Redis client with dict-backed storage."""
    store: dict[str, str] = {}
    r = MagicMock()
    r.get = MagicMock(side_effect=lambda key: store.get(key))
    r.set = MagicMock(side_effect=lambda key, val: store.__setitem__(key, val))
    return r


def _make_app(plugin_dir: str, tier: str = "community") -> FastAPI:
    """Build a test FastAPI app with the plugins router."""
    mock_redis = _mock_redis()

    with patch("config.PLUGIN_DIR", plugin_dir), \
         patch("config.FEATURE_TIER", tier), \
         patch("routers.plugins.get_redis", return_value=mock_redis):
        from routers.plugins import router

        app = FastAPI()
        app.include_router(router)
        # Store mock_redis on app for assertions
        app.state.mock_redis = mock_redis  # type: ignore[attr-defined]
    return app


class TestListPlugins:
    def test_returns_discovered_plugins(self, tmp_path: Path):
        plugin_dir = _make_plugin_dir(tmp_path)
        mock_redis = _mock_redis()

        with patch("config.PLUGIN_DIR", str(plugin_dir)), \
             patch("config.FEATURE_TIER", "community"), \
             patch("routers.plugins.get_redis", return_value=mock_redis):
            from routers.plugins import router

            app = FastAPI()
            app.include_router(router)
            client = TestClient(app)

            response = client.get("/plugins")
            assert response.status_code == 200
            data = response.json()
            assert data["total"] == 2
            names = [p["name"] for p in data["plugins"]]
            assert "cerid-plugin-ocr" in names
            assert "cerid-plugin-analytics" in names

    def test_empty_plugin_dir(self, tmp_path: Path):
        mock_redis = _mock_redis()
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        with patch("config.PLUGIN_DIR", str(empty_dir)), \
             patch("config.FEATURE_TIER", "community"), \
             patch("routers.plugins.get_redis", return_value=mock_redis):
            from routers.plugins import router

            app = FastAPI()
            app.include_router(router)
            client = TestClient(app)

            response = client.get("/plugins")
            assert response.status_code == 200
            assert response.json()["total"] == 0

    def test_nonexistent_plugin_dir(self):
        mock_redis = _mock_redis()

        with patch("config.PLUGIN_DIR", "/nonexistent/path"), \
             patch("config.FEATURE_TIER", "community"), \
             patch("routers.plugins.get_redis", return_value=mock_redis):
            from routers.plugins import router

            app = FastAPI()
            app.include_router(router)
            client = TestClient(app)

            response = client.get("/plugins")
            assert response.status_code == 200
            assert response.json()["total"] == 0


class TestGetPlugin:
    def test_get_existing_plugin(self, tmp_path: Path):
        plugin_dir = _make_plugin_dir(tmp_path)
        mock_redis = _mock_redis()

        with patch("config.PLUGIN_DIR", str(plugin_dir)), \
             patch("config.FEATURE_TIER", "community"), \
             patch("routers.plugins.get_redis", return_value=mock_redis):
            from routers.plugins import router

            app = FastAPI()
            app.include_router(router)
            client = TestClient(app)

            response = client.get("/plugins/cerid-plugin-ocr")
            assert response.status_code == 200
            data = response.json()
            assert data["name"] == "cerid-plugin-ocr"
            assert data["version"] == "0.1.0"
            assert data["tier_required"] == "pro"

    def test_get_nonexistent_plugin(self, tmp_path: Path):
        plugin_dir = _make_plugin_dir(tmp_path)
        mock_redis = _mock_redis()

        with patch("config.PLUGIN_DIR", str(plugin_dir)), \
             patch("config.FEATURE_TIER", "community"), \
             patch("routers.plugins.get_redis", return_value=mock_redis):
            from routers.plugins import router

            app = FastAPI()
            app.include_router(router)
            client = TestClient(app)

            response = client.get("/plugins/nonexistent")
            assert response.status_code == 404


class TestEnableDisable:
    def test_enable_community_plugin(self, tmp_path: Path):
        plugin_dir = _make_plugin_dir(tmp_path)
        mock_redis = _mock_redis()

        with patch("config.PLUGIN_DIR", str(plugin_dir)), \
             patch("config.FEATURE_TIER", "community"), \
             patch("routers.plugins.get_redis", return_value=mock_redis), \
             patch("plugins.get_loaded_plugins", return_value={}):
            from routers.plugins import router

            app = FastAPI()
            app.include_router(router)
            client = TestClient(app)

            response = client.post("/plugins/cerid-plugin-analytics/enable")
            assert response.status_code == 200
            data = response.json()
            assert data["enabled"] is True

    def test_enable_pro_plugin_on_community_tier_returns_403(self, tmp_path: Path):
        plugin_dir = _make_plugin_dir(tmp_path)
        mock_redis = _mock_redis()

        with patch("config.PLUGIN_DIR", str(plugin_dir)), \
             patch("config.FEATURE_TIER", "community"), \
             patch("config.features.FEATURE_TIER", "community"), \
             patch("routers.plugins.get_redis", return_value=mock_redis):
            from errors import CeridError, error_response
            from routers.plugins import router

            app = FastAPI()
            app.include_router(router)

            @app.exception_handler(CeridError)
            async def _handler(request, exc):
                from fastapi.responses import JSONResponse
                status = 403 if exc.error_code.startswith("FEATURE_GATE_") else 500
                return JSONResponse(status_code=status, content=error_response(exc))

            client = TestClient(app, raise_server_exceptions=False)

            response = client.post("/plugins/cerid-plugin-ocr/enable")
            assert response.status_code == 403
            assert "pro" in response.json()["message"].lower()

    def test_enable_pro_plugin_on_pro_tier(self, tmp_path: Path):
        plugin_dir = _make_plugin_dir(tmp_path)
        mock_redis = _mock_redis()

        with patch("config.PLUGIN_DIR", str(plugin_dir)), \
             patch("config.FEATURE_TIER", "pro"), \
             patch("config.features.FEATURE_TIER", "pro"), \
             patch("routers.plugins.get_redis", return_value=mock_redis), \
             patch("plugins.get_loaded_plugins", return_value={}):
            from routers.plugins import router

            app = FastAPI()
            app.include_router(router)
            client = TestClient(app)

            response = client.post("/plugins/cerid-plugin-ocr/enable")
            assert response.status_code == 200
            data = response.json()
            assert data["enabled"] is True

    def test_disable_plugin(self, tmp_path: Path):
        plugin_dir = _make_plugin_dir(tmp_path)
        mock_redis = _mock_redis()

        with patch("config.PLUGIN_DIR", str(plugin_dir)), \
             patch("config.FEATURE_TIER", "community"), \
             patch("routers.plugins.get_redis", return_value=mock_redis), \
             patch("plugins.get_loaded_plugins", return_value={}):
            from routers.plugins import router

            app = FastAPI()
            app.include_router(router)
            client = TestClient(app)

            # Enable first, then disable
            client.post("/plugins/cerid-plugin-analytics/enable")
            response = client.post("/plugins/cerid-plugin-analytics/disable")
            assert response.status_code == 200
            assert response.json()["enabled"] is False

    def test_enable_nonexistent_returns_404(self, tmp_path: Path):
        plugin_dir = _make_plugin_dir(tmp_path)
        mock_redis = _mock_redis()

        with patch("config.PLUGIN_DIR", str(plugin_dir)), \
             patch("config.FEATURE_TIER", "community"), \
             patch("routers.plugins.get_redis", return_value=mock_redis):
            from routers.plugins import router

            app = FastAPI()
            app.include_router(router)
            client = TestClient(app)

            response = client.post("/plugins/nonexistent/enable")
            assert response.status_code == 404


class TestPluginConfig:
    def test_get_empty_config(self, tmp_path: Path):
        plugin_dir = _make_plugin_dir(tmp_path)
        mock_redis = _mock_redis()

        with patch("config.PLUGIN_DIR", str(plugin_dir)), \
             patch("config.FEATURE_TIER", "community"), \
             patch("routers.plugins.get_redis", return_value=mock_redis):
            from routers.plugins import router

            app = FastAPI()
            app.include_router(router)
            client = TestClient(app)

            response = client.get("/plugins/cerid-plugin-analytics/config")
            assert response.status_code == 200
            assert response.json()["values"] == {}

    def test_set_and_get_config(self, tmp_path: Path):
        plugin_dir = _make_plugin_dir(tmp_path)
        mock_redis = _mock_redis()

        with patch("config.PLUGIN_DIR", str(plugin_dir)), \
             patch("config.FEATURE_TIER", "community"), \
             patch("routers.plugins.get_redis", return_value=mock_redis):
            from routers.plugins import router

            app = FastAPI()
            app.include_router(router)
            client = TestClient(app)

            # Set config
            put_resp = client.put(
                "/plugins/cerid-plugin-analytics/config",
                json={"values": {"threshold": 0.8, "mode": "detailed"}},
            )
            assert put_resp.status_code == 200

            # Read it back
            get_resp = client.get("/plugins/cerid-plugin-analytics/config")
            assert get_resp.status_code == 200
            values = get_resp.json()["values"]
            assert values["threshold"] == 0.8
            assert values["mode"] == "detailed"

    def test_config_nonexistent_plugin_returns_404(self, tmp_path: Path):
        plugin_dir = _make_plugin_dir(tmp_path)
        mock_redis = _mock_redis()

        with patch("config.PLUGIN_DIR", str(plugin_dir)), \
             patch("config.FEATURE_TIER", "community"), \
             patch("routers.plugins.get_redis", return_value=mock_redis):
            from routers.plugins import router

            app = FastAPI()
            app.include_router(router)
            client = TestClient(app)

            response = client.get("/plugins/nonexistent/config")
            assert response.status_code == 404


class TestScanPlugins:
    def test_scan_discovers_plugins(self, tmp_path: Path):
        plugin_dir = _make_plugin_dir(tmp_path)
        mock_redis = _mock_redis()

        with patch("config.PLUGIN_DIR", str(plugin_dir)), \
             patch("config.FEATURE_TIER", "community"), \
             patch("routers.plugins.get_redis", return_value=mock_redis):
            from routers.plugins import router

            app = FastAPI()
            app.include_router(router)
            client = TestClient(app)

            response = client.post("/plugins/scan")
            assert response.status_code == 200
            data = response.json()
            assert data["total"] == 2

    def test_scan_after_adding_new_plugin(self, tmp_path: Path):
        plugin_dir = _make_plugin_dir(tmp_path)
        mock_redis = _mock_redis()

        with patch("config.PLUGIN_DIR", str(plugin_dir)), \
             patch("config.FEATURE_TIER", "community"), \
             patch("routers.plugins.get_redis", return_value=mock_redis):
            from routers.plugins import router

            app = FastAPI()
            app.include_router(router)
            client = TestClient(app)

            # Initial scan
            resp1 = client.post("/plugins/scan")
            assert resp1.json()["total"] == 2

            # Add a new plugin
            new_dir = plugin_dir / "new_plugin"
            new_dir.mkdir()
            (new_dir / "manifest.json").write_text(json.dumps({
                "name": "cerid-plugin-new",
                "version": "1.0.0",
                "description": "A new plugin",
                "type": "parser",
                "tier_required": "community",
            }))

            # Rescan
            resp2 = client.post("/plugins/scan")
            assert resp2.json()["total"] == 3


class TestPluginStatus:
    def test_pro_plugin_shows_requires_pro_on_community_tier(self, tmp_path: Path):
        plugin_dir = _make_plugin_dir(tmp_path)
        mock_redis = _mock_redis()

        with patch("config.PLUGIN_DIR", str(plugin_dir)), \
             patch("config.FEATURE_TIER", "community"), \
             patch("routers.plugins.get_redis", return_value=mock_redis):
            from routers.plugins import router

            app = FastAPI()
            app.include_router(router)
            client = TestClient(app)

            response = client.get("/plugins/cerid-plugin-ocr")
            assert response.status_code == 200
            assert response.json()["status"] == "requires_pro"

    def test_disabled_plugin_shows_disabled(self, tmp_path: Path):
        plugin_dir = _make_plugin_dir(tmp_path)
        mock_redis = _mock_redis()

        with patch("config.PLUGIN_DIR", str(plugin_dir)), \
             patch("config.FEATURE_TIER", "community"), \
             patch("routers.plugins.get_redis", return_value=mock_redis):
            from routers.plugins import router

            app = FastAPI()
            app.include_router(router)
            client = TestClient(app)

            response = client.get("/plugins/cerid-plugin-analytics")
            assert response.status_code == 200
            assert response.json()["status"] == "disabled"

    def test_enabled_plugin_shows_installed(self, tmp_path: Path):
        plugin_dir = _make_plugin_dir(tmp_path)
        mock_redis = _mock_redis()

        with patch("config.PLUGIN_DIR", str(plugin_dir)), \
             patch("config.FEATURE_TIER", "community"), \
             patch("routers.plugins.get_redis", return_value=mock_redis), \
             patch("plugins.get_loaded_plugins", return_value={}):
            from routers.plugins import router

            app = FastAPI()
            app.include_router(router)
            client = TestClient(app)

            client.post("/plugins/cerid-plugin-analytics/enable")
            response = client.get("/plugins/cerid-plugin-analytics")
            assert response.status_code == 200
            assert response.json()["status"] == "installed"

    def test_enabled_and_loaded_plugin_shows_active(self, tmp_path: Path):
        plugin_dir = _make_plugin_dir(tmp_path)
        mock_redis = _mock_redis()

        with patch("config.PLUGIN_DIR", str(plugin_dir)), \
             patch("config.FEATURE_TIER", "community"), \
             patch("routers.plugins.get_redis", return_value=mock_redis), \
             patch("plugins.get_loaded_plugins", return_value={"cerid-plugin-analytics": {}}):
            from routers.plugins import router

            app = FastAPI()
            app.include_router(router)
            client = TestClient(app)

            client.post("/plugins/cerid-plugin-analytics/enable")
            response = client.get("/plugins/cerid-plugin-analytics")
            assert response.status_code == 200
            assert response.json()["status"] == "active"
