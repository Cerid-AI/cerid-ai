# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for routers/automations.py — scheduled automation CRUD and execution."""
from __future__ import annotations

import json
from collections import defaultdict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers.automations import (
    Automation,
    AutomationAction,
    AutomationRun,
    _auto_key,
    execute_automation,
    router,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


def _mock_redis():
    """Create a mock Redis with in-memory dict backend for realistic CRUD."""
    store: dict[str, str] = {}
    zsets: dict[str, dict] = defaultdict(dict)
    r = MagicMock()
    r.get = lambda k: store.get(k)
    r.set = lambda k, v: store.__setitem__(k, v)
    r.delete = lambda *keys: [store.pop(k, None) for k in keys]
    r.keys = lambda pattern: [k for k in store if k.startswith(pattern.replace("*", ""))]
    r.mget = lambda keys: [store.get(k) for k in keys]
    r.zadd = lambda k, mapping: zsets[k].update(mapping)
    r.zrange = lambda k, start, end: list(zsets.get(k, {}).keys())
    r.zrevrange = lambda k, start, end: list(zsets.get(k, {}).keys())[:end + 1] if end >= 0 else list(zsets.get(k, {}).keys())
    r.zremrangebyrank = lambda k, start, end: None
    r.rpush = lambda k, v: None
    r.llen = lambda k: 0
    r.pipeline = MagicMock(return_value=MagicMock())
    return r, store


def _sample_create_payload(**overrides) -> dict:
    base = {
        "name": "Daily Research",
        "description": "Run a research query every morning",
        "prompt": "What are the latest developments in AI safety?",
        "schedule": "0 9 * * *",
        "action": "notify",
        "domains": ["general"],
        "enabled": True,
    }
    base.update(overrides)
    return base


def _seed_automation(store: dict, **overrides) -> dict:
    """Insert an automation directly into the mock store, return its data."""
    auto = {
        "id": "auto-001",
        "name": "Seeded Automation",
        "description": "",
        "prompt": "test prompt",
        "schedule": "0 9 * * *",
        "action": "notify",
        "domains": ["general"],
        "enabled": True,
        "created_at": "2026-03-01T00:00:00+00:00",
        "updated_at": "2026-03-01T00:00:00+00:00",
        "last_run_at": None,
        "last_status": None,
        "run_count": 0,
    }
    auto.update(overrides)
    store[_auto_key(auto["id"])] = json.dumps(auto)
    return auto


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestListAutomations:
    def test_empty_list(self) -> None:
        redis, _ = _mock_redis()
        with patch("routers.automations.get_redis", return_value=redis):
            app = _make_app()
            client = TestClient(app)
            resp = client.get("/automations")
            assert resp.status_code == 200
            assert resp.json() == []

    def test_returns_seeded_automations(self) -> None:
        redis, store = _mock_redis()
        _seed_automation(store, id="a1", name="First")
        _seed_automation(store, id="a2", name="Second")
        with patch("routers.automations.get_redis", return_value=redis):
            app = _make_app()
            client = TestClient(app)
            resp = client.get("/automations")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data) == 2
            names = {a["name"] for a in data}
            assert names == {"First", "Second"}


class TestCreateAutomation:
    def test_create_returns_201(self) -> None:
        redis, store = _mock_redis()
        with (
            patch("routers.automations.get_redis", return_value=redis),
            patch("routers.automations._validate_cron"),
            patch("routers.automations._register_job"),
        ):
            app = _make_app()
            client = TestClient(app)
            resp = client.post("/automations", json=_sample_create_payload())
            assert resp.status_code == 201
            data = resp.json()
            assert data["name"] == "Daily Research"
            assert "id" in data
            assert "created_at" in data
            assert data["enabled"] is True
            # Verify persisted in store
            assert len(store) == 1

    def test_create_disabled_skips_register(self) -> None:
        redis, _ = _mock_redis()
        with (
            patch("routers.automations.get_redis", return_value=redis),
            patch("routers.automations._validate_cron"),
            patch("routers.automations._register_job") as mock_reg,
        ):
            app = _make_app()
            client = TestClient(app)
            resp = client.post("/automations", json=_sample_create_payload(enabled=False))
            assert resp.status_code == 201
            mock_reg.assert_not_called()

    def test_create_invalid_cron_returns_422(self) -> None:
        redis, _ = _mock_redis()

        def _bad_cron(expr):
            from fastapi import HTTPException
            raise HTTPException(status_code=422, detail="Invalid cron expression")

        with (
            patch("routers.automations.get_redis", return_value=redis),
            patch("routers.automations._validate_cron", side_effect=_bad_cron),
        ):
            app = _make_app()
            client = TestClient(app)
            resp = client.post("/automations", json=_sample_create_payload(schedule="bad"))
            assert resp.status_code == 422

    def test_create_missing_name_returns_422(self) -> None:
        redis, _ = _mock_redis()
        with patch("routers.automations.get_redis", return_value=redis):
            app = _make_app()
            client = TestClient(app)
            payload = _sample_create_payload()
            del payload["name"]
            resp = client.post("/automations", json=payload)
            assert resp.status_code == 422


