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

    assert report["degraded"] == []
    assert report["features"]["meeting_diarization"]["loaded"] is True


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


def test_desktop_and_in_process_features_are_not_reported_broken(plugin_state):
    """Router-served features (analytics, digests) and desktop-implemented
    connectors have no backend plugin. Absence of a plugin is not a failure."""
    import config.features as features

    features.set_tier("pro")
    health = plugin_state(loaded={}, failed={})
    report = health._pro_feature_health()

    assert report["features"]["advanced_analytics"]["implementation"] == "in_process_or_desktop"
    assert report["degraded"] == []
