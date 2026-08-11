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

    @pytest.fixture(autouse=True)
    def _entitled(self, monkeypatch):
        """These cases are about the FLOW, so entitle the connector. Starting
        an authorization is Pro-gated (see TestStartAuthIsGated); without this
        every case below would assert against a 403 instead."""
        import config.features as features

        monkeypatch.setattr(features, "is_feature_enabled", lambda _f: True)

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

    def test_apple_returns_system_settings_link(self, client):
        resp = client.post("/connectors/apple_calendar/auth/start")
        body = resp.json()
        assert body["auth_kind"] == "tcc_only"
        assert body["settings_url"].startswith("x-apple.systempreferences:")

    def test_unknown_slug_404s(self, client):
        resp = client.post("/connectors/nope/auth/start")
        assert resp.status_code == 404


def _tool_result(text: str, *, is_error: bool = False):
    """The shape ``MCPClientPool.call_tool`` actually returns.

    A bare ``str`` would pass through ``tool_text`` untouched and so would not
    exercise the flattening the real reply needs; ``structuredContent`` is None
    on the ms365 sibling, which is the whole reason the code has to be parsed
    out of prose.
    """
    import types

    return types.SimpleNamespace(
        content=[types.SimpleNamespace(text=text)],
        structuredContent=None,
        isError=is_error,
    )


# Verbatim reply from the running ms365 sibling's `login` tool. Note the
# top-level key is "error" and isError is False *on the success path* — there
# is no flag that distinguishes this from a failure, so the parser cannot
# branch on one.
_LIVE_LOGIN_REPLY = (
    '{"error":"device_code_required","message":"To sign in, use a web browser '
    "to open the page https://www.microsoft.com/link and enter the code "
    'VDBF68NR to authenticate.\\nAfter login run the \\"verify login\\" command"}'
)


