# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for the availability capability flag on source kinds.

The wizard gates kinds that have no working ingestion path; availability is
derived from the connector registry + OAuth set + the webhook special-case.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.routers.connectors import oauth_connector_kinds
from app.routers.sources import (
    CreateSourceRequest,
    HealthProbeResult,
    _kind_availability,
    _kind_providers,
    _kind_requires_desktop,
    create_source,
)


def test_registered_connector_is_available():
    # rss has a registered SourceConnector
    assert _kind_availability("rss", oauth_connector_kinds()) == "available"
    assert _kind_availability("url_watch", oauth_connector_kinds()) == "available"


def test_webhook_is_available_without_connector():
    assert _kind_availability("webhook", oauth_connector_kinds()) == "available"


def test_oauth_kind_is_oauth():
    oauth = oauth_connector_kinds()
    assert "gmail" in oauth  # guards the fixture
    assert _kind_availability("gmail", oauth) == "oauth"


def test_folder_kind_is_available():
    # folder is bridge-backed via the watched-folders store; no connector needed
    assert _kind_availability("folder", oauth_connector_kinds()) == "available"


def test_unimplemented_kind_is_coming_soon():
    # voice_note has no connector and no OAuth path — use it as the coming_soon example
    assert _kind_availability("voice_note", oauth_connector_kinds()) == "coming_soon"


def test_every_source_kind_classified():
    from core.ingest.sources.kinds import SOURCE_KINDS

    oauth = oauth_connector_kinds()
    valid = {"available", "oauth", "coming_soon", "requires_desktop"}
    # Pin the clipboard heartbeat probe so this stays hermetic (no Redis).
    with patch(
        "app.routers.sources._check_clipboard_daemon",
        return_value=HealthProbeResult(ok=True, detail="heartbeat 1s ago"),
    ):
        for k in SOURCE_KINDS:
            assert _kind_availability(k, oauth) in valid


# --- desktop-helper-backed kinds (apple_mail / clipboard) ---


def test_helper_kinds_flagged_requires_desktop():
    assert _kind_requires_desktop("apple_mail") is True
    assert _kind_requires_desktop("clipboard") is True
    # apple_reminders has NO backend connector since 2026-08-12 — it is a
    # desktop-APP kind (ingestion in packages/desktop/src/main/connectors/
    # apple_reminders.ts), so the connector-derived probe must say False.
    assert _kind_requires_desktop("apple_reminders") is False
    assert _kind_requires_desktop("rss") is False
    assert _kind_requires_desktop("folder") is False


def test_helper_kind_requires_desktop_when_helper_missing(monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda *a, **k: None)
    oauth = oauth_connector_kinds()
    assert _kind_availability("apple_mail", oauth) == "requires_desktop"


def test_helper_kind_available_when_helper_present(monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda *a, **k: "/usr/local/bin/helper")
    oauth = oauth_connector_kinds()
    assert _kind_availability("apple_mail", oauth) == "available"


def test_clipboard_requires_desktop_when_daemon_heartbeat_absent():
    with patch(
        "app.routers.sources._check_clipboard_daemon",
        return_value=HealthProbeResult(ok=False, detail="daemon heartbeat absent"),
    ):
        assert _kind_availability("clipboard", oauth_connector_kinds()) == "requires_desktop"


def test_clipboard_available_when_daemon_heartbeat_fresh():
    with patch(
        "app.routers.sources._check_clipboard_daemon",
        return_value=HealthProbeResult(ok=True, detail="heartbeat 2s ago"),
    ):
        assert _kind_availability("clipboard", oauth_connector_kinds()) == "available"


def _kinds_client() -> TestClient:
    from app.routers.sources import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_kinds_endpoint_reports_requires_desktop_and_allowed_roots(monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda *a, **k: None)
    with patch(
        "app.routers.sources._check_clipboard_daemon",
        return_value=HealthProbeResult(ok=False, detail="daemon heartbeat absent"),
    ):
        r = _kinds_client().get("/sources/kinds")
    assert r.status_code == 200
    by_kind = {k["kind"]: k for k in r.json()}

    for kind in ("apple_mail", "clipboard"):
        assert by_kind[kind]["requires_desktop"] is True
        assert by_kind[kind]["availability"] == "requires_desktop"

    # Desktop-app kind: no backend connector (requires_desktop derives from
    # the connector, so it is False) but the availability still routes the
    # wizard to the desktop app rather than falling through to coming_soon.
    assert by_kind["apple_reminders"]["requires_desktop"] is False
    assert by_kind["apple_reminders"]["availability"] == "requires_desktop"

    assert by_kind["rss"]["requires_desktop"] is False
    assert by_kind["rss"]["availability"] == "available"
    assert by_kind["rss"]["allowed_roots"] == []

    # folder metadata carries the container-side allowed roots for the wizard.
    assert by_kind["folder"]["allowed_roots"]
    assert by_kind["folder"]["availability"] == "available"


