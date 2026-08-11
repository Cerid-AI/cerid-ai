# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""PluginInfo must carry the manifest's ``feature_flags``.

Every shipped manifest declares which ``config.features.FEATURE_FLAGS`` keys the
plugin needs, and the loader already keeps them on the in-memory record, but the
router dropped them — so a client could see ``tier_required: "pro"`` without ever
learning which capability was missing.

These assert against ``GET /plugins/{name}`` and ``POST /plugins/{name}/enable``,
never ``GET /plugins``: ``app.routers.health`` also declares ``GET /plugins`` and
is registered first in ``app.main``, so in the real app its handler wins and a
gate written there would never see this router's response shape.
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
    pro = tmp_path / "gmail"
    pro.mkdir()
    (pro / "manifest.json").write_text(json.dumps({
        "name": "gmail",
        "version": "0.1.0",
        "description": "Gmail connector",
        "tier_required": "pro",
        "feature_flags": ["gmail_connector"],
    }))

    core = tmp_path / "analytics"
    core.mkdir()
    (core / "manifest.json").write_text(json.dumps({
        "name": "analytics",
        "version": "0.2.0",
        "description": "Analytics plugin with no flags declared",
        "tier_required": "community",
    }))
    return tmp_path


def _client(plugin_dir: str, tier: str = "community") -> TestClient:
    from app.routers.plugins import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _get(plugin_dir: Path, name: str, tier: str = "community") -> dict:
    with patch("config.PLUGIN_DIR", str(plugin_dir)), \
         patch("config.features.FEATURE_TIER", tier), \
         patch("app.routers.plugins.get_redis", return_value=_mock_redis()):
        resp = _client(str(plugin_dir), tier).get(f"/plugins/{name}")
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_detail_exposes_manifest_feature_flags(tmp_path: Path):
    body = _get(_write_manifests(tmp_path), "gmail", tier="pro")
    assert body["feature_flags"] == ["gmail_connector"]


def test_detail_reports_empty_list_when_manifest_declares_none(tmp_path: Path):
    body = _get(_write_manifests(tmp_path), "analytics")
    assert body["feature_flags"] == []


def test_enable_response_carries_feature_flags(tmp_path: Path):
    plugin_dir = _write_manifests(tmp_path)
    with patch("config.PLUGIN_DIR", str(plugin_dir)), \
         patch("config.features.FEATURE_TIER", "pro"), \
         patch("app.routers.plugins.get_redis", return_value=_mock_redis()):
        resp = _client(str(plugin_dir), "pro").post("/plugins/gmail/enable")
    assert resp.status_code == 200, resp.text
    assert resp.json()["feature_flags"] == ["gmail_connector"]


def test_existing_fields_survive_the_addition(tmp_path: Path):
    # Backward compatibility: the new field is additive, nothing else moved.
    body = _get(_write_manifests(tmp_path), "gmail", tier="pro")
    assert body["name"] == "gmail"
    assert body["version"] == "0.1.0"
    assert body["tier_required"] == "pro"
    assert body["enabled"] is False


def test_shipped_manifests_are_surfaced_verbatim():
    """The real plugin tree, not the repo-root ``./plugins`` decoys.

    ``config.PLUGIN_DIR`` defaults to ``src/mcp/plugins``; the repo-root
    ``plugins/`` holds a different, flagless set of directories. Reading the
    wrong one makes this invariant look untestable.
    """
    from app.routers.plugins import _discover_manifests, _manifest_to_info

    real_dir = Path(__file__).resolve().parents[1] / "plugins"
    with patch("config.PLUGIN_DIR", str(real_dir)), \
         patch("app.routers.plugins.get_redis", return_value=_mock_redis()):
        manifests = _discover_manifests()
        assert manifests, f"no manifests discovered under {real_dir}"
        declared = {
            name: list(m.get("feature_flags") or []) for name, m in manifests.items()
        }
        assert any(declared.values()), "fixture guard: shipped manifests declare flags"
        surfaced = {
            name: _manifest_to_info(m, enabled=False).feature_flags
            for name, m in manifests.items()
        }
    assert surfaced == declared
