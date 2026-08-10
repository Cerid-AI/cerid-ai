# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""POST /sources refuses Pro kinds at community tier.

Found 2026-08-10. `KIND_TIER`'s comment claimed the connector instantiation
path enforced the tier "via app.config.features.is_feature_enabled" — that
module does not exist, and the route had no feature check anywhere. The
desktop Add-Source wizard posts here directly, bypassing the renderer-side Pro
gate that covers the connector rows, so a community install could create and
sync `apple_mail` and `apple_reminders` sources.

The gate belongs on the route, not in the renderer: the renderer decides what
a user is *offered*, the route decides what the server will *do*.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.ingest.sources.kinds import KIND_TIER


@pytest.fixture()
def client():
    from app.routers.sources import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def _post(client, kind: str):
    return client.post(
        "/sources",
        json={"kind": kind, "display_name": f"test-{kind}", "config": {}},
    )


class TestProKindsAreRefusedAtCommunityTier:
    @pytest.mark.parametrize("kind", ["apple_mail", "apple_reminders", "gmail"])
    def test_pro_kind_is_403(self, client, kind, monkeypatch):
        monkeypatch.setattr("app.routers.sources.is_tier_met", lambda _t: False)
        res = _post(client, kind)
        assert res.status_code == 403
        assert "Pro" in res.json()["detail"]

    def test_the_refusal_happens_before_any_connector_is_built(
        self, client, monkeypatch,
    ):
        """A 403 that still instantiated the connector would have already
        touched the user's mailbox — the point is to refuse first."""
        monkeypatch.setattr("app.routers.sources.is_tier_met", lambda _t: False)
        called: list[str] = []
        monkeypatch.setattr(
            "app.routers.sources.get_connector",
            lambda k: called.append(k),
        )
        assert _post(client, "apple_mail").status_code == 403
        assert called == []

    def test_a_core_kind_is_not_blocked_by_the_gate(self, client, monkeypatch):
        """Guard against over-reach: the gate must not touch core kinds.

        `rss` is core, so it must get past the tier check. It then fails on
        config/store grounds, which is fine — anything other than 403 proves
        the Pro gate did not fire.
        """
        monkeypatch.setattr("app.routers.sources.is_tier_met", lambda _t: False)
        assert KIND_TIER["rss"] == "core"
        assert _post(client, "rss").status_code != 403

    def test_pro_kind_passes_the_gate_when_entitled(self, client, monkeypatch):
        monkeypatch.setattr("app.routers.sources.is_tier_met", lambda _t: True)
        assert _post(client, "apple_mail").status_code != 403


def test_every_pro_kind_in_the_table_is_covered_by_the_gate():
    """The gate reads KIND_TIER, so it cannot drift from the table — but pin
    that the table still marks the desktop Apple kinds Pro. Flipping one to
    "core" would silently un-gate it."""
    for kind in ("apple_mail", "apple_reminders", "apple_notes", "imessage"):
        assert KIND_TIER[kind] == "pro"