class TestGetAutomation:
    def test_get_existing(self) -> None:
        redis, store = _mock_redis()
        _seed_automation(store, id="auto-get", name="Fetched")
        with patch("routers.automations.get_redis", return_value=redis):
            app = _make_app()
            client = TestClient(app)
            resp = client.get("/automations/auto-get")
            assert resp.status_code == 200
            assert resp.json()["name"] == "Fetched"

    def test_get_missing_returns_404(self) -> None:
        redis, _ = _mock_redis()
        with patch("routers.automations.get_redis", return_value=redis):
            app = _make_app()
            client = TestClient(app)
            resp = client.get("/automations/nonexistent")
            assert resp.status_code == 404


class TestUpdateAutomation:
    def test_update_name(self) -> None:
        redis, store = _mock_redis()
        _seed_automation(store, id="auto-upd", name="Old Name")
        with (
            patch("routers.automations.get_redis", return_value=redis),
            patch("routers.automations._register_job"),
        ):
            app = _make_app()
            client = TestClient(app)
            resp = client.put("/automations/auto-upd", json={"name": "New Name"})
            assert resp.status_code == 200
            assert resp.json()["name"] == "New Name"

    def test_update_schedule_validates_cron(self) -> None:
        redis, store = _mock_redis()
        _seed_automation(store, id="auto-cron")

        def _bad_cron(expr):
            from fastapi import HTTPException
            raise HTTPException(status_code=422, detail="Invalid cron expression")

        with (
            patch("routers.automations.get_redis", return_value=redis),
            patch("routers.automations._validate_cron", side_effect=_bad_cron),
        ):
            app = _make_app()
            client = TestClient(app)
            resp = client.put("/automations/auto-cron", json={"schedule": "bad"})
            assert resp.status_code == 422

    def test_update_missing_returns_404(self) -> None:
        redis, _ = _mock_redis()
        with patch("routers.automations.get_redis", return_value=redis):
            app = _make_app()
            client = TestClient(app)
            resp = client.put("/automations/missing", json={"name": "x"})
            assert resp.status_code == 404

    def test_disable_unregisters_job(self) -> None:
        redis, store = _mock_redis()
        _seed_automation(store, id="auto-dis", enabled=True)
        with (
            patch("routers.automations.get_redis", return_value=redis),
            patch("routers.automations._unregister_job") as mock_unreg,
        ):
            app = _make_app()
            client = TestClient(app)
            resp = client.put("/automations/auto-dis", json={"enabled": False})
            assert resp.status_code == 200
            assert resp.json()["enabled"] is False
            mock_unreg.assert_called_once_with("auto-dis")


class TestDeleteAutomation:
    def test_delete_existing(self) -> None:
        redis, store = _mock_redis()
        _seed_automation(store, id="auto-del")
        with (
            patch("routers.automations.get_redis", return_value=redis),
            patch("routers.automations._unregister_job"),
        ):
            app = _make_app()
            client = TestClient(app)
            resp = client.delete("/automations/auto-del")
            assert resp.status_code == 200
            assert resp.json()["status"] == "deleted"

    def test_delete_missing_returns_404(self) -> None:
        redis, _ = _mock_redis()
        with patch("routers.automations.get_redis", return_value=redis):
            app = _make_app()
            client = TestClient(app)
            resp = client.delete("/automations/ghost")
            assert resp.status_code == 404


class TestEnableDisable:
    def test_enable_registers_job(self) -> None:
        redis, store = _mock_redis()
        _seed_automation(store, id="auto-en", enabled=False)
        with (
            patch("routers.automations.get_redis", return_value=redis),
            patch("routers.automations._register_job") as mock_reg,
        ):
            app = _make_app()
            client = TestClient(app)
            resp = client.post("/automations/auto-en/enable")
            assert resp.status_code == 200
            assert resp.json()["enabled"] is True
            mock_reg.assert_called_once()

    def test_disable_unregisters_job(self) -> None:
        redis, store = _mock_redis()
        _seed_automation(store, id="auto-dis2", enabled=True)
        with (
            patch("routers.automations.get_redis", return_value=redis),
            patch("routers.automations._unregister_job") as mock_unreg,
        ):
            app = _make_app()
            client = TestClient(app)
            resp = client.post("/automations/auto-dis2/disable")
            assert resp.status_code == 200
            assert resp.json()["enabled"] is False
            mock_unreg.assert_called_once_with("auto-dis2")

    def test_enable_missing_returns_404(self) -> None:
        redis, _ = _mock_redis()
        with patch("routers.automations.get_redis", return_value=redis):
            app = _make_app()
            client = TestClient(app)
            resp = client.post("/automations/nope/enable")
            assert resp.status_code == 404