class TestMicrosoftDeviceCodeStart:
    """The device code comes from the sibling, not from a docstring.

    Until 2026-08-10 this branch returned a fixed string telling the operator to
    `docker compose exec` into a container and read a code off its stdout —
    impossible from the desktop app, and the declared ``device_code`` /
    ``verification_uri`` fields were assigned by nothing anywhere in the repo.
    `login` is a live MCP tool on the sibling (the stack passes
    --enable-auth-tools), so the code can simply be fetched.
    """

    @pytest.fixture(autouse=True)
    def _entitled(self, monkeypatch):
        import config.features as features

        monkeypatch.setattr(features, "is_feature_enabled", lambda _f: True)

    @staticmethod
    def _with_pool(monkeypatch, call_tool):
        import sys
        import types

        # Load the real package BEFORE shadowing one of its submodules:
        # ``core.mcp_clients.__init__`` re-exports ``MCPClientPool`` out of
        # ``client_pool``, so with the stub already in sys.modules the router's
        # later ``from core.mcp_clients.result_text import ...`` would import the
        # parent for the first time and die on the missing name. That made these
        # cases pass or fail on whether some earlier test happened to have loaded
        # the package — an ordering dependency, not a behaviour.
        import core.mcp_clients  # noqa: F401

        mod = types.ModuleType("core.mcp_clients.client_pool")
        mod.get_pool = lambda: types.SimpleNamespace(call_tool=call_tool)
        monkeypatch.setitem(sys.modules, "core.mcp_clients.client_pool", mod)

    def test_returns_the_code_and_url_the_sibling_issued(self, client, monkeypatch):
        seen = {}

        async def _call_tool(sibling, tool, args):
            seen["sibling"], seen["tool"], seen["args"] = sibling, tool, args
            return _tool_result(_LIVE_LOGIN_REPLY)

        self._with_pool(monkeypatch, _call_tool)

        body = client.post("/connectors/outlook/auth/start").json()
        assert seen == {"sibling": "ms365", "tool": "login", "args": {}}
        assert body["auth_kind"] == "msal_device_code"
        assert body["device_code"] == "VDBF68NR"
        assert body["verification_uri"] == "https://www.microsoft.com/link"
        # The operator still has to be told where to put the code.
        assert "VDBF68NR" in body["instructions"]
        assert "https://www.microsoft.com/link" in body["instructions"]

    def test_does_not_invent_an_expiry_the_sibling_never_sent(
        self, client, monkeypatch,
    ):
        """``expires_in`` is declared but the login reply carries no expiry.

        Filling it with MSAL's default would expire the code in the UI at a time
        the sibling never agreed to — a number we made up reads identically to
        one it reported.
        """
        async def _call_tool(*_a):
            return _tool_result(_LIVE_LOGIN_REPLY)

        self._with_pool(monkeypatch, _call_tool)
        assert client.post("/connectors/outlook/auth/start").json()["expires_in"] is None

    def test_url_does_not_swallow_the_rest_of_the_sentence(self, client, monkeypatch):
        """The URL is mid-sentence; a greedy read hands over a dead link.

        The Google branch already shipped a consent URL ending
        "prompt=select_account\\nMarkdown" for exactly this reason.
        """
        async def _call_tool(*_a):
            return _tool_result(_LIVE_LOGIN_REPLY)

        self._with_pool(monkeypatch, _call_tool)
        uri = client.post("/connectors/outlook/auth/start").json()["verification_uri"]
        assert uri.endswith("/link")
        assert " " not in uri and "\\n" not in uri and "enter" not in uri

    def test_a_url_at_a_line_break_is_not_glued_to_the_next_word(
        self, client, monkeypatch,
    ):
        r"""The reply is JSON, so a newline inside ``message`` is the two
        characters \ and n until the envelope is decoded — and the URL regex
        matches \S+. Reading the raw envelope is what shipped a Google consent
        link ending "prompt=select_account\nMarkdown". Decode, then parse.
        """
        async def _call_tool(*_a):
            return _tool_result(
                '{"error":"device_code_required","message":"Open '
                'https://www.microsoft.com/link\\nand enter the code ZZQ12345."}'
            )

        self._with_pool(monkeypatch, _call_tool)
        body = client.post("/connectors/outlook/auth/start").json()
        assert body["verification_uri"] == "https://www.microsoft.com/link"
        assert body["device_code"] == "ZZQ12345"

    def test_an_error_flagged_reply_is_not_mined_for_a_code(self, client, monkeypatch):
        """MCP reports tool failure as a RESULT, not an exception.

        ``except`` never fires for a rejected bearer or an unknown tool name, so
        the isError flag is the only signal there is — and this payload is the
        reason the check cannot be skipped as redundant: it is well-formed
        device-code prose, so a parser that ignores the flag extracts a perfectly
        plausible code out of a reply the server said had failed. Today's sibling
        sets isError False here, but its payload's top-level key is already
        ``"error"``, so a version that flags it is one upstream line away. Not
        trusting a flagged reply costs a fallback; trusting one hands the
        operator a code that will not work and no way to tell why.
        """
        async def _call_tool(*_a):
            return _tool_result(_LIVE_LOGIN_REPLY, is_error=True)

        self._with_pool(monkeypatch, _call_tool)

        body = client.post("/connectors/outlook/auth/start").json()
        assert body["device_code"] is None
        assert body["verification_uri"] is None
        assert "node dist/index.js --login" in body["instructions"]
        assert "--profile pro" in body["instructions"]

    def test_unparseable_reply_falls_back_and_shows_what_came_back(
        self, client, monkeypatch,
    ):
        async def _call_tool(*_a):
            return _tool_result("Already signed in as someone@example.com.")

        self._with_pool(monkeypatch, _call_tool)

        body = client.post("/connectors/outlook/auth/start").json()
        assert body["device_code"] is None
        assert "Already signed in" in body["instructions"]
        assert "node dist/index.js --login" in body["instructions"]

    def test_unreachable_sibling_falls_back_rather_than_returning_nothing(
        self, client, monkeypatch,
    ):
        """An operator whose stack is down is the one who most needs the
        manual path — an empty response strands them."""
        async def _boom(*_a):
            raise ConnectionError("connection refused")

        self._with_pool(monkeypatch, _boom)

        body = client.post("/connectors/outlook/auth/start").json()
        assert body["device_code"] is None
        assert "connection refused" in body["instructions"]
        assert "node dist/index.js --login" in body["instructions"]
        assert "-f docker-compose.yml" in body["instructions"]

    def test_the_fallback_does_not_name_a_url_the_sibling_contradicts(
        self, client, monkeypatch,
    ):
        """The manual string said "microsoft.com/devicelogin"; the sibling
        issues codes against microsoft.com/link. Point at the URL the login
        actually prints instead of a second, hardcoded one."""
        async def _boom(*_a):
            raise ConnectionError("down")

        self._with_pool(monkeypatch, _boom)
        instructions = client.post("/connectors/outlook/auth/start").json()["instructions"]
        assert "devicelogin" not in instructions
        assert "the URL it prints" in instructions

    def test_outlook_calendar_uses_the_same_flow(self, client, monkeypatch):
        """Both ms365 connectors share auth_kind, and a per-slug regression
        would leave one of the two paid connectors on the dead path."""
        async def _call_tool(*_a):
            return _tool_result(_LIVE_LOGIN_REPLY)

        self._with_pool(monkeypatch, _call_tool)
        body = client.post("/connectors/outlook_calendar/auth/start").json()
        assert body["device_code"] == "VDBF68NR"


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


