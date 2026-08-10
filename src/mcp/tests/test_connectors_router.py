# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for /connectors REST surface (Phase F.2 deferred cleanup)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_every_connector_instruction_doc_exists() -> None:
    """Every ConnectorMeta.instruction_doc must point at a real file.

    Operators are shown these paths in connector status + TCC banners, so a
    dangling link is a user-facing bug. Guards docs/PRO_*.md against being
    renamed or removed without updating connectors.py.
    """
    from app.routers.connectors import _CONNECTORS

    missing = {
        slug: meta.instruction_doc
        for slug, meta in _CONNECTORS.items()
        if not (REPO_ROOT / meta.instruction_doc).is_file()
    }
    assert not missing, f"connectors point at non-existent docs: {missing}"


# Connector plugins that reach the user through the Electron bridge rows
# (src/web/src/components/sources/source-rows.ts :: APPLE_BRIDGE_KINDS) rather
# than /connectors. Sources → Connectors concatenates both feeds with no dedup,
# so a connector belongs to exactly one of them or it renders twice.
_BRIDGE_ROW_CONNECTORS = {"apple_mail", "apple_imessage"}


def test_every_connector_plugin_reaches_a_ui_surface() -> None:
    """A ``"type": "connector"`` plugin in neither feed is unreachable.

    Sources → Connectors is fed by /connectors *plus* the desktop bridge rows.
    A paid connector in neither is invisible however complete its backend is.
    """
    from app.routers.connectors import _CONNECTORS

    listed_flags = {meta.feature_flag for meta in _CONNECTORS.values()}
    unreachable = {}
    for manifest_path in sorted((REPO_ROOT / "src/mcp/plugins").glob("*/manifest.json")):
        manifest = json.loads(manifest_path.read_text())
        if manifest.get("type") != "connector":
            continue
        name = manifest.get("name")
        if name in _BRIDGE_ROW_CONNECTORS:
            assert name not in _CONNECTORS, (
                f"{name} is in both feeds — the gallery would show it twice"
            )
            continue
        flags = manifest.get("feature_flags") or []
        if not any(flag in listed_flags for flag in flags):
            unreachable[name] = flags

    assert not unreachable, (
        f"connector plugins reachable from no UI surface: {unreachable}"
    )


def test_bridge_row_connectors_are_actually_in_the_bridge_row_list() -> None:
    """The exemption above is only honest if the bridge really renders them.

    Without this, dropping a kind from APPLE_BRIDGE_KINDS would leave the
    connector reaching *no* surface while the gate above stayed green.
    """
    rows_ts = (
        REPO_ROOT / "src/web/src/components/sources/source-rows.ts"
    ).read_text()
    bridge_block = rows_ts.split("APPLE_BRIDGE_KINDS", 1)[-1].split("]", 1)[0]

    # Manifest name `apple_mail` → bridge kind `mail`; `apple_imessage` → `imessage`.
    for plugin_name in sorted(_BRIDGE_ROW_CONNECTORS):
        kind = plugin_name.removeprefix("apple_")
        assert f'kind: "{kind}"' in bridge_block, (
            f"{plugin_name} is exempted from /connectors but "
            f'kind: "{kind}" is not in APPLE_BRIDGE_KINDS'
        )


def _make_app() -> FastAPI:
    from app.routers.connectors import router

    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture()
def client(monkeypatch):
    # Privacy-default: clear env so missing_env paths surface
    for var in ("CERID_CONNECTORS_BEARER", "GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET"):
        monkeypatch.delenv(var, raising=False)
    return TestClient(_make_app())


class TestListConnectors:
    def test_lists_every_connector(self, client):
        resp = client.get("/connectors")
        assert resp.status_code == 200
        slugs = [c["slug"] for c in resp.json()["connectors"]]
        assert "gmail" in slugs
        assert "google_calendar" in slugs
        assert "outlook" in slugs
        assert "outlook_calendar" in slugs
        assert "apple_calendar" in slugs
        assert "apple_photos" in slugs
        assert "apple_reminders" in slugs
        # Mail / iMessage / Notes are bridge rows, not REST rows — see
        # _BRIDGE_ROW_CONNECTORS above.
        assert "apple_mail" not in slugs
        assert "apple_imessage" not in slugs

    def test_each_carries_required_fields(self, client):
        connectors = client.get("/connectors").json()["connectors"]
        for c in connectors:
            assert "feature_flag" in c
            assert "auth_kind" in c
            assert "missing_env" in c
            assert "instruction_doc" in c

    def test_each_carries_explainer_fields(self, client):
        """P0-C.4 — every connector explains what it reads, its sync
        semantics (one-time import vs watch vs on-demand), and where the
        data lands. Non-empty strings so the UI never renders blanks."""
        connectors = client.get("/connectors").json()["connectors"]
        for c in connectors:
            for field in ("imports_desc", "sync_semantics", "lands_in"):
                assert field in c, f"{c['slug']} missing {field}"
                assert isinstance(c[field], str) and c[field].strip(), (
                    f"{c['slug']}.{field} must be a non-empty explainer string"
                )

    def test_explainer_semantics_name_the_sync_model(self, client):
        """Each sync_semantics string must actually state the model
        (on-demand / one-time / continuous / watch) rather than marketing."""
        connectors = client.get("/connectors").json()["connectors"]
        for c in connectors:
            text = c["sync_semantics"].lower()
            assert any(
                token in text
                for token in ("on-demand", "one-time", "continuous", "watch")
            ), f"{c['slug']}.sync_semantics does not name its sync model: {text!r}"

    def test_missing_env_reported_for_gmail(self, client):
        connectors = client.get("/connectors").json()["connectors"]
        gmail = next(c for c in connectors if c["slug"] == "gmail")
        assert "CERID_CONNECTORS_BEARER" in gmail["missing_env"]
        assert "GOOGLE_OAUTH_CLIENT_ID" in gmail["missing_env"]
        assert gmail["env_complete"] is False

    def test_apple_connectors_have_no_env_requirements(self, client):
        connectors = client.get("/connectors").json()["connectors"]
        apple_cal = next(c for c in connectors if c["slug"] == "apple_calendar")
        assert apple_cal["missing_env"] == []
        assert apple_cal["env_complete"] is True
        assert apple_cal["auth_kind"] == "tcc_only"


