# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""GET /plugins has exactly one owner (F003 / F211).

``health.router`` and ``plugins.router`` both used to declare ``GET
/plugins``. Starlette matches in registration order, so health's
name-keyed dict answered every call, while the OpenAPI schema — built
into a dict keyed by path — published ``plugins.router``'s array-shaped
``PluginListResponse``. Every generated client iterated an array and got
an object, and the Settings → Extensions toggle read ``enabled`` off a
payload that has no such key.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _app_in_main_registration_order() -> FastAPI:
    """Mount health then plugins exactly as ``main.py._api_routers`` does."""
    from app.routers import health, plugins

    app = FastAPI()
    app.include_router(health.router)
    app.include_router(plugins.router)
    return app


def _seed_plugin_dir(tmp_path: Path) -> Path:
    d = tmp_path / "demo-plugin"
    d.mkdir()
    (d / "manifest.json").write_text(
        json.dumps(
            {
                "name": "demo-plugin",
                "version": "1.0.0",
                "type": "connector",
                "tier_required": "community",
            }
        ),
        encoding="utf-8",
    )
    return tmp_path


def test_get_plugins_serves_the_published_array_schema(tmp_path, monkeypatch):
    """The served payload must match the schema OpenAPI publishes for it."""
    import config

    monkeypatch.setattr(config, "PLUGIN_DIR", str(_seed_plugin_dir(tmp_path)))
    app = _app_in_main_registration_order()

    published = app.openapi()["paths"]["/plugins"]["get"]
    body = TestClient(app).get("/plugins").json()

    assert "PluginListResponse" in json.dumps(published), (
        "OpenAPI no longer publishes the plugins-router schema for GET /plugins"
    )
    assert isinstance(body["plugins"], list), (
        f"GET /plugins served {type(body['plugins']).__name__}, "
        "but the published schema declares an array"
    )
    assert body["total"] == 1
    assert body["plugins"][0]["name"] == "demo-plugin"
    assert "enabled" in body["plugins"][0]


def test_only_one_router_declares_get_plugins():
    """A second declaration would silently shadow whichever loads first."""
    from app.routers import health, plugins

    declarations = [
        f"{module.__name__}:{route.name}"
        for module in (health, plugins)
        for route in module.router.routes
        if getattr(route, "path", None) == "/plugins"
        and "GET" in getattr(route, "methods", set())
    ]
    assert declarations == ["app.routers.plugins:list_plugins"], declarations
