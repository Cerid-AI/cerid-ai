# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Preservation invariants for the paid ``apple_notes_reader`` feature.

Written 2026-08-10. Every Apple connector had a dedicated backend test —
``test_apple_mail_data_source.py``, ``test_apple_imessage_data_source.py``,
``test_apple_reminders_data_source.py``, ``test_apple_photos_data_source.py``,
``test_apple_calendar_data_source.py`` — except Notes, which had none. The
only place ``apple_notes`` appeared in the suite was as a bare string in a
tuple, a ``source_id`` literal, a docstring, and one live-stack preservation
test. Nothing asserted the flag's tier, its declared home, or how the health
surface reports it.

WHAT THIS MODULE COVERS (all of it backend, all of it runnable offline):

  * the flag exists, resolves to the Pro tier, and flips with the tier
  * it is declared ``"desktop"`` in ``config.features.NON_PLUGIN_IMPLEMENTATIONS``,
    the map added 2026-08-10 so a paid flag with no plugin can no longer hide
    in a residual bucket that ``degraded`` never read
  * ``/health.pro_features`` names that implementation rather than ``"unknown"``,
    and does not call the feature degraded merely for having no plugin
  * the ingest contract the desktop connector actually posts to is reachable,
    and ``apple_notes`` is Pro-gated at ``POST /sources``

WHAT THIS MODULE CANNOT COVER, AND DOES NOT PRETEND TO:

  The scan path itself. ``packages/desktop/src/main/connectors/apple_notes.ts``
  reads the user's live NoteStore through the Electron main process; exercising
  it needs a signed application bundle, a TCC Full-Disk-Access grant, and a real
  macOS login session. None of that exists in a Python test process or in CI, so
  the 402 lines of scan/parse logic in that file are UNVERIFIED here. Their tests
  belong to the desktop package's own suite. Nothing below should be read as
  evidence that Notes ingestion works end to end on a user's machine — only that
  the backend half of the contract holds.

A NOTE ON WHAT IS DELIBERATELY NOT ASSERTED:

  Not "no backend plugin exists for apple_notes". A rival backend implementation
  IS on disk at the repo-root ``plugins/apple-notes/`` — a NoteStore.sqlite
  reader gated on ``check_feature("apple_notes_reader")``. Asserting absence
  against the repo root would fail; asserting it against ``src/mcp/plugins/``
  alone would pass vacuously while the rival sat one directory up. The
  defensible invariant is about SUPPLY, not about files: no manifest in either
  plugin root may claim ``apple_notes_reader`` as a ``feature_flags`` entry,
  because the flag already has exactly one declared home and two homes for one
  flag means one of them is a lie.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

FLAG = "apple_notes_reader"
KIND = "apple_notes"

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_PLUGIN_ROOTS = (_REPO_ROOT / "src/mcp/plugins", _REPO_ROOT / "plugins")


@pytest.fixture(autouse=True)
def _restore_feature_tier():
    """Every test here mutates the process-global tier. Put it back."""
    import config.features as features

    original = features.FEATURE_TIER
    yield
    features.set_tier(original)


# --- Flag identity and tier behaviour ----------------------------------------