class TestSiblingReachability:
    """A closed circuit breaker is not evidence of reachability.

    The breaker opens only after three recorded failures, so a sibling nobody
    has called — including one whose container was never started — looks
    identical to a healthy one. Reporting that as reachable made `outlook` and
    `outlook_calendar` read "connected" on 2026-08-09 while no ms365 container
    existed at all.
    """

    @staticmethod
    def _with_pool(monkeypatch, entries):
        import sys
        import types

        mod = types.ModuleType("core.mcp_clients.client_pool")
        mod.get_pool = lambda: types.SimpleNamespace(list_connectors=lambda: entries)
        monkeypatch.setitem(sys.modules, "core.mcp_clients.client_pool", mod)

    def test_never_contacted_is_unknown_not_reachable(self, client, monkeypatch):
        self._with_pool(monkeypatch, [
            {"name": "ms365", "url": "http://x", "failures": 0,
             "circuit_open": False, "ever_succeeded": False},
        ])
        outlook = next(
            c for c in client.get("/connectors").json()["connectors"]
            if c["slug"] == "outlook"
        )
        assert outlook["sibling_reachable"] is None
        assert outlook["requires_sibling"] == "ms365"

    def test_a_successful_call_makes_it_reachable(self, client, monkeypatch):
        self._with_pool(monkeypatch, [
            {"name": "ms365", "url": "http://x", "failures": 0,
             "circuit_open": False, "ever_succeeded": True},
        ])
        outlook = next(
            c for c in client.get("/connectors").json()["connectors"]
            if c["slug"] == "outlook"
        )
        assert outlook["sibling_reachable"] is True

    def test_open_circuit_is_unreachable_even_if_it_once_worked(self, client, monkeypatch):
        self._with_pool(monkeypatch, [
            {"name": "ms365", "url": "http://x", "failures": 3,
             "circuit_open": True, "ever_succeeded": True},
        ])
        outlook = next(
            c for c in client.get("/connectors").json()["connectors"]
            if c["slug"] == "outlook"
        )
        assert outlook["sibling_reachable"] is False

    def test_apple_connectors_declare_no_sibling(self, client):
        cal = next(
            c for c in client.get("/connectors").json()["connectors"]
            if c["slug"] == "apple_calendar"
        )
        # Same null as "not contacted yet" — requires_sibling is what tells the
        # UI which of the two it is looking at.
        assert cal["requires_sibling"] is None
        assert cal["sibling_reachable"] is None

    def test_auth_status_separates_unknown_from_unreachable(self, client, monkeypatch):
        # The env check comes first in the detail ladder; this test is about
        # what it says once the env IS complete.
        monkeypatch.setenv("CERID_CONNECTORS_BEARER", "test-bearer")
        self._with_pool(monkeypatch, [
            {"name": "ms365", "url": "http://x", "failures": 0,
             "circuit_open": False, "ever_succeeded": False},
        ])
        detail = client.get("/connectors/outlook/auth/status").json()["detail"]
        assert "not been contacted yet" in detail
        assert "not reachable" not in detail


class TestGetConnector:
    def test_unknown_slug_returns_404(self, client):
        resp = client.get("/connectors/nonexistent")
        assert resp.status_code == 404

    def test_known_slug_returns_status(self, client):
        resp = client.get("/connectors/gmail")
        assert resp.status_code == 200
        body = resp.json()
        assert body["slug"] == "gmail"
        assert body["feature_flag"] == "gmail_connector"