class TestManualRun:
    def test_trigger_run_success(self) -> None:
        redis, store = _mock_redis()
        _seed_automation(store, id="auto-run")
        mock_run = AutomationRun(
            automation_id="auto-run",
            run_id="run-001",
            started_at="2026-03-01T00:00:00+00:00",
            completed_at="2026-03-01T00:01:00+00:00",
            status="success",
        )
        with (
            patch("routers.automations.get_redis", return_value=redis),
            patch("routers.automations.execute_automation", new_callable=AsyncMock, return_value=mock_run),
        ):
            app = _make_app()
            client = TestClient(app)
            resp = client.post("/automations/auto-run/run")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "success"
            assert data["run_id"] == "run-001"

    def test_trigger_run_missing_returns_404(self) -> None:
        redis, _ = _mock_redis()
        with patch("routers.automations.get_redis", return_value=redis):
            app = _make_app()
            client = TestClient(app)
            resp = client.post("/automations/missing/run")
            assert resp.status_code == 404


class TestHistory:
    def test_history_returns_runs(self) -> None:
        redis, store = _mock_redis()
        _seed_automation(store, id="auto-hist")
        # Seed a run directly in the store
        run_data = {
            "automation_id": "auto-hist",
            "run_id": "r1",
            "started_at": "2026-03-01T00:00:00+00:00",
            "completed_at": "2026-03-01T00:01:00+00:00",
            "status": "success",
            "result": None,
            "error": None,
        }
        run_key = "cerid:automation_runs:auto-hist:r1"
        store[run_key] = json.dumps(run_data)
        # Add to sorted-set index
        redis.zrevrange = MagicMock(return_value=["r1"])
        with patch("routers.automations.get_redis", return_value=redis):
            app = _make_app()
            client = TestClient(app)
            resp = client.get("/automations/auto-hist/history")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data) == 1
            assert data[0]["run_id"] == "r1"

    def test_history_missing_automation_returns_404(self) -> None:
        redis, _ = _mock_redis()
        with patch("routers.automations.get_redis", return_value=redis):
            app = _make_app()
            client = TestClient(app)
            resp = client.get("/automations/missing/history")
            assert resp.status_code == 404


class TestPresets:
    def test_presets_endpoint(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.get("/automations/presets")
        assert resp.status_code == 200
        data = resp.json()
        assert "daily_morning" in data
        assert data["daily_morning"]["cron"] == "0 9 * * *"


class TestExecuteAutomation:
    @pytest.mark.asyncio
    async def test_execute_success(self) -> None:
        redis, store = _mock_redis()
        auto = Automation(
            id="exec-1",
            name="Exec Test",
            prompt="test",
            schedule="0 9 * * *",
            action=AutomationAction.NOTIFY,
            domains=["general"],
            enabled=True,
            created_at="2026-03-01T00:00:00+00:00",
            updated_at="2026-03-01T00:00:00+00:00",
        )
        mock_result = {
            "context": "some context",
            "sources": [{"content": "c", "relevance": 0.9}],
            "confidence": 0.85,
        }
        with (
            patch("routers.automations.get_redis", return_value=redis),
            patch("agents.query_agent.agent_query", new_callable=AsyncMock, return_value=mock_result),
        ):
            run = await execute_automation(auto)
            assert run.status == "success"
            assert run.error is None
            assert run.result is not None
            assert run.result["confidence"] == 0.85

    @pytest.mark.asyncio
    async def test_execute_error_captured(self) -> None:
        redis, store = _mock_redis()
        auto = Automation(
            id="exec-err",
            name="Error Test",
            prompt="fail",
            schedule="0 9 * * *",
            action=AutomationAction.NOTIFY,
            domains=["general"],
            enabled=True,
            created_at="2026-03-01T00:00:00+00:00",
            updated_at="2026-03-01T00:00:00+00:00",
        )
        with (
            patch("routers.automations.get_redis", return_value=redis),
            patch("agents.query_agent.agent_query", new_callable=AsyncMock, side_effect=RuntimeError("boom")),
        ):
            run = await execute_automation(auto)
            assert run.status == "error"
            assert run.error == "boom"
