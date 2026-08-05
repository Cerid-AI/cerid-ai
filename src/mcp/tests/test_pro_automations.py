# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for utils.pro_automations + /settings/pro-automations REST."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def mock_redis():
    r = MagicMock()
    storage: dict[str, str] = {}

    def _get(key):
        return storage.get(key)

    def _set(key, value):
        storage[key] = value

    def _delete(key):
        storage.pop(key, None)

    r.get.side_effect = _get
    r.set.side_effect = _set
    r.delete.side_effect = _delete
    r._storage = storage  # type: ignore[attr-defined]
    return r


# ── pure utility tests ─────────────────────────────────────────────────

class TestIsEnabled:
    def test_redis_override_wins_over_env(self, mock_redis, monkeypatch):
        monkeypatch.setenv("CERID_INBOX_TRIAGE_ENABLED", "false")
        mock_redis._storage["cerid:automations:inbox_triage:enabled"] = "true"
        with patch("app.deps.get_redis", return_value=mock_redis):
            from utils.pro_automations import is_enabled
            assert is_enabled("inbox_triage") is True

    def test_env_fallback_when_redis_unset(self, mock_redis, monkeypatch):
        monkeypatch.setenv("CERID_INBOX_TRIAGE_ENABLED", "true")
        # Redis empty for this key
        with patch("app.deps.get_redis", return_value=mock_redis):
            from utils.pro_automations import is_enabled
            assert is_enabled("inbox_triage") is True

    def test_safe_default_false_when_redis_unavailable(self, monkeypatch):
        monkeypatch.delenv("CERID_INBOX_TRIAGE_ENABLED", raising=False)
        with patch("app.deps.get_redis", return_value=None):
            from utils.pro_automations import is_enabled
            assert is_enabled("inbox_triage") is False

    def test_unknown_automation_returns_false(self):
        from utils.pro_automations import is_enabled
        assert is_enabled("nonexistent") is False


class TestGetSchedule:
    def test_redis_override_wins(self, mock_redis):
        mock_redis._storage["cerid:automations:inbox_triage:schedule"] = "0 */6 * * *"
        with patch("app.deps.get_redis", return_value=mock_redis):
            from utils.pro_automations import get_schedule
            assert get_schedule("inbox_triage") == "0 */6 * * *"

    def test_default_schedule_when_redis_unset(self, mock_redis):
        with patch("app.deps.get_redis", return_value=mock_redis):
            from utils.pro_automations import get_schedule
            assert get_schedule("inbox_triage") == "*/15 * * * *"


class TestCronValidation:
    def test_set_schedule_rejects_too_few_fields(self, mock_redis):
        with patch("app.deps.get_redis", return_value=mock_redis):
            from utils.pro_automations import set_schedule
            with pytest.raises(ValueError):
                set_schedule("inbox_triage", "0 *")

    def test_set_schedule_rejects_illegal_chars(self, mock_redis):
        with patch("app.deps.get_redis", return_value=mock_redis):
            from utils.pro_automations import set_schedule
            with pytest.raises(ValueError):
                set_schedule("inbox_triage", "* * * * <script>")

    def test_set_schedule_accepts_empty_string(self, mock_redis):
        with patch("app.deps.get_redis", return_value=mock_redis):
            from utils.pro_automations import get_schedule, set_schedule
            set_schedule("inbox_triage", "")
            assert get_schedule("inbox_triage") == ""

    def test_set_schedule_accepts_valid_cron(self, mock_redis):
        with patch("app.deps.get_redis", return_value=mock_redis):
            from utils.pro_automations import get_schedule, set_schedule
            set_schedule("inbox_triage", "0 8 * * 1-5")
            assert get_schedule("inbox_triage") == "0 8 * * 1-5"


class TestGetState:
    def test_state_includes_flag_status(self, mock_redis):
        with (
            patch("app.deps.get_redis", return_value=mock_redis),
            patch("config.features.is_feature_enabled", return_value=True),
        ):
            from utils.pro_automations import get_state
            state = get_state("inbox_triage")
        assert state["feature"] == "inbox_triage"
        assert state["feature_flag_enabled"] is True
        assert state["default_schedule"] == "*/15 * * * *"
        assert len(state["cadence_presets"]) >= 3

    def test_unknown_raises(self):
        from utils.pro_automations import get_state
        with pytest.raises(KeyError):
            get_state("nonexistent")


