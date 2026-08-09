# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for the routing-tiers overlay read path (core/routing/smart_router) and
the job-side tier resolution (app/routers/models)."""
from __future__ import annotations

import json

import pytest

import core.routing.smart_router as sr


@pytest.fixture(autouse=True)
def _reset_overlay_cache():
    """Each test starts with a cold overlay cache."""
    sr._tier_overlay = {}
    sr._tier_overlay_mtime = -1.0
    yield
    sr._tier_overlay = {}
    sr._tier_overlay_mtime = -1.0


# ---------------------------------------------------------------------------
# smart_router overlay read path
# ---------------------------------------------------------------------------


class TestResolveTierIdReadPath:
    def test_identity_when_no_overlay_file(self, tmp_path, monkeypatch):
        # Path points at a non-existent file → every id resolves to itself.
        missing = tmp_path / "routing_tiers.json"
        monkeypatch.setattr(sr.config, "ROUTING_TIERS_OVERLAY_PATH", str(missing))
        assert sr._resolve_tier_id("openrouter/x-ai/grok-4.3:online") == "openrouter/x-ai/grok-4.3:online"
        assert sr._load_tier_overlay() == {}

    def test_resolves_through_present_overlay(self, tmp_path, monkeypatch):
        overlay = {
            "openrouter/anthropic/claude-sonnet-4.6": "openrouter/anthropic/claude-sonnet-4.7",
            "openrouter/x-ai/grok-4.3:online": "openrouter/x-ai/grok-4.4:online",
        }
        path = tmp_path / "routing_tiers.json"
        path.write_text(json.dumps(overlay))
        monkeypatch.setattr(sr.config, "ROUTING_TIERS_OVERLAY_PATH", str(path))

        assert (
            sr._resolve_tier_id("openrouter/anthropic/claude-sonnet-4.6")
            == "openrouter/anthropic/claude-sonnet-4.7"
        )
        # Id not in the overlay → identity.
        assert (
            sr._resolve_tier_id("openrouter/openai/gpt-4o")
            == "openrouter/openai/gpt-4o"
        )

    def test_corrupt_overlay_falls_back_to_identity(self, tmp_path, monkeypatch):
        path = tmp_path / "routing_tiers.json"
        path.write_text("{ this is not valid json ")
        monkeypatch.setattr(sr.config, "ROUTING_TIERS_OVERLAY_PATH", str(path))

        assert sr._load_tier_overlay() == {}
        assert (
            sr._resolve_tier_id("openrouter/anthropic/claude-sonnet-4.6")
            == "openrouter/anthropic/claude-sonnet-4.6"
        )

    def test_non_dict_overlay_falls_back_to_identity(self, tmp_path, monkeypatch):
        path = tmp_path / "routing_tiers.json"
        path.write_text(json.dumps(["not", "a", "map"]))
        monkeypatch.setattr(sr.config, "ROUTING_TIERS_OVERLAY_PATH", str(path))
        assert sr._load_tier_overlay() == {}

    def test_non_string_values_are_dropped(self, tmp_path, monkeypatch):
        path = tmp_path / "routing_tiers.json"
        path.write_text(json.dumps({"a": "b", "c": 5, "d": None}))
        monkeypatch.setattr(sr.config, "ROUTING_TIERS_OVERLAY_PATH", str(path))
        assert sr._load_tier_overlay() == {"a": "b"}

    def test_cache_reloads_on_mtime_change(self, tmp_path, monkeypatch):
        path = tmp_path / "routing_tiers.json"
        path.write_text(json.dumps({"x": "y1"}))
        monkeypatch.setattr(sr.config, "ROUTING_TIERS_OVERLAY_PATH", str(path))
        assert sr._resolve_tier_id("x") == "y1"

        # Rewrite with a newer mtime → cache invalidates and reloads.
        import os

        path.write_text(json.dumps({"x": "y2"}))
        os.utime(str(path), (sr._tier_overlay_mtime + 10, sr._tier_overlay_mtime + 10))
        assert sr._resolve_tier_id("x") == "y2"

    def test_route_uses_overlay_for_research_tier(self, tmp_path, monkeypatch):
        """End-to-end: the registry export reflects the overlay-resolved id."""
        src = sr.tier_source_ids()
        # Pick the research-tier source id and map it forward.
        research_src = str(sr.RESEARCH_MODELS["grok-online"]["id"])
        assert research_src in src
        path = tmp_path / "routing_tiers.json"
        path.write_text(json.dumps({research_src: "openrouter/x-ai/grok-9.9:online"}))
        monkeypatch.setattr(sr.config, "ROUTING_TIERS_OVERLAY_PATH", str(path))

        assert sr.get_model_registry()["research"]["grok-online"] == "openrouter/x-ai/grok-9.9:online"


# ---------------------------------------------------------------------------
# Job-side tier resolution (app/routers/models)
# ---------------------------------------------------------------------------


class TestRefreshRoutingTiersOverlay:
    def test_writes_only_changed_ids(self, tmp_path, monkeypatch):
        from app.routers import models as models_mod

        out = tmp_path / "routing_tiers.json"
        monkeypatch.setattr(models_mod, "_ROUTING_TIERS_OVERLAY_PATH", out)

        # Catalog upgrades sonnet 4.6→4.7 and grok-4.3:online→4.4:online; the
        # rest of the tier ids have no in-family successor here, so they stay
        # out of the overlay (identity).
        catalog = [
            "anthropic/claude-sonnet-4.7",
            "x-ai/grok-4.4:online",
        ]
        diff = models_mod._refresh_routing_tiers_overlay(catalog)

        persisted = json.loads(out.read_text())
        assert persisted == {
            "openrouter/anthropic/claude-sonnet-4.6": "openrouter/anthropic/claude-sonnet-4.7",
            "openrouter/x-ai/grok-4.3:online": "openrouter/x-ai/grok-4.4:online",
        }
        changed = {row["id"] for row in diff}
        assert changed == set(persisted.keys())
        for row in diff:
            assert row["to"] == persisted[row["id"]]

    def test_empty_catalog_writes_nothing(self, tmp_path, monkeypatch):
        from app.routers import models as models_mod

        out = tmp_path / "routing_tiers.json"
        monkeypatch.setattr(models_mod, "_ROUTING_TIERS_OVERLAY_PATH", out)
        assert models_mod._refresh_routing_tiers_overlay([]) == []
        assert not out.exists()

    def test_no_upgrades_writes_empty_map(self, tmp_path, monkeypatch):
        from app.routers import models as models_mod

        out = tmp_path / "routing_tiers.json"
        monkeypatch.setattr(models_mod, "_ROUTING_TIERS_OVERLAY_PATH", out)

        # A catalog with no same-family successors → overlay is written but empty.
        diff = models_mod._refresh_routing_tiers_overlay(["some/unrelated-model-1.0"])
        assert diff == []
        assert json.loads(out.read_text()) == {}

    def test_does_not_cross_series(self, tmp_path, monkeypatch):
        from app.routers import models as models_mod

        out = tmp_path / "routing_tiers.json"
        monkeypatch.setattr(models_mod, "_ROUTING_TIERS_OVERLAY_PATH", out)

        # gpt-5-nano is dotted-less in the relevant token? It carries no dotted
        # version → must stay pinned even if a "gpt-6-nano" exists.
        catalog = ["openai/gpt-6-nano"]
        models_mod._refresh_routing_tiers_overlay(catalog)
        persisted = json.loads(out.read_text())
        assert "openrouter/openai/gpt-5-nano" not in persisted

    def test_overlay_roundtrips_into_router(self, tmp_path, monkeypatch):
        """Job writes the overlay; router reads it back through the same path."""
        from app.routers import models as models_mod

        out = tmp_path / "routing_tiers.json"
        monkeypatch.setattr(models_mod, "_ROUTING_TIERS_OVERLAY_PATH", out)
        monkeypatch.setattr(sr.config, "ROUTING_TIERS_OVERLAY_PATH", str(out))

        models_mod._refresh_routing_tiers_overlay(["anthropic/claude-sonnet-4.7"])
        assert (
            sr._resolve_tier_id("openrouter/anthropic/claude-sonnet-4.6")
            == "openrouter/anthropic/claude-sonnet-4.7"
        )


class TestApplyLatestAssignmentsTierWiring:
    @pytest.mark.asyncio
    async def test_tier_refresh_runs_when_role_diff_empty(self, tmp_path, monkeypatch):
        """An empty role diff must NOT skip the tier-overlay refresh — the tier
        tables upgrade on their own cadence."""
        from app.routers import models as models_mod

        out = tmp_path / "routing_tiers.json"
        monkeypatch.setattr(models_mod, "_ROUTING_TIERS_OVERLAY_PATH", out)

        async def _fake_compute():
            return {
                "updates": [],  # no role changes
                "resolved": {},
                "catalog_size": 2,
                "catalog_ids": ["anthropic/claude-sonnet-4.7", "x-ai/grok-4.4:online"],
            }

        monkeypatch.setattr(models_mod, "_compute_model_updates", _fake_compute)

        result = await models_mod.apply_latest_assignments()
        assert result["applied"] == []
        assert result["restart_required"] is False
        # The tier overlay was still written + reported.
        assert out.exists()
        tier_ids = {row["id"] for row in result["tier_updates"]}
        assert "openrouter/anthropic/claude-sonnet-4.6" in tier_ids
        assert "openrouter/x-ai/grok-4.3:online" in tier_ids
