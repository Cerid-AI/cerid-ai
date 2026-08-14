# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Pro-feature liveness bookkeeping — the gate behind /health.pro_features.

Until 2026-08-09 ``plugins/__init__.py`` logged a plugin load failure at ERROR
and threw it away. Three Pro plugins (metamorphic verification, the three
Meeting Capture features, Spotlight donation) were dead on a fully licensed
install and nothing could tell: /health looked fine and /billing/capabilities
kept reporting them enabled.

These tests pin the two halves of the fix — failures are *remembered*, and an
entitled-but-unloaded feature is reported as ``degraded``.
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture(autouse=True)
def _restore_feature_tier():
    import config.features as features

    original = features.FEATURE_TIER
    yield
    features.FEATURE_TIER = original
    features._refresh_flags()


@pytest.fixture
def plugin_state(monkeypatch):
    """Drive the health view off controlled loaded/failed plugin maps."""
    import app.routers.health as health_mod

    def _apply(loaded: dict, failed: dict):
        monkeypatch.setitem(
            __import__("sys").modules, "plugins",
            _FakePluginsModule(loaded, failed),
        )
        return health_mod

    return _apply


class _FakePluginsModule:
    def __init__(self, loaded: dict, failed: dict) -> None:
        self._loaded, self._failed = loaded, failed

    def get_loaded_plugins(self) -> dict:
        return self._loaded

    def get_failed_plugins(self) -> dict:
        return self._failed


# --- Failure bookkeeping ------------------------------------------------------

def test_a_failed_plugin_is_remembered_not_discarded(tmp_path):
    """The whole point: an ERROR log is not a record anything can gate on."""
    import plugins as P

    entry = tmp_path / "brokenplugin"
    entry.mkdir()
    (entry / "manifest.json").write_text(json.dumps({
        "name": "broken_thing", "tier": "pro", "feature_flags": ["broken_flag"],
    }))

    P._failed_plugins.clear()
    P._record_plugin_failure(entry, ValueError("kaboom"))

    rec = P.get_failed_plugins()["brokenplugin"]
    assert rec["name"] == "broken_thing"
    assert rec["tier"] == "pro"
    assert rec["feature_flags"] == ["broken_flag"]
    assert rec["error_type"] == "ValueError"
    assert "kaboom" in rec["error"]
    P._failed_plugins.clear()


def test_failure_recording_survives_an_unreadable_manifest(tmp_path):
    """The load may have failed *because* the manifest is broken; bookkeeping
    must not raise into the scan loop on top of it."""
    import plugins as P

    entry = tmp_path / "nomanifest"
    entry.mkdir()
    (entry / "manifest.json").write_text("{not json")

    P._failed_plugins.clear()
    P._record_plugin_failure(entry, RuntimeError("boom"))

    rec = P.get_failed_plugins()["nomanifest"]
    assert rec["tier"] == "unknown"
    assert rec["feature_flags"] == []
    P._failed_plugins.clear()


# --- Health view --------------------------------------------------------------

def test_entitled_but_unloaded_pro_feature_is_degraded(plugin_state):
    import config.features as features

    features.set_tier("pro")
    health = plugin_state(
        loaded={},
        failed={"meeting_capture": {
            "name": "meeting_capture", "tier": "pro",
            "feature_flags": ["meeting_diarization"],
            "error_type": "AttributeError", "error": "no attribute 'AudioMetaData'",
        }},
    )
    report = health._pro_feature_health()

    assert "meeting_diarization" in report["degraded"]
    entry = report["features"]["meeting_diarization"]
    assert entry["entitled"] is True
    assert entry["loaded"] is False
    assert "AudioMetaData" in entry["blocked_reason"]


def test_the_same_failure_is_not_degraded_when_unentitled(plugin_state):
    """A community stack legitimately does not load Pro plugins. Reporting that
    as degraded would make the gate cry wolf on every free install."""
    import config.features as features

    features.set_tier("community")
    health = plugin_state(
        loaded={},
        failed={"meeting_capture": {
            "name": "meeting_capture", "tier": "pro",
            "feature_flags": ["meeting_diarization"],
            "error_type": "AttributeError", "error": "boom",
        }},
    )
    assert health._pro_feature_health()["degraded"] == []


def test_a_loaded_plugin_reports_healthy(plugin_state):
    """A plugin that loaded supplies its flags, and they report loaded.

    Two things changed here on 2026-08-10. The fixture's `feature_flags` key
    used to be fiction — `get_loaded_plugins()` did not return it, so this
    passed against a shape production never produced while real loaded plugins
    were attributed to nothing. That is fixed at the source, so the fixture is
    now realistic.

    Second, it asserted `degraded == []` while stubbing exactly ONE plugin.
    That only held because every other paid flag fell into the residual bucket.
    Now that an unbacked paid flag degrades, a one-plugin fixture legitimately
    degrades the other twelve — so assert the flag under test, not the whole
    report. Suppressing that by stubbing every plugin would restore the very
    blindness this change removed.
    """
    import config.features as features

    features.set_tier("pro")
    health = plugin_state(
        loaded={"meeting_capture": {
            "name": "meeting_capture", "tier": "pro",
            "feature_flags": ["meeting_diarization"],
        }},
        failed={},
    )
    report = health._pro_feature_health()

    assert report["features"]["meeting_diarization"]["loaded"] is True
    assert report["features"]["meeting_diarization"]["implementation"] == "backend_plugin"
    assert "meeting_diarization" not in report["degraded"]


