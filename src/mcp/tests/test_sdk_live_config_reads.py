# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""The SDK discovery routes must read config at request time (F023/F339/F340).

``sdk.py`` bound ``FEATURE_TIER`` and ``DOMAINS`` with ``from config...
import`` at module scope. Both are *rebound* at runtime — ``set_tier()``
assigns the module global, and the taxonomy writers reassign ``DOMAINS`` from
``TAXONOMY.keys()`` — so a ``from`` import freezes a boot-time copy that no
later write can reach. Live, that served ``tier: pro`` from a process whose
own ``/settings`` said ``enterprise``, and 12 of 23 domains.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from app.routers import sdk

    app = FastAPI()
    app.include_router(sdk.router)
    return TestClient(app)


def test_settings_reports_the_tier_the_process_is_serving(client):
    import config.features as features_mod

    original = features_mod.current_tier()
    try:
        features_mod.set_tier("enterprise")
        body = client.get("/sdk/v1/settings").json()
        assert body["tier"] == features_mod.current_tier() == "enterprise", body

        features_mod.set_tier("community")
        body = client.get("/sdk/v1/settings").json()
        assert body["tier"] == "community", body
    finally:
        features_mod.set_tier(original)


def test_settings_features_follow_the_tier(client):
    """The flags dict is mutated in place, so it already followed — pin it."""
    import config.features as features_mod

    original = features_mod.current_tier()
    try:
        features_mod.set_tier("enterprise")
        body = client.get("/sdk/v1/settings").json()
        assert body["features"]["multi_user"] is True
        assert body["tier"] == "enterprise", (
            "payload is internally contradictory: enterprise-only flags on a "
            f"tier of {body['tier']!r}"
        )
    finally:
        features_mod.set_tier(original)


def test_taxonomy_publishes_every_live_domain(client):
    import config.taxonomy as taxonomy_mod

    taxonomy_mod.TAXONOMY["w2l2_probe_domain"] = {
        "description": "added after import, as domain rehydration does",
        "sub_categories": [],
    }
    try:
        body = client.get("/sdk/v1/taxonomy").json()
        assert set(body["domains"]) == set(body["taxonomy"]), (
            "domains and taxonomy disagree: "
            f"{sorted(set(body['taxonomy']) - set(body['domains']))} missing"
        )
        assert "w2l2_probe_domain" in body["domains"]
    finally:
        taxonomy_mod.TAXONOMY.pop("w2l2_probe_domain", None)