class TestTheFlagIsPaidAndFlipsWithTheTier:
    def test_the_flag_exists(self):
        import config.features as features

        assert FLAG in features.FEATURE_FLAGS

    def test_it_resolves_to_the_pro_tier(self):
        """``_get_feature_tier`` returns "pro" for unknown flags too, so pin
        membership in the Pro set as well — otherwise this passes for a flag
        that was never assigned a tier at all."""
        import config.features as features

        assert features._get_feature_tier(FLAG) == "pro"
        assert FLAG in features._PRO_TIER_FLAGS
        assert FLAG not in features._COMMUNITY_FLAGS

    def test_it_is_a_member_of_the_apple_connectors_bucket(self):
        import config.features as features

        assert FLAG in features.FEATURE_BUCKETS["pro_apple_connectors"]

    @pytest.mark.parametrize(
        ("tier", "enabled"),
        [("community", False), ("pro", True), ("enterprise", True)],
    )
    def test_the_flag_follows_the_tier(self, tier, enabled):
        import config.features as features

        features.set_tier(tier)
        assert features.FEATURE_FLAGS[FLAG] is enabled
        assert features.is_feature_enabled(FLAG) is enabled

    def test_check_feature_refuses_at_community_tier(self):
        """The gate any backend caller would reach for. A community install
        must not be able to read the user's Notes store."""
        import config.features as features
        from errors import FeatureGateError

        features.set_tier("community")
        with pytest.raises(FeatureGateError):
            features.check_feature(FLAG)

    def test_check_feature_permits_at_pro_tier(self):
        import config.features as features

        features.set_tier("pro")
        features.check_feature(FLAG)  # must not raise

    @pytest.mark.parametrize(
        ("tier", "enabled"), [("community", "False"), ("pro", "True")],
    )
    def test_the_flag_is_correct_at_boot_before_any_set_tier_call(self, tier, enabled):
        """``set_tier`` recomputes from ``_PRO_TIER_FLAGS``, so the tests above
        cannot see the *initializer* in the FEATURE_FLAGS literal at all — a
        hardcoded ``True`` there would leave a community container entitled from
        import until something happened to call ``set_tier``. Boot a real
        interpreter at each tier to read the value the process actually starts
        with.
        """
        import os
        import subprocess

        env = dict(os.environ, CERID_TIER=tier, PYTHONPATH=str(_REPO_ROOT / "src/mcp"))
        proc = subprocess.run(
            [
                sys.executable, "-c",
                "import config.features as f;"
                f"print(f.FEATURE_TIER, f.FEATURE_FLAGS[{FLAG!r}])",
            ],
            env=env, capture_output=True, text=True, timeout=120, check=True,
        )
        assert proc.stdout.strip().splitlines()[-1] == f"{tier} {enabled}"


# --- Declared home -----------------------------------------------------------


class TestTheFlagHasExactlyOneDeclaredHome:
    def test_it_is_declared_desktop(self):
        """Added 2026-08-10. Before the map existed, a paid flag with no plugin
        fell into a residual bucket that ``degraded`` never drew from, so it
        read as healthy whether or not anything implemented it."""
        import config.features as features

        assert features.NON_PLUGIN_IMPLEMENTATIONS.get(FLAG) == "desktop"

    def test_it_is_not_also_marked_planned(self):
        """"Planned" and "implemented in the desktop app" are contradictory
        claims; the health surface checks PLANNED first and would report the
        shipped feature as unbuilt."""
        import config.features as features

        assert FLAG not in features.PLANNED_FEATURES

    def test_no_plugin_manifest_in_either_root_claims_the_flag(self):
        """The sibling check in ``test_pro_feature_health.py`` globs only
        ``src/mcp/plugins``. ``plugins/__init__.load_plugins`` also scans the
        repo-root ``plugins/`` directory, so a manifest there can supply flags
        that check never sees. Scan both.
        """
        claimants = []
        for root in _PLUGIN_ROOTS:
            for manifest_path in sorted(root.glob("*/manifest.json")):
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if FLAG in (manifest.get("feature_flags") or []):
                    claimants.append(str(manifest_path))
        assert not claimants, (
            f"{FLAG} is declared NON_PLUGIN_IMPLEMENTATIONS[...] == 'desktop' but "
            f"these manifests also claim to supply it: {claimants}"
        )

    def test_the_manifest_scan_actually_reads_manifests(self):
        """Non-vacuity guard for the test above. If the plugin roots move or the
        glob stops matching, the emptiness of ``claimants`` proves nothing —
        so pin that the same walk finds real manifests declaring real flags."""
        seen_manifests = 0
        seen_flags: set[str] = set()
        for root in _PLUGIN_ROOTS:
            for manifest_path in root.glob("*/manifest.json"):
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                seen_manifests += 1
                seen_flags.update(manifest.get("feature_flags") or [])
        assert seen_manifests >= 5
        assert seen_flags, "no manifest declared any feature_flags — glob is broken"


# --- /health.pro_features ----------------------------------------------------