def test_planned_features_are_not_reported_broken(plugin_state):
    """A feature deliberately marked 'Coming in 1.0.x' has no implementation by
    design — that is honesty, not degradation."""
    import config.features as features

    features.set_tier("pro")
    health = plugin_state(loaded={}, failed={})
    report = health._pro_feature_health()

    for flag in features.PLANNED_FEATURES:
        if flag in report["features"]:
            assert report["features"][flag]["implementation"] == "planned"
            assert flag not in report["degraded"]


def test_declared_non_plugin_features_are_not_reported_broken(plugin_state):
    """Router-served and desktop-implemented features have no backend plugin,
    and absence of a plugin is not a failure — PROVIDED they say so.

    Rewritten 2026-08-10. This asserted `implementation == "in_process_or_desktop"`,
    which pinned the residual bucket itself as correct: any flag with no plugin
    landed there, so the test passed equally for a feature genuinely served by a
    router and for one nobody had implemented. It now asserts the SPECIFIC
    declared home, so a flag losing its declaration is visible.
    """
    import config.features as features

    features.set_tier("pro")
    health = plugin_state(loaded={}, failed={})
    report = health._pro_feature_health()

    assert report["features"]["advanced_analytics"]["implementation"] == "in_process"
    assert report["features"]["spotlight_donation"]["implementation"] == "desktop"
    # Nothing here is a plugin failure, so nothing router- or desktop-served
    # may appear in `degraded`.
    assert "advanced_analytics" not in report["degraded"]
    assert "spotlight_donation" not in report["degraded"]


def test_an_undeclared_paid_flag_degrades(plugin_state, monkeypatch):
    """The point of the redesign: a paid flag with no plugin and no declaration
    must FAIL, not fall into a bucket nothing reads."""
    import config.features as features

    features.set_tier("pro")
    orig_tier = features._get_feature_tier
    monkeypatch.setitem(features.FEATURE_FLAGS, "brand_new_paid_thing", True)
    monkeypatch.setattr(
        features, "_get_feature_tier",
        lambda f: "pro" if f == "brand_new_paid_thing" else orig_tier(f),
    )
    health = plugin_state(loaded={}, failed={})
    report = health._pro_feature_health()

    assert report["features"]["brand_new_paid_thing"]["implementation"] == "unknown"
    assert "brand_new_paid_thing" in report["degraded"]


def test_a_flag_declared_unimplemented_degrades_when_entitled(plugin_state, monkeypatch):
    """A flag recorded as `unimplemented` must surface as degraded when entitled.

    Uses a SYNTHETIC flag. It used to assert on `sso_saml`, which was the real
    example until it was built on 2026-08-11 — at which point the test failed
    for the best possible reason, and the fix was not to find another
    unimplemented flag to point at but to stop depending on one existing. The
    `unimplemented` bucket is empty now, and this must keep working when it is.
    """
    import config.features as features

    features.set_tier("enterprise")
    orig_tier = features._get_feature_tier
    monkeypatch.setitem(features.FEATURE_FLAGS, "sold_but_unbuilt", True)
    monkeypatch.setitem(features.NON_PLUGIN_IMPLEMENTATIONS, "sold_but_unbuilt", "unimplemented")
    monkeypatch.setattr(
        features, "_get_feature_tier",
        lambda f: "enterprise" if f == "sold_but_unbuilt" else orig_tier(f),
    )
    health = plugin_state(loaded={}, failed={})
    report = health._pro_feature_health()

    assert report["features"]["sold_but_unbuilt"]["implementation"] == "unimplemented"
    assert "sold_but_unbuilt" in report["degraded"]


def test_the_unimplemented_bucket_is_empty(plugin_state):
    """Nothing is currently sold and unbuilt — and this says so out loud.

    A green run of the suite above would otherwise be equally consistent with
    "the bucket is empty" and "the bucket is full and nobody looked".
    """
    import config.features as features

    unimplemented = sorted(
        flag
        for flag, kind in features.NON_PLUGIN_IMPLEMENTATIONS.items()
        if kind == "unimplemented"
    )
    assert unimplemented == [], (
        f"these are entitled and have no implementation: {unimplemented}"
    )


# --- Sprint 2: the residual bucket, and the gates that could not fail --------