class TestReset:
    def test_clears_both_keys(self, mock_redis):
        mock_redis._storage["cerid:automations:inbox_triage:enabled"] = "true"
        mock_redis._storage["cerid:automations:inbox_triage:schedule"] = "0 * * * *"
        with patch("app.deps.get_redis", return_value=mock_redis):
            from utils.pro_automations import reset
            reset("inbox_triage")
        assert "cerid:automations:inbox_triage:enabled" not in mock_redis._storage
        assert "cerid:automations:inbox_triage:schedule" not in mock_redis._storage


# ── REST surface ───────────────────────────────────────────────────────

def _make_app() -> FastAPI:
    from app.routers.pro_automations import router

    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def client():
    return TestClient(_make_app())


class TestListEndpoint:
    def test_returns_all_automations(self, client, mock_redis):
        with patch("app.deps.get_redis", return_value=mock_redis):
            resp = client.get("/settings/pro-automations")
        assert resp.status_code == 200
        body = resp.json()
        names = {a["feature"] for a in body["automations"]}
        assert "inbox_triage" in names
        assert "daily_digest" in names

    def test_state_carries_cadence_presets(self, client, mock_redis):
        with patch("app.deps.get_redis", return_value=mock_redis):
            body = client.get("/settings/pro-automations").json()
        first = body["automations"][0]
        assert "cadence_presets" in first
        assert any(p["cron"] == "" for p in first["cadence_presets"])  # "Off" preset present


class TestPut:
    def test_enable_blocked_when_feature_off(self, client, mock_redis):
        with (
            patch("app.deps.get_redis", return_value=mock_redis),
            patch("config.features.is_feature_enabled", return_value=False),
        ):
            resp = client.put(
                "/settings/pro-automations/inbox_triage",
                json={"enabled": True},
            )
        assert resp.status_code == 403

    def test_enable_succeeds_when_feature_on(self, client, mock_redis):
        with (
            patch("app.deps.get_redis", return_value=mock_redis),
            patch("config.features.is_feature_enabled", return_value=True),
        ):
            resp = client.put(
                "/settings/pro-automations/inbox_triage",
                json={"enabled": True, "schedule": "0 * * * *"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["enabled"] is True
        assert body["schedule"] == "0 * * * *"

    def test_invalid_cron_returns_400(self, client, mock_redis):
        with (
            patch("app.deps.get_redis", return_value=mock_redis),
            patch("config.features.is_feature_enabled", return_value=True),
        ):
            resp = client.put(
                "/settings/pro-automations/inbox_triage",
                json={"schedule": "0 *"},  # too few fields
            )
        assert resp.status_code == 400

    def test_unknown_automation_returns_404(self, client, mock_redis):
        with patch("app.deps.get_redis", return_value=mock_redis):
            resp = client.put("/settings/pro-automations/bogus", json={"enabled": False})
        assert resp.status_code == 404


class TestRunNow:
    def test_blocked_when_feature_off(self, client):
        with patch("config.features.is_feature_enabled", return_value=False):
            resp = client.post("/settings/pro-automations/inbox_triage/run-now")
        assert resp.status_code == 403

    def test_triggers_inbox_triage(self, client):
        from core.agents.inbox_triage import TriageResult

        fake = TriageResult(threads=[], sources_queried=["gmail"])
        with (
            patch("config.features.is_feature_enabled", return_value=True),
            patch(
                "core.agents.inbox_triage.triage_inboxes",
                new_callable=AsyncMock, return_value=fake,
            ),
        ):
            resp = client.post("/settings/pro-automations/inbox_triage/run-now")
        assert resp.status_code == 200
        body = resp.json()
        assert body["feature"] == "inbox_triage"
        assert body["triggered"] is True

    def test_unknown_returns_404(self, client):
        with patch("config.features.is_feature_enabled", return_value=True):
            resp = client.post("/settings/pro-automations/bogus/run-now")
        assert resp.status_code == 404


class TestDelete:
    def test_clears_overrides(self, client, mock_redis):
        mock_redis._storage["cerid:automations:inbox_triage:enabled"] = "true"
        mock_redis._storage["cerid:automations:inbox_triage:schedule"] = "* * * * *"
        with patch("app.deps.get_redis", return_value=mock_redis):
            resp = client.delete("/settings/pro-automations/inbox_triage")
        assert resp.status_code == 200
        # After reset, falls back to defaults
        body = resp.json()
        assert body["schedule"] == "*/15 * * * *"