class _FakePluginsModule:
    """Drives the health view off a controlled plugin registry.

    ``get_loaded_plugins()`` is empty unless ``load_plugins()`` ran in this
    process, so reading the real registry from a unit test would assert against
    an empty map and pass no matter what. Substituting the module makes the
    input explicit: no backend plugin supplies this flag, which is exactly the
    state a running container is in.
    """

    def __init__(self, loaded: dict, failed: dict) -> None:
        self._loaded, self._failed = loaded, failed

    def get_loaded_plugins(self) -> dict:
        return self._loaded

    def get_failed_plugins(self) -> dict:
        return self._failed


@pytest.fixture
def report(monkeypatch):
    """``/health.pro_features`` payload at a chosen tier, with a chosen registry.

    ``_pro_feature_health()`` is the function ``/health`` assigns to
    ``result["pro_features"]``; it is called directly because the full health
    payload needs Neo4j and Redis.
    """
    import app.routers.health as health_mod
    import config.features as features

    def _build(tier: str = "pro", loaded: dict | None = None, failed: dict | None = None):
        features.set_tier(tier)
        monkeypatch.setitem(
            sys.modules, "plugins", _FakePluginsModule(loaded or {}, failed or {}),
        )
        return health_mod._pro_feature_health()

    return _build


class TestHealthReportsTheFeatureAsDesktopImplemented:
    def test_implementation_is_desktop_not_unknown(self, report):
        entry = report(tier="pro")["features"][FLAG]
        assert entry["implementation"] == "desktop"
        assert entry["entitled"] is True

    def test_it_is_not_degraded_merely_for_having_no_plugin(self, report):
        """A desktop-implemented feature has no backend plugin by design.
        Reporting that as degradation would make
        ``scripts/lint-pro-feature-health.py`` fail on every healthy Pro stack.
        """
        assert FLAG not in report(tier="pro")["degraded"]

    def test_a_failed_unrelated_plugin_does_not_drag_it_into_degraded(self, report):
        """The health view attributes a failed plugin by its ``feature_flags``,
        falling back to the plugin NAME. A failure that declares no flags must
        not be attributed to a flag it never claimed."""
        payload = report(
            tier="pro",
            failed={"apple-notes": {
                "name": "cerid-apple-notes", "tier": "pro", "feature_flags": [],
                "error_type": "PluginLoadError", "error": "invalid type 'ingestion'",
            }},
        )
        assert payload["features"][FLAG]["implementation"] == "desktop"
        assert FLAG not in payload["degraded"]

    def test_it_is_not_degraded_at_community_tier_either(self, report):
        """Unentitled is not degraded — a free install legitimately does not
        have this feature."""
        payload = report(tier="community")
        assert payload["features"][FLAG]["entitled"] is False
        assert FLAG not in payload["degraded"]

    def test_removing_the_declaration_makes_it_degrade(self, report, monkeypatch):
        """The gate on the two assertions above: they are only meaningful if
        the report CAN say otherwise. Drop the declaration and the same paid
        flag must fall through to ``unknown`` and land in ``degraded``."""
        import config.features as features

        monkeypatch.delitem(features.NON_PLUGIN_IMPLEMENTATIONS, FLAG)
        payload = report(tier="pro")
        assert payload["features"][FLAG]["implementation"] == "unknown"
        assert FLAG in payload["degraded"]


# --- The ingest contract the desktop connector posts to ----------------------


@pytest.fixture()
def ingest_client():
    from app.routers.ingestion import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