class TestEveryPaidFlagHasADeclaredHome:
    """The invariant that replaced the `in_process_or_desktop` catch-all.

    That bucket was where 14 of 27 paid flags lived, and `degraded` never drew
    from it — so deleting a broken plugin, narrowing CERID_ENABLED_PLUGINS, or
    adding a flag nobody implemented all landed there and read as healthy.
    """

    def _paid_flags(self):
        import config.features as f
        return [
            k for k in f.FEATURE_FLAGS
            if f._get_feature_tier(k) in ("pro", "enterprise")
        ]

    def test_every_paid_flag_is_plugin_backed_planned_or_declared(self):
        import json
        import pathlib

        import config.features as f

        root = pathlib.Path(__file__).resolve().parents[3]
        manifest_flags: set[str] = set()
        for m in (root / "src/mcp/plugins").glob("*/manifest.json"):
            manifest_flags.update(json.loads(m.read_text()).get("feature_flags") or [])

        undeclared = [
            flag for flag in self._paid_flags()
            if flag not in manifest_flags
            and flag not in f.PLANNED_FEATURES
            and flag not in f.NON_PLUGIN_IMPLEMENTATIONS
        ]
        assert not undeclared, (
            "paid flags with no home — add a plugin, mark PLANNED, or declare "
            f"in NON_PLUGIN_IMPLEMENTATIONS: {sorted(undeclared)}"
        )

    def test_declarations_use_a_known_kind(self):
        import config.features as f

        allowed = {"in_process", "desktop", "entitlement_only", "unimplemented"}
        bad = {k: v for k, v in f.NON_PLUGIN_IMPLEMENTATIONS.items() if v not in allowed}
        assert not bad, f"unknown implementation kinds: {bad}"

    def test_the_map_does_not_claim_flags_a_plugin_already_supplies(self):
        """Two homes for one flag means one of them is a lie, and the reader
        cannot tell which."""
        import json
        import pathlib

        import config.features as f

        root = pathlib.Path(__file__).resolve().parents[3]
        manifest_flags: set[str] = set()
        for m in (root / "src/mcp/plugins").glob("*/manifest.json"):
            manifest_flags.update(json.loads(m.read_text()).get("feature_flags") or [])

        overlap = manifest_flags & set(f.NON_PLUGIN_IMPLEMENTATIONS)
        assert not overlap, f"declared non-plugin but supplied by a plugin: {sorted(overlap)}"


class TestManifestTierMatchesItsFlags:
    """`plugins/ocr` declared `tier: pro` while its only flag, `ocr_parsing`,
    is community. At community tier the loader therefore skipped it while the
    flag kept reporting enabled — the feature was advertised and absent. Pin
    the class, not the instance."""

    def test_no_manifest_requires_a_higher_tier_than_its_flags(self):
        import json
        import pathlib

        import config.features as f

        root = pathlib.Path(__file__).resolve().parents[3]
        rank = {"community": 0, "pro": 1, "enterprise": 2}
        mismatched = {}
        for m in sorted((root / "src/mcp/plugins").glob("*/manifest.json")):
            d = json.loads(m.read_text())
            flags = d.get("feature_flags") or []
            if not flags:
                continue
            need = rank.get(str(d.get("tier", "community")), 0)
            for flag in flags:
                have = rank.get(f._get_feature_tier(flag), 0)
                if need > have:
                    mismatched[d.get("name")] = (
                        f"manifest tier={d.get('tier')} but {flag} is "
                        f"{f._get_feature_tier(flag)}"
                    )
        assert not mismatched, (
            f"plugin gated above the tier of the flag it supplies: {mismatched}"
        )


# --- Conditionally-mounted implementations -----------------------------------


def test_sso_saml_degrades_when_multi_user_is_off(plugin_state, monkeypatch):
    """`in_process` is a claim about a router that is MOUNTED.

    app/routers/saml.py is registered only under CERID_MULTI_USER, so on a
    single-user Enterprise install the flag was entitled, declared
    implemented, and served by nothing — the residual-bucket substitution, one
    flag wide. Caught live: the gate reported "none degraded" on a stack whose
    /openapi.json contained no /auth/saml path at all.
    """
    import config.features as features

    monkeypatch.setattr(features, "CERID_MULTI_USER", False)
    features.set_tier("enterprise")
    health = plugin_state(loaded={}, failed={})
    report = health._pro_feature_health()

    assert report["features"]["sso_saml"]["loaded"] is False
    assert "CERID_MULTI_USER" in report["features"]["sso_saml"]["blocked_reason"]
    assert "sso_saml" in report["degraded"]


def test_sso_saml_is_healthy_when_multi_user_is_on(plugin_state, monkeypatch):
    import config.features as features

    monkeypatch.setattr(features, "CERID_MULTI_USER", True)
    features.set_tier("enterprise")
    health = plugin_state(loaded={}, failed={})
    report = health._pro_feature_health()

    assert report["features"]["sso_saml"]["implementation"] == "in_process"
    assert "sso_saml" not in report["degraded"]
