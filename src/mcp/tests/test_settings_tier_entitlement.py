# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""POST /settings/tier must not hand out a tier the server is not entitled to (F034).

The endpoint persists ``CERID_TIER`` to the env file, which is exactly the var
``app/routers/license.py:_baseline_tier()`` reads — so without an entitlement
check it is a self-service upgrade to enterprise that skips the license router
entirely.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


class FakeRedis:
    def __init__(self, initial: dict[str, str] | None = None) -> None:
        self._data = dict(initial or {})

    def get(self, key: str):
        return self._data.get(key)

    def set(self, key: str, value) -> None:
        self._data[key] = str(value)

    def delete(self, *keys: str) -> None:
        for k in keys:
            self._data.pop(k, None)


def _licensed(tier: str) -> FakeRedis:
    import app.routers.license as lic

    return FakeRedis({
        lic._LICENSE_STATUS: json.dumps(
            {"active": True, "tier": tier, "expires_at": int(time.time()) + 86400},
        ),
    })


@pytest.fixture()
def env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / ".env"
    path.write_text("EXISTING=1\n", encoding="utf-8")
    monkeypatch.setenv("CERID_ENV_FILE", str(path))
    sys.modules.pop("app.routers.setup", None)
    importlib.import_module("app.routers.setup")
    return path


@pytest.fixture(autouse=True)
def _restore_tier_state(monkeypatch: pytest.MonkeyPatch):
    from config import features

    original_tier = features.current_tier()
    original_env = os.environ.get("CERID_TIER")
    monkeypatch.delenv("CERID_TIER", raising=False)
    features.set_tier("community")
    yield
    if original_env is None:
        os.environ.pop("CERID_TIER", None)
    else:
        os.environ["CERID_TIER"] = original_env
    features.set_tier(original_tier)


def _client() -> TestClient:
    import app.routers.settings as settings_mod

    app = FastAPI()
    app.include_router(settings_mod.router)
    return TestClient(app)


@pytest.fixture()
def patched_redis(monkeypatch: pytest.MonkeyPatch):
    import app.routers.settings as settings_mod

    def _install(redis):
        monkeypatch.setattr(settings_mod, "get_redis", lambda: redis)
        return _client()

    return _install


class TestTierRequiresEntitlement:
    def test_unlicensed_upgrade_to_enterprise_is_refused(
        self, env_file: Path, patched_redis,
    ) -> None:
        client = patched_redis(FakeRedis())
        resp = client.post("/settings/tier", json={"tier": "enterprise"})
        assert resp.status_code == 403, resp.text

        from config import features
        assert features.current_tier() == "community"
        assert "CERID_TIER=enterprise" not in env_file.read_text(encoding="utf-8")
        assert os.environ.get("CERID_TIER") != "enterprise"

    def test_unlicensed_upgrade_to_pro_is_refused(
        self, env_file: Path, patched_redis,
    ) -> None:
        client = patched_redis(FakeRedis())
        assert client.post("/settings/tier", json={"tier": "pro"}).status_code == 403
        assert "CERID_TIER=pro" not in env_file.read_text(encoding="utf-8")

    def test_downgrade_is_always_allowed(self, env_file: Path, patched_redis) -> None:
        client = patched_redis(FakeRedis())
        resp = client.post("/settings/tier", json={"tier": "community"})
        assert resp.status_code == 200, resp.text

    def test_licensed_enterprise_may_set_enterprise(
        self, env_file: Path, patched_redis,
    ) -> None:
        client = patched_redis(_licensed("enterprise"))
        resp = client.post("/settings/tier", json={"tier": "enterprise"})
        assert resp.status_code == 200, resp.text
        assert "CERID_TIER=enterprise" in env_file.read_text(encoding="utf-8")

    def test_pro_license_may_not_reach_enterprise(
        self, env_file: Path, patched_redis,
    ) -> None:
        client = patched_redis(_licensed("pro"))
        assert client.post("/settings/tier", json={"tier": "enterprise"}).status_code == 403
        assert client.post("/settings/tier", json={"tier": "pro"}).status_code == 200

    def test_redis_failure_falls_back_to_the_env_baseline(
        self, env_file: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import app.routers.settings as settings_mod

        def _boom():
            raise RuntimeError("redis down")

        monkeypatch.setattr(settings_mod, "get_redis", _boom)
        client = _client()
        assert client.post("/settings/tier", json={"tier": "enterprise"}).status_code == 403

    def test_operator_env_pin_is_still_honoured(
        self, env_file: Path, monkeypatch: pytest.MonkeyPatch, patched_redis,
    ) -> None:
        """CERID_TIER is the operator's floor — an env-pinned box keeps working."""
        monkeypatch.setenv("CERID_TIER", "enterprise")
        client = patched_redis(FakeRedis())
        assert client.post("/settings/tier", json={"tier": "enterprise"}).status_code == 200
