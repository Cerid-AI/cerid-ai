# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""I4 — Private Mode levels toggle correctly.

Preservation invariant: the Private Mode surface (GET/POST/DELETE
/settings/private-mode) must round-trip every valid level (0-3) and
reject invalid input with 422. Settings consolidation in later
sprints must not silently change the level validator range.

Note: the plan doc and early docs say "4 security levels 1-4"; the
actual Pydantic validator is ``ge=0, le=3`` — 4 values (0, 1, 2, 3)
where 0 is disabled. This test codifies the real contract so doc
drift can never produce a 422 on Level 1 in production.
"""
from __future__ import annotations


def _reset(http_client) -> None:
    """Teardown helper — always leave the system in level 0."""
    http_client.delete("/settings/private-mode")


def test_private_mode_get_returns_level_field(http_client):
    try:
        r = http_client.get("/settings/private-mode")
        assert r.status_code == 200, f"HTTP {r.status_code}: {r.text[:200]}"
        body = r.json()
        assert "level" in body, f"/settings/private-mode missing 'level': {body}"
        assert isinstance(body["level"], int), (
            f"level must be int, got {type(body['level']).__name__}"
        )
    finally:
        _reset(http_client)


def test_private_mode_round_trips_each_level(http_client):
    try:
        for lvl in (0, 1, 2, 3):
            r = http_client.post("/settings/private-mode", json={"level": lvl})
            assert r.status_code == 200, (
                f"POST level={lvl} HTTP {r.status_code}: {r.text[:200]}"
            )
            assert r.json()["level"] == lvl, (
                f"POST level={lvl} returned {r.json()}"
            )
            got = http_client.get("/settings/private-mode").json()["level"]
            assert got == lvl, (
                f"GET after POST level={lvl} returned level={got}"
            )
    finally:
        _reset(http_client)


def test_private_mode_rejects_out_of_range_levels(http_client):
    try:
        for bad in (-1, 4, 99):
            r = http_client.post("/settings/private-mode", json={"level": bad})
            assert r.status_code in (400, 422), (
                f"POST level={bad} should return 422; got HTTP {r.status_code}"
            )
    finally:
        _reset(http_client)


def test_private_mode_delete_resets_to_zero(http_client):
    try:
        http_client.post("/settings/private-mode", json={"level": 3})
        assert http_client.get("/settings/private-mode").json()["level"] == 3
        r = http_client.delete("/settings/private-mode")
        assert r.status_code == 200, f"DELETE HTTP {r.status_code}"
        assert r.json()["level"] == 0
        assert http_client.get("/settings/private-mode").json()["level"] == 0
    finally:
        _reset(http_client)