class TestStartAuth:
    """Google's flow is an MCP tool call, not a browsable page.

    This used to assert only that *some* URL came back, which the old
    implementation satisfied by returning ``http://127.0.0.1:8810/oauth/start``
    — a route that 404s. The test passed for as long as the feature was broken.
    """

    def test_google_without_an_account_asks_for_one_instead_of_inventing_a_url(
        self, client, monkeypatch,
    ):
        monkeypatch.delenv("USER_GOOGLE_EMAIL", raising=False)
        body = client.post("/connectors/gmail/auth/start").json()
        assert body["auth_kind"] == "google_oauth"
        assert body["auth_url"] is None
        assert "USER_GOOGLE_EMAIL" in body["instructions"]

    def test_google_returns_the_consent_url_the_sibling_produced(
        self, client, monkeypatch,
    ):
        import sys
        import types

        url = "https://accounts.google.com/o/oauth2/auth?client_id=x&scope=y"
        mod = types.ModuleType("core.mcp_clients.client_pool")

        async def _call_tool(name, tool, args):
            assert tool == "start_google_auth"
            return f"Please visit {url} to authorize."

        mod.get_pool = lambda: types.SimpleNamespace(call_tool=_call_tool)
        monkeypatch.setitem(sys.modules, "core.mcp_clients.client_pool", mod)
        monkeypatch.setenv("USER_GOOGLE_EMAIL", "someone@example.com")

        body = client.post("/connectors/gmail/auth/start").json()
        assert body["auth_url"] == url
        assert "someone@example.com" in body["instructions"]

    def test_google_surfaces_an_unreachable_sibling_rather_than_a_url(
        self, client, monkeypatch,
    ):
        import sys
        import types

        mod = types.ModuleType("core.mcp_clients.client_pool")

        async def _boom(*_a, **_k):
            raise ConnectionError("connection refused")

        mod.get_pool = lambda: types.SimpleNamespace(call_tool=_boom)
        monkeypatch.setitem(sys.modules, "core.mcp_clients.client_pool", mod)
        monkeypatch.setenv("USER_GOOGLE_EMAIL", "someone@example.com")

        body = client.post("/connectors/gmail/auth/start").json()
        assert body["auth_url"] is None
        assert "connection refused" in body["instructions"]

    def test_microsoft_returns_device_code_instructions(self, client):
        resp = client.post("/connectors/outlook/auth/start")
        body = resp.json()
        assert body["auth_kind"] == "msal_device_code"
        assert "devicelogin" in body["instructions"]

    def test_apple_returns_system_settings_link(self, client):
        resp = client.post("/connectors/apple_calendar/auth/start")
        body = resp.json()
        assert body["auth_kind"] == "tcc_only"
        assert body["settings_url"].startswith("x-apple.systempreferences:")

    def test_unknown_slug_404s(self, client):
        resp = client.post("/connectors/nope/auth/start")
        assert resp.status_code == 404


class TestAuthStatus:
    def test_microsoft_incomplete_when_env_missing(self, client):
        resp = client.get("/connectors/outlook/auth/status")
        body = resp.json()
        assert body["slug"] == "outlook"
        assert body["completed"] is False
        assert "env" in body["detail"].lower() or "missing" in body["detail"].lower()

    def test_apple_complete_when_data_source_configured(self, client):
        # Stub the registry so it returns a "configured" data source.
        from unittest.mock import MagicMock

        mock_ds = MagicMock()
        mock_ds.is_configured.return_value = True

        with (
            patch("app.data_sources.registry.get", return_value=mock_ds),
            patch("config.features.is_feature_enabled", return_value=True),
        ):
            resp = client.get("/connectors/apple_calendar/auth/status")
        body = resp.json()
        assert body["slug"] == "apple_calendar"
        assert body["completed"] is True


class TestDisconnect:
    """These instructions are the operator's only path to revoking access, so
    they must be runnable. All three shipped commands were wrong until
    2026-08-10 — and because the endpoint reports ``cleared: False`` either
    way, following them cleared nothing and reported no error.
    """

    def test_google_names_the_real_credentials_path(self, client):
        resp = client.post("/connectors/gmail/disconnect")
        body = resp.json()
        assert body["cleared"] is False
        assert "docker compose" in body["detail"]
        # The server runs as uid 1000 with HOME=/home/app; /root does not exist
        # in that image and is not writable by the server user.
        assert "/home/app/.google_workspace_mcp" in body["detail"]
        assert "/root/" not in body["detail"]

    def test_microsoft_logout_command_is_actually_invocable(self, client):
        resp = client.post("/connectors/outlook/disconnect")
        detail = resp.json()["detail"]
        # `ms365-mcp` is not an executable in the image (the package bin is
        # `ms-365-mcp-server`), and logout is a FLAG, not a subcommand.
        assert "node dist/index.js --logout" in detail
        assert "ms365-mcp logout" not in detail

    @pytest.mark.parametrize("slug", ["gmail", "outlook", "outlook_calendar"])
    def test_every_compose_command_can_resolve_its_service(self, client, slug):
        """Naming only the stacks file puts you in compose project
        `connectors`, where the service does not exist; omitting the profile
        filters it out entirely. Both were wrong in every shipped string."""
        detail = client.post(f"/connectors/{slug}/disconnect").json()["detail"]
        assert "-f docker-compose.yml" in detail
        assert "--profile pro" in detail

    def test_apple_returns_settings_revocation(self, client):
        resp = client.post("/connectors/apple_calendar/disconnect")
        body = resp.json()
        assert "System Settings" in body["detail"]
