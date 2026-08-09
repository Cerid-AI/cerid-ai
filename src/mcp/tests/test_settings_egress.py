# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Coverage for GET /settings/egress — the data-egress transparency endpoint (Task 1.3b).

Every assertion here is anchored to a real code path (see app/routers/settings.py's
per-row comments), not to the setting's name — that's the point of the endpoint.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.routers.settings as settings_module
import config
import utils.web_search as web_search_module
from app.routers.settings import router

_ALL_CHANNELS = {
    "chat_llm",
    "internal_llm",
    "ingest_enrichment",
    "external_verification",
    "web_search",
    "model_catalog_refresh",
    "model_downloads",
    "error_reporting",
    "kb_backup_sync",
}


@pytest.fixture
def client(monkeypatch):
    """Isolated TestClient with a deterministic 'typical deployment' baseline.

    Baseline = OpenRouter configured (the common case), no Tavily/SearXNG,
    no Sentry DSN, default categorize/verification/auto-update flags, and
    the HF-model-cache check stubbed out (real cache state on the dev/CI
    box would otherwise make model_downloads flaky).

    web_search's status is now sourced from the real
    ``utils.web_search.get_search_provider()`` factory (Task 1.3b hardening),
    which reads ``TAVILY_API_KEY``/``SEARXNG_URL`` independently of
    ``config`` (a fresh ``os.getenv`` call and a module-level constant
    captured at ``utils.web_search`` import time, respectively) — so both
    are cleared here directly, in addition to the ``config`` attributes
    (which still drive the SearXNG destination string).
    """
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "test-openrouter-key", raising=False)
    monkeypatch.setattr(config, "INTERNAL_LLM_PROVIDER", "openrouter", raising=False)
    monkeypatch.setattr(config, "CATEGORIZE_MODE", "smart", raising=False)
    monkeypatch.setattr(config, "ENABLE_EXTERNAL_VERIFICATION", True, raising=False)
    monkeypatch.setattr(config, "TAVILY_API_KEY", "", raising=False)
    monkeypatch.setattr(config, "SEARXNG_URL", "", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.setattr(web_search_module, "SEARXNG_URL", "", raising=False)
    monkeypatch.setattr(config, "MODEL_AUTO_UPDATE_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "ENABLE_SENTRY", False, raising=False)
    monkeypatch.setattr(config, "SYNC_DIR", "/tmp/cerid-sync", raising=False)
    monkeypatch.delenv("SENTRY_DSN_MCP", raising=False)
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    monkeypatch.setattr(settings_module, "_hf_model_cached", lambda *a, **kw: False)

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _egress_by_channel(response_json: dict) -> dict[str, dict]:
    return {row["channel"]: row for row in response_json["egress"]}


class TestEgressShape:
    def test_all_channels_present(self, client):
        resp = client.get("/settings/egress")
        assert resp.status_code == 200
        rows = _egress_by_channel(resp.json())
        assert set(rows.keys()) == _ALL_CHANNELS

    def test_every_row_has_required_fields_and_valid_status(self, client):
        resp = client.get("/settings/egress")
        data = resp.json()
        assert "egress" in data
        for row in data["egress"]:
            assert row["status"] in ("local", "external_off", "external_on")
            for field in ("channel", "destination", "trigger", "payload_class", "setting_key"):
                assert isinstance(row[field], str) and row[field]


class TestBaselineStatuses:
    """Typical deployment: OpenRouter configured, nothing else opted in."""

    def test_chat_llm_external_on(self, client):
        rows = _egress_by_channel(client.get("/settings/egress").json())
        assert rows["chat_llm"]["status"] == "external_on"

    def test_internal_llm_external_on(self, client):
        rows = _egress_by_channel(client.get("/settings/egress").json())
        assert rows["internal_llm"]["status"] == "external_on"

    def test_ingest_enrichment_external_on(self, client):
        rows = _egress_by_channel(client.get("/settings/egress").json())
        assert rows["ingest_enrichment"]["status"] == "external_on"

    def test_external_verification_external_on(self, client):
        rows = _egress_by_channel(client.get("/settings/egress").json())
        assert rows["external_verification"]["status"] == "external_on"

    def test_model_catalog_refresh_external_on(self, client):
        rows = _egress_by_channel(client.get("/settings/egress").json())
        assert rows["model_catalog_refresh"]["status"] == "external_on"

    def test_model_downloads_external_off_when_not_cached(self, client):
        rows = _egress_by_channel(client.get("/settings/egress").json())
        assert rows["model_downloads"]["status"] == "external_off"

    def test_error_reporting_external_off_by_default(self, client):
        rows = _egress_by_channel(client.get("/settings/egress").json())
        assert rows["error_reporting"]["status"] == "external_off"

    def test_kb_backup_sync_always_local(self, client):
        rows = _egress_by_channel(client.get("/settings/egress").json())
        assert rows["kb_backup_sync"]["status"] == "local"

    def test_web_search_external_on_via_openrouter_fallback(self, client):
        """Discrepancy vs the original plan: web_search is NOT off just because
        Tavily/SearXNG are unconfigured. utils/web_search.py::get_search_provider()
        falls back to OpenRouter's ':online' model, which works as long as
        OPENROUTER_API_KEY is set (the baseline here). So the real default
        status is external_on, not external_off.
        """
        rows = _egress_by_channel(client.get("/settings/egress").json())
        assert rows["web_search"]["status"] == "external_on"
        assert "OpenRouter" in rows["web_search"]["destination"]


class TestInternalLlmProviderRouting:
    def test_ollama_flips_internal_llm_and_ingest_enrichment_to_local(self, client, monkeypatch):
        monkeypatch.setattr(config, "INTERNAL_LLM_PROVIDER", "ollama", raising=False)
        rows = _egress_by_channel(client.get("/settings/egress").json())
        assert rows["internal_llm"]["status"] == "local"
        assert rows["ingest_enrichment"]["status"] == "local"

    def test_quenchforge_also_counts_as_local(self, client, monkeypatch):
        monkeypatch.setattr(config, "INTERNAL_LLM_PROVIDER", "quenchforge", raising=False)
        rows = _egress_by_channel(client.get("/settings/egress").json())
        assert rows["internal_llm"]["status"] == "local"

    def test_external_verification_ignores_internal_llm_provider(self, client, monkeypatch):
        """core/agents/hallucination/verification.py always uses call_llm_raw
        (hardcoded OpenRouter) — it never routes through call_internal_llm, so
        switching INTERNAL_LLM_PROVIDER to ollama must NOT flip this channel.
        """
        monkeypatch.setattr(config, "INTERNAL_LLM_PROVIDER", "ollama", raising=False)
        rows = _egress_by_channel(client.get("/settings/egress").json())
        assert rows["external_verification"]["status"] == "external_on"


class TestCategorizeModeManual:
    def test_manual_mode_is_local_regardless_of_provider(self, client, monkeypatch):
        monkeypatch.setattr(config, "CATEGORIZE_MODE", "manual", raising=False)
        monkeypatch.setattr(config, "INTERNAL_LLM_PROVIDER", "openrouter", raising=False)
        rows = _egress_by_channel(client.get("/settings/egress").json())
        assert rows["ingest_enrichment"]["status"] == "local"


class TestExternalVerificationToggle:
    def test_disabled_is_local_not_external(self, client, monkeypatch):
        monkeypatch.setattr(config, "ENABLE_EXTERNAL_VERIFICATION", False, raising=False)
        rows = _egress_by_channel(client.get("/settings/egress").json())
        assert rows["external_verification"]["status"] == "local"


class TestModelAutoUpdate:
    def test_disabled_flips_catalog_refresh_off(self, client, monkeypatch):
        monkeypatch.setattr(config, "MODEL_AUTO_UPDATE_ENABLED", False, raising=False)
        rows = _egress_by_channel(client.get("/settings/egress").json())
        assert rows["model_catalog_refresh"]["status"] == "external_off"


class TestWebSearchProviders:
    def test_tavily_key_set_external_on(self, client, monkeypatch):
        """get_search_provider() reads TAVILY_API_KEY via a fresh os.getenv()
        call (utils/web_search.py::_tavily_api_key), independent of config —
        set the real env var, not just the config attribute."""
        monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-key")
        monkeypatch.setattr(config, "TAVILY_API_KEY", "tvly-test-key", raising=False)
        rows = _egress_by_channel(client.get("/settings/egress").json())
        assert rows["web_search"]["status"] == "external_on"
        assert "Tavily" in rows["web_search"]["destination"]

    def test_searxng_url_set_external_on(self, client, monkeypatch):
        """utils.web_search.SEARXNG_URL is a module-level constant captured
        at import time, independent of config — patch it directly so
        get_search_provider() actually picks SearxngProvider."""
        monkeypatch.setattr(web_search_module, "SEARXNG_URL", "http://localhost:8080", raising=False)
        monkeypatch.setattr(config, "SEARXNG_URL", "http://localhost:8080", raising=False)
        rows = _egress_by_channel(client.get("/settings/egress").json())
        assert rows["web_search"]["status"] == "external_on"
        assert "SearXNG" in rows["web_search"]["destination"]

    def test_no_provider_at_all_is_external_off(self, client, monkeypatch):
        """Only genuinely off when the OpenRouter fallback is also unavailable."""
        monkeypatch.setattr(config, "OPENROUTER_API_KEY", "", raising=False)
        rows = _egress_by_channel(client.get("/settings/egress").json())
        assert rows["web_search"]["status"] == "external_off"


class TestChatLlmRequiresKey:
    def test_no_openrouter_key_is_external_off(self, client, monkeypatch):
        """app/routers/chat.py 503s before any network attempt when unconfigured
        — no egress actually occurs, so this must not read as external_on."""
        monkeypatch.setattr(config, "OPENROUTER_API_KEY", "", raising=False)
        rows = _egress_by_channel(client.get("/settings/egress").json())
        assert rows["chat_llm"]["status"] == "external_off"


class TestErrorReporting:
    def test_dsn_set_is_external_on(self, client, monkeypatch):
        monkeypatch.setenv("SENTRY_DSN_MCP", "https://example@o0.ingest.sentry.io/1")
        monkeypatch.setattr(config, "ENABLE_SENTRY", True, raising=False)
        rows = _egress_by_channel(client.get("/settings/egress").json())
        assert rows["error_reporting"]["status"] == "external_on"

    def test_enable_sentry_flag_is_inert(self, client, monkeypatch):
        """Discrepancy vs the original plan: config.ENABLE_SENTRY is never read
        by app/observability/sentry_init.py::init_sentry() — the only gate is
        SENTRY_DSN_MCP/SENTRY_DSN being set. Prove the flag doesn't affect the
        reported status either way (ENABLE_SENTRY=False + DSN set still reports
        external_on — anything else would misreport an active integration as off).
        """
        monkeypatch.setenv("SENTRY_DSN_MCP", "https://example@o0.ingest.sentry.io/1")
        monkeypatch.setattr(config, "ENABLE_SENTRY", False, raising=False)
        rows = _egress_by_channel(client.get("/settings/egress").json())
        assert rows["error_reporting"]["status"] == "external_on"


class TestModelDownloadsCache:
    def test_cached_models_report_local(self, client, monkeypatch):
        monkeypatch.setattr(settings_module, "_hf_model_cached", lambda *a, **kw: True)
        rows = _egress_by_channel(client.get("/settings/egress").json())
        assert rows["model_downloads"]["status"] == "local"


class TestResponseValidation:
    def test_response_matches_egress_report_model(self, client):
        resp = client.get("/settings/egress")
        report = settings_module.EgressReport.model_validate(resp.json())
        assert len(report.egress) == len(_ALL_CHANNELS)
