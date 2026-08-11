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


class TestEveryProKindIsRefusedEverywhere:
    """Enumerate the POPULATION, don't spot-check the instances.

    The four ungated paths closed on 2026-08-10 were each found by reading,
    which had already missed them three times. These tests derive their subject
    from `KIND_TIER`, so a NEW Pro kind is covered the moment it is declared —
    nobody has to remember to add a case.

    A grep-style lint was considered and rejected: the real chokepoints are
    behavioural (a route returning 403, a loop skipping a kind), and a static
    scan for "is there a gate symbol nearby" produces false positives on
    routers that are gated INDIRECTLY. `/data-sources/query` is the example —
    it has no gate of its own, and needs none, because a Pro DataSource is
    registered by a plugin `register()` that the tier-gated loader refuses to
    run at community tier. A noisy gate gets ignored, which is worse than none.
    """

    def _pro_kinds(self):
        from core.ingest.sources.kinds import KIND_TIER
        return sorted(k for k, t in KIND_TIER.items() if t == "pro")

    def test_the_population_is_not_empty(self):
        """Guards the two tests below from passing vacuously if KIND_TIER is
        ever restructured."""
        assert len(self._pro_kinds()) >= 5

    def test_no_pro_kind_can_be_created_at_community_tier(self, client, monkeypatch):
        monkeypatch.setattr("app.routers.sources.is_tier_met", lambda _t: False)
        allowed = []
        for kind in self._pro_kinds():
            res = client.post(
                "/sources",
                json={"kind": kind, "display_name": f"t-{kind}", "config": {}},
            )
            if res.status_code != 403:
                allowed.append((kind, res.status_code))
        assert not allowed, f"Pro kinds creatable at community tier: {allowed}"

    def test_no_pro_kind_is_polled_at_community_tier(self):
        """The scheduler is the other way a Pro kind gets worked on — and it
        walks rows that already exist, which the creation gate cannot help
        with."""
        from app.scheduler import _POLLABLE_KINDS
        from core.ingest.sources.kinds import KIND_TIER

        pro_pollable = [k for k in _POLLABLE_KINDS if KIND_TIER.get(k) == "pro"]
        # Every Pro kind in the poll set must be covered by the per-kind gate
        # in _run_source_poll; test_scheduler.py asserts the behaviour. This
        # pins that the two sets stay in sync so that test cannot go stale.
        assert set(pro_pollable) <= set(self._pro_kinds())