# --- desktop-app-backed kinds (no backend SourceConnector; ingestion lives
# entirely in packages/desktop/src/main/connectors/*.ts) ---


def test_apple_calendar_photos_reminders_flagged_requires_desktop():
    # Shipped via the desktop app's own connectors, not a backend SourceConnector,
    # so they must resolve to requires_desktop rather than falling through to
    # coming_soon on a build that ships them.
    oauth = oauth_connector_kinds()
    assert _kind_availability("apple_calendar", oauth) == "requires_desktop"
    assert _kind_availability("apple_photos", oauth) == "requires_desktop"
    assert _kind_availability("apple_reminders", oauth) == "requires_desktop"


# --- webhook-backed kinds (chat_capture / dev_events) ---


def test_webhook_backed_kinds_are_available():
    oauth = oauth_connector_kinds()
    assert _kind_availability("chat_capture", oauth) == "available"
    assert _kind_availability("dev_events", oauth) == "available"


def test_kind_providers_surfaced_for_webhook_backed_kinds():
    assert _kind_providers("chat_capture") == ["discord", "matrix", "slack", "teams"]
    assert _kind_providers("dev_events") == ["github", "linear", "sentry", "stripe"]
    # The generic webhook kind is raw pass-through (no recipes); pull/file kinds
    # have no recipes either.
    assert _kind_providers("webhook") == []
    assert _kind_providers("rss") == []


def _echo_created(_driver, **kw):
    """Mirror srcdb.create_source(...) return shape from its kwargs."""
    return {
        "id": "src_1",
        "kind": kw["kind"],
        "family": kw["family"],
        "display_name": kw["display_name"],
        "tier": kw["tier"],
        "status": "connected",
        "config": kw["config"],
        "sync_cursor": {},
    }


def _patch_create():
    return (
        patch("app.routers.sources.srcdb.create_source", side_effect=_echo_created),
        patch("app.routers.sources.get_neo4j", return_value=MagicMock()),
        patch("app.routers.sources.webhook_tokens.generate_token", return_value="tok_x"),
        patch(
            "app.routers.sources.webhook_tokens.generate_hmac_secret",
            return_value="sec_x",
        ),
    )


@pytest.mark.asyncio
async def test_create_chat_capture_mints_token_no_hmac():
    mock_create, p_neo, p_tok, p_sec = _patch_create()
    with mock_create as create_mock, p_neo, p_tok, p_sec:
        rec = await create_source(
            CreateSourceRequest(
                kind="chat_capture", display_name="Slack", config={"provider": "slack"},
            ),
        )
    cfg = create_mock.call_args.kwargs["config"]
    assert cfg["token"] == "tok_x"
    assert cfg["provider"] == "slack"
    assert "hmac_secret" not in cfg  # slack does not mandate a signature
    assert rec.kind == "chat_capture"


@pytest.mark.asyncio
async def test_create_dev_events_github_auto_mints_hmac():
    mock_create, p_neo, p_tok, p_sec = _patch_create()
    with mock_create as create_mock, p_neo, p_tok, p_sec:
        await create_source(
            CreateSourceRequest(
                kind="dev_events", display_name="GH", config={"provider": "github"},
            ),
        )
    # github sets requires_signature=True → receiver would reject without a secret.
    assert create_mock.call_args.kwargs["config"]["hmac_secret"] == "sec_x"  # pragma: allowlist secret


@pytest.mark.asyncio
async def test_create_chat_capture_requires_provider():
    _mc, p_neo, _pt, _ps = _patch_create()
    with p_neo, pytest.raises(HTTPException) as ei:
        await create_source(
            CreateSourceRequest(kind="chat_capture", display_name="x", config={}),
        )
    assert ei.value.status_code == 422


@pytest.mark.asyncio
async def test_create_rejects_unknown_provider():
    _mc, p_neo, _pt, _ps = _patch_create()
    with p_neo, pytest.raises(HTTPException) as ei:
        await create_source(
            CreateSourceRequest(
                kind="dev_events", display_name="x", config={"provider": "bogus"},
            ),
        )
    assert ei.value.status_code == 422


@pytest.mark.asyncio
async def test_create_generic_webhook_stays_provider_optional():
    mock_create, p_neo, p_tok, p_sec = _patch_create()
    with mock_create, p_neo, p_tok, p_sec:
        rec = await create_source(
            CreateSourceRequest(kind="webhook", display_name="hook", config={}),
        )
    assert rec.kind == "webhook"
