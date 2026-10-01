# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""What the plugin endpoints store is what the loader applies.

``POST /plugins/{name}/enable|disable`` wrote ``cerid:plugins:{name}:enabled``
and only the router's own listing read it back, so a plugin switched off in
Settings was shown as disabled and kept running.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.mget_calls = 0

    def get(self, key: str) -> str | None:
        return self.store.get(key)

    def set(self, key: str, value: str) -> None:
        self.store[key] = value

    def mget(self, keys: list[str]) -> list[str | None]:
        self.mget_calls += 1
        return [self.store.get(k) for k in keys]


def _write_plugin(base: Path, name: str, **manifest: object) -> None:
    d = base / name
    d.mkdir()
    (d / "manifest.json").write_text(
        json.dumps({"name": name, "version": "1.0.0", "type": "parser", **manifest})
    )
    (d / "plugin.py").write_text("def register(): pass\n")


@pytest.fixture
def redis_store(monkeypatch):
    """One store behind both the endpoints and the loader."""
    import plugins

    fake = _FakeRedis()
    monkeypatch.setattr(plugins, "_get_redis", lambda: fake, raising=False)
    with patch("app.routers.plugins.get_redis", return_value=fake):
        yield fake


@pytest.fixture
def loader_state():
    import plugins

    saved = (
        dict(plugins._loaded_plugins),
        dict(plugins._failed_plugins),
        list(plugins._plugin_tool_definitions),
        dict(plugins._plugin_tool_handlers),
    )
    plugins._loaded_plugins.clear()
    plugins._failed_plugins.clear()
    yield plugins
    for target, original in zip(
        (
            plugins._loaded_plugins,
            plugins._failed_plugins,
            plugins._plugin_tool_definitions,
            plugins._plugin_tool_handlers,
        ),
        saved,
    ):
        target.clear()
        if isinstance(target, dict):
            target.update(original)
        else:
            target.extend(original)


def _client(plugin_dir: Path) -> TestClient:
    from app.routers.plugins import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestLoaderAppliesTheStoredChoice:
    def test_disabled_through_the_endpoint_is_not_loaded(
        self, tmp_path, redis_store, loader_state
    ):
        _write_plugin(tmp_path, "alpha")
        _write_plugin(tmp_path, "beta")

        with patch("config.PLUGIN_DIR", str(tmp_path)):
            assert _client(tmp_path).post("/plugins/alpha/disable").status_code == 200

        loaded = loader_state.load_plugins(str(tmp_path))

        assert loaded == ["beta"]
        failure = loader_state.get_failed_plugins()["alpha"]
        assert failure["error_type"] == "PluginDisabledError"
        assert "settings" in failure["error"]

    def test_re_enabled_through_the_endpoint_loads_again(
        self, tmp_path, redis_store, loader_state
    ):
        _write_plugin(tmp_path, "alpha")

        with patch("config.PLUGIN_DIR", str(tmp_path)):
            client = _client(tmp_path)
            client.post("/plugins/alpha/disable")
            assert loader_state.load_plugins(str(tmp_path)) == []
            client.post("/plugins/alpha/enable")

        assert loader_state.load_plugins(str(tmp_path)) == ["alpha"]

    def test_no_stored_key_keeps_the_default(self, tmp_path, redis_store, loader_state):
        _write_plugin(tmp_path, "alpha")

        assert loader_state.load_plugins(str(tmp_path)) == ["alpha"]
        assert redis_store.mget_calls == 1

    def test_redis_failure_keeps_the_default_and_is_logged(
        self, tmp_path, monkeypatch, loader_state
    ):
        _write_plugin(tmp_path, "alpha")
        _write_plugin(tmp_path, "beta")
        attempts = []

        def _down():
            attempts.append(1)
            raise ConnectionError("redis is down")

        monkeypatch.setattr(loader_state, "_get_redis", _down, raising=False)

        with patch("core.utils.swallowed.log_swallowed_error") as logged:
            loaded = loader_state.load_plugins(str(tmp_path))

        assert loaded == ["alpha", "beta"]
        # One read for the whole pass: a dead Redis must not cost a connect
        # timeout per plugin at boot.
        assert len(attempts) == 1
        assert logged.call_count == 1
        assert logged.call_args.args[0] == "plugins"

    def test_stored_enable_does_not_pass_the_environment_allowlist(
        self, tmp_path, redis_store, loader_state
    ):
        _write_plugin(tmp_path, "alpha")
        _write_plugin(tmp_path, "beta")
        redis_store.set("cerid:plugins:alpha:enabled", "1")
        redis_store.set("cerid:plugins:beta:enabled", "1")

        with patch("config.ENABLED_PLUGINS", ["beta"]):
            loaded = loader_state.load_plugins(str(tmp_path))

        assert loaded == ["beta"]
        assert "CERID_ENABLED_PLUGINS" in loader_state.get_failed_plugins()["alpha"]["error"]

    def test_stored_disable_applies_inside_the_environment_allowlist(
        self, tmp_path, redis_store, loader_state
    ):
        _write_plugin(tmp_path, "alpha")
        _write_plugin(tmp_path, "beta")
        redis_store.set("cerid:plugins:beta:enabled", "0")

        with patch("config.ENABLED_PLUGINS", ["alpha", "beta"]):
            loaded = loader_state.load_plugins(str(tmp_path))

        assert loaded == ["alpha"]

    def test_stored_enable_does_not_pass_the_tier_check(
        self, tmp_path, redis_store, loader_state
    ):
        _write_plugin(tmp_path, "paid", tier="pro")
        redis_store.set("cerid:plugins:paid:enabled", "1")

        with patch("config.features.FEATURE_TIER", "community"):
            loaded = loader_state.load_plugins(str(tmp_path))

        assert loaded == []


