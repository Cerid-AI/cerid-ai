# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""PluginInfo must carry a human display name and the manifest ``type``.

GUI spec MUST-6 (tasks/2026-08-11-gui-review-spec.md): Settings → Extensions
rendered raw manifest names (``apple_mail``) because the router served no
human label, and the client could not distinguish connector-backing packs
from capability packs because the manifest ``type`` field was dropped.

These assert against ``GET /plugins/{name}``, never ``GET /plugins``:
``app.routers.health`` also declares ``GET /plugins`` and is registered first
in ``app.main``, so in the real app its handler wins and a gate written there
would never see this router's response shape.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _mock_redis() -> MagicMock:
    store: dict[str, str] = {}
    r = MagicMock()
    r.get = MagicMock(side_effect=lambda key: store.get(key))
    r.set = MagicMock(side_effect=lambda key, val: store.__setitem__(key, val))
    return r


def _write_manifests(tmp_path: Path) -> Path:
    explicit = tmp_path / "outlook"
    explicit.mkdir()
    (explicit / "manifest.json").write_text(json.dumps({
        "name": "outlook",
        "display_name": "Outlook Mail",
        "version": "0.1.0",
        "type": "connector",
        "tier_required": "pro",
    }))

    fallback = tmp_path / "apple_mail"
    fallback.mkdir()
    (fallback / "manifest.json").write_text(json.dumps({
        "name": "apple_mail",
        "version": "0.1.0",
        "type": "connector",
        "tier_required": "pro",
    }))

    untyped = tmp_path / "analytics"
    untyped.mkdir()
    (untyped / "manifest.json").write_text(json.dumps({
        "name": "analytics",
        "version": "0.2.0",
        "tier_required": "community",
    }))
    return tmp_path


def _get(plugin_dir: Path, name: str, tier: str = "community") -> dict:
    with patch("config.PLUGIN_DIR", str(plugin_dir)), \
         patch("config.features.FEATURE_TIER", tier), \
         patch("app.routers.plugins.get_redis", return_value=_mock_redis()):
        from app.routers.plugins import router

        app = FastAPI()
        app.include_router(router)
        resp = TestClient(app).get(f"/plugins/{name}")
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_explicit_manifest_display_name_wins(tmp_path: Path):
    body = _get(_write_manifests(tmp_path), "outlook", tier="pro")
    assert body["display_name"] == "Outlook Mail"


def test_display_name_falls_back_to_title_cased_name(tmp_path: Path):
    body = _get(_write_manifests(tmp_path), "apple_mail", tier="pro")
    assert body["display_name"] == "Apple Mail"


def test_plugin_type_served_from_manifest_type(tmp_path: Path):
    body = _get(_write_manifests(tmp_path), "outlook", tier="pro")
    assert body["plugin_type"] == "connector"


def test_plugin_type_defaults_to_tool_when_manifest_omits_it(tmp_path: Path):
    body = _get(_write_manifests(tmp_path), "analytics")
    assert body["plugin_type"] == "tool"


def test_loader_record_carries_display_name(tmp_path: Path):
    """``app.routers.health`` wins GET /plugins registration order in the real
    app and serves the loader's records verbatim — so the record itself must
    carry ``display_name`` or the Settings page never sees it."""
    from plugins import _loaded_plugins, load_plugins

    _loaded_plugins.clear()
    d = tmp_path / "apple_mail"
    d.mkdir()
    (d / "manifest.json").write_text(json.dumps({
        "name": "apple_mail",
        "version": "0.1.0",
        "type": "connector",
    }))
    (d / "plugin.py").write_text("def register(): pass\n")

    loaded = load_plugins(str(tmp_path))
    assert loaded == ["apple_mail"]
    from plugins import get_loaded_plugins

    record = get_loaded_plugins()["apple_mail"]
    assert record["display_name"] == "Apple Mail"
    _loaded_plugins.clear()


def test_shipped_manifests_give_every_connector_pack_a_human_label():
    """The real manifests (not fixtures) must not fall back to snake_case for
    the connector-backing and Apple packs the Settings page lists."""
    plugin_root = Path(__file__).resolve().parents[1] / "plugins"
    expected = {
        "apple_calendar": "Apple Calendar",
        "apple_mail": "Apple Mail",
        "apple_photos": "Apple Photos",
        "gmail": "Gmail",
        "google_calendar": "Google Calendar",
        "outlook": "Outlook Mail",
        "outlook_calendar": "Outlook Calendar",
        "ocr": "OCR",
    }
    for dirname, label in expected.items():
        manifest = json.loads((plugin_root / dirname / "manifest.json").read_text())
        assert manifest.get("display_name") == label, dirname