class TestTheIngestPathIsReachable:
    """``packages/desktop/src/main/connectors/apple_notes.ts`` posts every note
    through ``postStructured(mcpBaseUrl, 'apple_notes', {...})``, which is a
    POST to ``/ingest/structured`` with ``X-Client-ID: apple_notes``. The scan
    that produces those notes cannot run here, but the endpoint it posts into
    is ordinary backend code and its contract is pinned below.
    """

    def _post_a_note(self, client, monkeypatch):
        calls: list[tuple] = []

        def _fake_ingest(content, domain, metadata):
            calls.append((content, domain, metadata))
            return {"status": "ok", "artifact_id": "art:apple-notes-1"}

        monkeypatch.setattr("app.routers.ingestion.ingest_content", _fake_ingest)
        # AF-043: this test pins the payload-shape contract, not the Pro
        # entitlement gate — entitle apple_notes_reader so the shape assertions
        # below still exercise the ingest path.
        monkeypatch.setattr("config.features.is_feature_enabled", lambda _flag: True)
        resp = client.post(
            "/ingest/structured",
            json={
                "content": "# Recipe for sourdough\n\nFlour, water, salt, time.",
                "domain": "notes",
                "source_id": "x-coredata://note/p42",
                "metadata": {
                    "source": "apple_notes",
                    "title": "Recipe for sourdough",
                    "folder_path": "Personal/Recipes",
                    "account": "iCloud",
                    "modified_at": "2026-08-10T00:00:00Z",
                },
            },
            headers={"X-Client-ID": "apple_notes"},
        )
        return resp, calls

    def test_the_connectors_payload_shape_is_accepted(self, ingest_client, monkeypatch):
        resp, calls = self._post_a_note(ingest_client, monkeypatch)
        assert resp.status_code == 200
        assert len(calls) == 1

    def test_every_field_the_connector_sends_reaches_the_ingest_service(
        self, ingest_client, monkeypatch,
    ):
        """A 200 that dropped the folder or the note id would still look fine
        to the connector, which only checks ``res.ok``."""
        _resp, calls = self._post_a_note(ingest_client, monkeypatch)
        content, domain, metadata = calls[0]

        assert content.startswith("# Recipe for sourdough")
        assert domain == "notes"
        assert metadata["source"] == "apple_notes"
        assert metadata["title"] == "Recipe for sourdough"
        assert metadata["folder_path"] == "Personal/Recipes"
        assert metadata["account"] == "iCloud"
        assert metadata["modified_at"] == "2026-08-10T00:00:00Z"
        # AF-007: a non-UUID connector identifier is an external id, not a
        # :Source reference. The endpoint reclassifies the ``source_id`` the
        # connector sends into ``external_id`` (and scopes it with a
        # ``source_kind``) so it never reaches the per-source quality floor.
        assert metadata["external_id"] == "x-coredata://note/p42"
        assert metadata["source_kind"] == "apple_notes"
        assert "source_id" not in metadata
        assert metadata["client_source"] == "apple_notes"


# --- POST /sources tier gate -------------------------------------------------


@pytest.fixture()
def sources_client():
    from app.routers.sources import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def _create_source(client, kind: str):
    return client.post(
        "/sources",
        json={"kind": kind, "display_name": f"test-{kind}", "config": {}},
    )


class TestTheKindIsProGatedAtSourceCreation:
    """Follows the pattern in ``test_sources_pro_gate.py``, but drives the real
    tier through ``features.set_tier`` rather than stubbing ``is_tier_met``, so
    the flag wiring and the route gate are exercised together.
    """

    def test_the_kind_table_marks_it_pro(self):
        from core.ingest.sources.kinds import KIND_TIER

        assert KIND_TIER[KIND] == "pro"

    def test_creation_is_refused_at_community_tier(self, sources_client):
        import config.features as features

        features.set_tier("community")
        resp = _create_source(sources_client, KIND)
        assert resp.status_code == 403
        assert "Pro" in resp.json()["detail"]

    def test_no_connector_is_built_before_the_refusal(self, sources_client, monkeypatch):
        """A 403 raised after instantiation would already have touched the
        user's Notes store."""
        import config.features as features

        features.set_tier("community")
        built: list[str] = []
        monkeypatch.setattr(
            "app.routers.sources.get_connector", lambda k: built.append(k),
        )
        assert _create_source(sources_client, KIND).status_code == 403
        assert built == []

    def test_creation_passes_the_gate_at_pro_tier(self, sources_client):
        """Past the tier check it may still fail on store or connector grounds —
        anything other than 403 proves the Pro gate did not fire."""
        import config.features as features

        features.set_tier("pro")
        assert _create_source(sources_client, KIND).status_code != 403