class TestEndpointRefusesWhatTheLoaderWould:
    def test_enable_outside_the_environment_allowlist_is_refused(
        self, tmp_path, redis_store
    ):
        _write_plugin(tmp_path, "alpha")

        with patch("config.PLUGIN_DIR", str(tmp_path)), \
             patch("config.ENABLED_PLUGINS", ["beta"]):
            response = _client(tmp_path).post("/plugins/alpha/enable")

        assert response.status_code == 403
        assert "CERID_ENABLED_PLUGINS" in response.json()["detail"]
        assert redis_store.store == {}

    def test_enable_inside_the_environment_allowlist_is_stored(
        self, tmp_path, redis_store
    ):
        _write_plugin(tmp_path, "alpha")

        with patch("config.PLUGIN_DIR", str(tmp_path)), \
             patch("config.ENABLED_PLUGINS", ["alpha"]):
            response = _client(tmp_path).post("/plugins/alpha/enable")

        assert response.status_code == 200
        assert redis_store.store == {"cerid:plugins:alpha:enabled": "1"}

    def test_enable_above_the_tier_is_refused(self, tmp_path, redis_store):
        _write_plugin(tmp_path, "paid", tier="enterprise")

        with patch("config.PLUGIN_DIR", str(tmp_path)), \
             patch("config.features.FEATURE_TIER", "pro"):
            response = _client(tmp_path).post("/plugins/paid/enable")

        assert response.status_code == 403
        assert redis_store.store == {}


class TestListingReportsWhatTheLoaderWillDo:
    def test_no_stored_key_reports_the_loader_default(self, tmp_path, redis_store):
        _write_plugin(tmp_path, "alpha")
        _write_plugin(tmp_path, "paid", tier="pro")

        with patch("config.PLUGIN_DIR", str(tmp_path)), \
             patch("config.features.FEATURE_TIER", "community"):
            body = _client(tmp_path).get("/plugins").json()

        enabled = {p["name"]: p["enabled"] for p in body["plugins"]}
        assert enabled == {"alpha": True, "paid": False}

    def test_outside_the_environment_allowlist_reports_disabled(
        self, tmp_path, redis_store
    ):
        _write_plugin(tmp_path, "alpha")
        redis_store.set("cerid:plugins:alpha:enabled", "1")

        with patch("config.PLUGIN_DIR", str(tmp_path)), \
             patch("config.ENABLED_PLUGINS", ["beta"]):
            body = _client(tmp_path).get("/plugins/alpha").json()

        assert body["enabled"] is False
        assert body["restart_required"] is False

    def test_disabling_a_running_plugin_says_it_needs_a_restart(
        self, tmp_path, redis_store, loader_state
    ):
        _write_plugin(tmp_path, "alpha")
        loader_state.load_plugins(str(tmp_path))

        with patch("config.PLUGIN_DIR", str(tmp_path)):
            client = _client(tmp_path)
            assert client.get("/plugins/alpha").json()["restart_required"] is False
            body = client.post("/plugins/alpha/disable").json()
            listed = client.get("/plugins/alpha").json()

        assert body["enabled"] is False
        assert body["restart_required"] is True
        assert listed["restart_required"] is True

    def test_enabling_a_plugin_skipped_at_boot_says_it_needs_a_restart(
        self, tmp_path, redis_store, loader_state
    ):
        _write_plugin(tmp_path, "alpha")
        redis_store.set("cerid:plugins:alpha:enabled", "0")
        loader_state.load_plugins(str(tmp_path))

        with patch("config.PLUGIN_DIR", str(tmp_path)):
            client = _client(tmp_path)
            assert client.get("/plugins/alpha").json()["restart_required"] is False
            body = client.post("/plugins/alpha/enable").json()

        assert body["enabled"] is True
        assert body["restart_required"] is True

    def test_a_plugin_that_failed_to_load_is_not_waiting_on_a_restart(
        self, tmp_path, redis_store, loader_state
    ):
        _write_plugin(tmp_path, "alpha", requires=["a_module_that_does_not_exist"])
        loader_state.load_plugins(str(tmp_path))

        with patch("config.PLUGIN_DIR", str(tmp_path)):
            body = _client(tmp_path).get("/plugins/alpha").json()

        assert body["enabled"] is True
        assert body["restart_required"] is False


class TestPluginToolsFollowTheStoredChoiceLive:
    async def test_disabling_withholds_the_tool_without_a_restart(
        self, tmp_path, redis_store, loader_state
    ):
        from app.tools import _dispatch_plugin_tool, get_all_tools

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
        assert loader_state.load_plugins(str(tmp_path)) == ["toolplug"]
        assert await _dispatch_plugin_tool("plg_toolplug_ping", {"x": 1}) == {"pong": 1}

        with patch("config.PLUGIN_DIR", str(tmp_path)):
            client = _client(tmp_path)
            client.post("/plugins/toolplug/disable")

            assert await _dispatch_plugin_tool("plg_toolplug_ping", {"x": 2}) is None
            assert "plg_toolplug_ping" not in {t["name"] for t in get_all_tools()}

            client.post("/plugins/toolplug/enable")

        assert await _dispatch_plugin_tool("plg_toolplug_ping", {"x": 3}) == {"pong": 3}
        assert "plg_toolplug_ping" in {t["name"] for t in get_all_tools()}