class TestStartAuthIsGated:
    """Starting an authorization is a GRANT, and it was ungated.

    `feature_enabled` was computed for /connectors and only ever reported, so a
    community install could walk a real Google consent screen and have a
    refresh token written into the sibling's volume. Ingest would then refuse
    to use it — value withheld, credential handed over anyway.
    """

    def test_community_tier_cannot_start_an_authorization(self, client, monkeypatch):
        import config.features as features

        monkeypatch.setattr(features, "is_feature_enabled", lambda _f: False)
        res = client.post("/connectors/gmail/auth/start")
        assert res.status_code == 403
        assert "Pro" in res.json()["detail"]

    def test_community_tier_cannot_start_a_microsoft_device_code(
        self, client, monkeypatch,
    ):
        """The gate has to survive the branch becoming a real sibling call.

        A device code is a grant in progress: complete it and a token lands in
        the ms365 container's volume for an install whose ingest will refuse to
        use it. The sibling must not be asked for one at all.
        """
        import sys
        import types

        import config.features as features
        import core.mcp_clients  # noqa: F401 — see TestMicrosoftDeviceCodeStart._with_pool

        monkeypatch.setattr(features, "is_feature_enabled", lambda _f: False)

        calls = []

        async def _call_tool(*args):
            calls.append(args)
            raise AssertionError("sibling contacted despite the Pro gate")

        mod = types.ModuleType("core.mcp_clients.client_pool")
        mod.get_pool = lambda: types.SimpleNamespace(call_tool=_call_tool)
        monkeypatch.setitem(sys.modules, "core.mcp_clients.client_pool", mod)

        res = client.post("/connectors/outlook/auth/start")
        assert res.status_code == 403
        assert "Pro" in res.json()["detail"]
        assert calls == []

    def test_disconnect_is_NOT_gated(self, client, monkeypatch):
        """Deliberate asymmetry. Gating revocation would trap a user with
        access they want to remove — a gate that blocks the safety action is
        worse than no gate."""
        import config.features as features

        monkeypatch.setattr(features, "is_feature_enabled", lambda _f: False)
        assert client.post("/connectors/gmail/disconnect").status_code == 200

    def test_auth_status_is_NOT_gated(self, client, monkeypatch):
        """Read-only, and the UI needs it to render the locked state."""
        import config.features as features

        monkeypatch.setattr(features, "is_feature_enabled", lambda _f: False)
        assert client.get("/connectors/gmail/auth/status").status_code == 200


class TestBridgeOnlyConnectorsAreStillGuarded:
    """`apple_notes` has no backend plugin — correctly.

    The desktop app implements it end to end; adding a backend plugin would be
    a rival implementation of one feature, the mistake the `spotlight_donor`
    deletion already corrected. But the reachability invariant above iterates
    plugin MANIFESTS, so a bridge-only connector is invisible to it: dropping
    `{ kind: "notes" }` from APPLE_BRIDGE_KINDS would remove a paid connector
    from the UI with every gate green.

    These anchor on `config.features.NON_PLUGIN_IMPLEMENTATIONS`, which
    declares independently where a flag with no plugin actually lives.
    """

    def _bridge_block(self) -> str:
        rows = (REPO_ROOT / "src/web/src/components/sources/source-rows.ts").read_text()
        return rows.split("APPLE_BRIDGE_KINDS", 1)[-1].split("]", 1)[0]

    def test_every_desktop_declared_connector_flag_reaches_a_surface(self):
        import config.features as features

        # `spotlight_donation` is a Settings → Extensions surface, not a source
        # row, so it is excluded here; the Pro-gating lint covers it via its
        # renderer gate.
        bridge_flags = {
            flag for flag, where in features.NON_PLUGIN_IMPLEMENTATIONS.items()
            if where == "desktop" and flag != "spotlight_donation"
        }
        assert bridge_flags, "no desktop-declared connector flags — anchor is empty"

        block = self._bridge_block()
        missing = []
        for flag in sorted(bridge_flags):
            kind = flag.removeprefix("apple_").removesuffix("_reader")
            if f'kind: "{kind}"' not in block:
                missing.append((flag, kind))
        assert not missing, (
            "declared desktop-implemented but absent from APPLE_BRIDGE_KINDS, "
            f"so nothing renders them: {missing}"
        )

    def test_a_bridge_kind_is_never_also_a_rest_connector(self):
        """Sources → Connectors concatenates both feeds with no dedup, so a
        kind in both renders twice — once working, once reporting a Swift
        helper the container cannot see."""
        from app.routers.connectors import _CONNECTORS

        block = self._bridge_block()
        for slug in _CONNECTORS:
            kind = slug.removeprefix("apple_")
            assert f'kind: "{kind}"' not in block, (
                f"{slug} is a REST connector AND a bridge row — it would render twice"
            )
