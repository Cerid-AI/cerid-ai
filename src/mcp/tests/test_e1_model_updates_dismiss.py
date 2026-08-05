# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""E1 Phase-5 — POST /models/updates/dismiss must actually persist (CR-075).

Registry: ``docs/superpowers/specs/2026-07-17-audit-e1-findings-registry.jsonl``
(CR-075). The endpoint returned ``{"dismissed": True, "id": ...}`` without
storing anything, so ``_compute_model_updates`` re-surfaced the same update on
the next poll. The fix persists the dismissal to a Redis set and filters it from
the notification surfaces (list/check). RED-then-GREEN.
"""
from __future__ import annotations

import pytest


class _FakeRedis:
    def __init__(self) -> None:
        self.store: set[str] = set()

    def sadd(self, _key, value):
        self.store.add(value)

    def smembers(self, _key):
        return set(self.store)


@pytest.mark.asyncio
async def test_dismiss_persists_the_id(monkeypatch):
    """RED on HEAD: dismiss is a no-op, so the id is never stored."""
    import app.routers.models as m

    fake = _FakeRedis()
    monkeypatch.setattr("app.deps.get_redis", lambda: fake)

    resp = await m.dismiss_model_update("classifier:meta-llama/llama-3.3-70b-instruct")

    assert resp["dismissed"] is True
    assert "classifier:meta-llama/llama-3.3-70b-instruct" in fake.store


@pytest.mark.asyncio
async def test_list_filters_dismissed_update(monkeypatch):
    """A dismissed update must not re-surface on /updates."""
    import app.routers.models as m

    fake = _FakeRedis()
    fake.store.add("classifier:new-model")
    monkeypatch.setattr("app.deps.get_redis", lambda: fake)

    both = [
        {"role": "classifier", "from": "old", "to": "new-model", "id": "classifier:new-model"},
        {"role": "chat", "from": "a", "to": "b", "id": "chat:b"},
    ]

    async def _fake_compute():
        return {
            "updates": list(both), "new": list(both), "deprecated": [],
            "last_checked": "t", "catalog_size": 1, "resolved": {}, "catalog_ids": [],
        }

    monkeypatch.setattr(m, "_compute_model_updates", _fake_compute)

    result = await m.list_model_updates()
    assert [u["id"] for u in result["updates"]] == ["chat:b"]
    assert [u["id"] for u in result["new"]] == ["chat:b"]


@pytest.mark.asyncio
async def test_compute_stamps_stable_ids(monkeypatch):
    """Each update carries a stable role:to id so a dismissal can pin it."""
    import app.routers.models as m
    import core.routing.model_catalog as mc

    monkeypatch.setattr(m, "_current_assignments", lambda: {"classifier": "old-model"})

    async def _catalog():
        return [{"id": "new-model"}]

    monkeypatch.setattr(m, "fetch_openrouter_catalog", _catalog)
    monkeypatch.setattr(m, "catalog_ids", lambda _c: ["new-model"])
    monkeypatch.setattr(mc, "resolve_assignments", lambda cur, ids, hardware_profile="": {"classifier": "new-model"})
    monkeypatch.setattr(m, "resolve_assignments", lambda cur, ids, hardware_profile="": {"classifier": "new-model"})

    result = await m._compute_model_updates()
    assert result["updates"] == [
        {"role": "classifier", "from": "old-model", "to": "new-model", "id": "classifier:new-model"}
    ]
