# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Contract test enforcing the Pro-tier gating discipline.

Every Pro flag in ``config.features.FEATURE_FLAGS`` must either:
  (a) have at least one runtime gate call (``require_feature("<flag>")``,
      ``is_feature_enabled("<flag>")``, or ``check_feature("<flag>")``)
      anywhere under ``src/mcp/``; OR
  (b) be present in ``scripts/pro_gating_allowlist.txt`` because its
      implementation lands in a later phase of the 2026-05-20 Pro Tier
      Implementation Plan.

This is the test-side mirror of ``scripts/lint-pro-gating.py``. Both must
pass in CI; the lint catches PR-time regressions and this test guards the
discipline during local + integration runs.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
LINT_SCRIPT = REPO_ROOT / "scripts" / "lint-pro-gating.py"


def test_pro_gating_lint_passes() -> None:
    """Shell out to the lint script and assert success.

    Running via subprocess (rather than re-implementing the AST walk inline)
    keeps the lint script + this test from drifting apart — a single source
    of discovery logic.
    """
    if not LINT_SCRIPT.exists():
        pytest.skip(f"lint script not present: {LINT_SCRIPT}")
    result = subprocess.run(
        [sys.executable, str(LINT_SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(
            f"lint-pro-gating.py reported violations:\n{result.stdout}\n{result.stderr}"
        )


def _load_allowlist() -> set[str]:
    path = REPO_ROOT / "scripts" / "pro_gating_allowlist.txt"
    flags: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            flags.add(line)
    return flags


def test_connector_flags_are_gate_asserted_not_allowlisted() -> None:
    """Regression lock for the plugin-loader fix.

    The connector / meeting / intelligence flags load their gates through the
    class-based plugin loader. They must NOT sit in the allowlist — if their
    gate ever disappears (e.g. a loader regression), the lint must catch it.
    """
    allowlist = _load_allowlist()
    must_be_gated = {
        "gmail_connector",
        "outlook_connector",
        "google_calendar_sync",
        "outlook_calendar_sync",
        "apple_calendar_eventkit",
        "apple_photos_reader",
        "reminders_eventkit",
        "apple_mail_reader",
        "imessage_reader",
        "meeting_diarization",
        "calendar_stitching",
        "meeting_summary",
        "custom_smart_rag",
        "daily_digest",
        "inbox_triage",
        "advanced_analytics",
    }
    leaked = must_be_gated & allowlist
    assert not leaked, (
        f"these flags are gated and must not be allowlisted: {sorted(leaked)}"
    )


def test_allowlist_is_only_unimplemented_flags() -> None:
    """The allowlist is empty — every Pro flag now has a runtime gate
    (the Apple connector suite, Phase 4.1/4.2, closed the last entries)."""
    assert _load_allowlist() == set()


def test_feature_buckets_well_formed() -> None:
    """FEATURE_BUCKETS members must all be valid flags in FEATURE_FLAGS."""
    from config.features import FEATURE_BUCKETS, FEATURE_FLAGS

    for bucket, flags in FEATURE_BUCKETS.items():
        for flag in flags:
            assert flag in FEATURE_FLAGS, (
                f"Bucket {bucket!r} references unknown flag {flag!r} "
                f"(not in FEATURE_FLAGS)"
            )


def test_bucket_intersection_semantics() -> None:
    """A bucket is enabled iff ALL its BUILT member flags are enabled.

    Planned features (PLANNED_FEATURES — announced for a near-term
    release, not yet built; V1 Task 5.3 honesty gate) are excluded from
    the intersection: they must not disable an otherwise-available
    bucket. A bucket whose members are all planned is unavailable.
    """
    from config.features import (
        FEATURE_BUCKETS,
        FEATURE_FLAGS,
        PLANNED_FEATURES,
        is_bucket_enabled,
    )

    for bucket, flags in FEATURE_BUCKETS.items():
        built = [f for f in flags if f not in PLANNED_FEATURES]
        expected = bool(built) and all(FEATURE_FLAGS.get(f, False) for f in built)
        assert is_bucket_enabled(bucket) == expected, (
            f"Bucket {bucket!r} enabled-state ({is_bucket_enabled(bucket)}) "
            f"does not match all-of built member flags ({expected})"
        )


def test_get_bucket_status_shape() -> None:
    """Shape contract for the /billing/capabilities response."""
    from config.features import get_bucket_status

    status = get_bucket_status()
    assert isinstance(status, dict)
    assert "pro_meeting_capture" in status
    assert "mac_native" in status
    for _name, payload in status.items():
        assert "enabled" in payload
        assert "tier_required" in payload
        assert "features" in payload
        assert isinstance(payload["features"], dict)
        for _flag_name, flag_detail in payload["features"].items():
            assert "enabled" in flag_detail
            assert "tier_required" in flag_detail


def test_set_tier_flips_pro_buckets_together() -> None:
    """Switching to Pro lights up all pro_* buckets atomically."""
    from config.features import (
        FEATURE_BUCKETS,
        FEATURE_TIER,
        is_bucket_enabled,
        set_tier,
    )

    original = FEATURE_TIER
    try:
        set_tier("community")
        for bucket in FEATURE_BUCKETS:
            if bucket.startswith("pro_"):
                assert not is_bucket_enabled(bucket), (
                    f"Pro bucket {bucket!r} should be OFF at community tier"
                )

        set_tier("pro")
        for bucket in FEATURE_BUCKETS:
            if bucket.startswith("pro_"):
                assert is_bucket_enabled(bucket), (
                    f"Pro bucket {bucket!r} should be ON at pro tier"
                )
    finally:
        set_tier(original)


def test_every_pro_flag_flips_with_the_tier() -> None:
    """EVERY Pro flag must follow a runtime tier change — not just bucketed ones.

    ``test_set_tier_flips_pro_buckets_together`` iterates ``pro_*`` buckets, so a
    Pro flag outside every bucket was invisible to it. ``spotlight_donation`` was
    exactly that: initialised from ``_pro_level()`` at import but absent from
    ``_PRO_TIER_FLAGS``, so ``_refresh_flags()`` never touched it and its value
    froze at whatever the boot tier implied. Activating a license then left it
    reported as locked; losing one left it reported as unlocked.

    The population here is "every flag the capability map calls Pro", which is
    what the UI and the license router actually read.
    """
    import config.features as features

    original = features.FEATURE_TIER
    pro_flags = sorted(
        name for name in features.FEATURE_FLAGS
        if features._get_feature_tier(name) == "pro"
        # Planned-but-unbuilt features are pinned False on purpose; the tier
        # matrix renders them "Coming in 1.0.x" rather than claiming them.
        and name not in features.PLANNED_FEATURES
    )
    assert pro_flags, "no Pro flags discovered — the population query is broken"

    try:
        features.set_tier("community")
        stuck_on = [f for f in pro_flags if features.FEATURE_FLAGS[f]]
        assert not stuck_on, (
            f"Pro flags still enabled at community tier: {stuck_on} — a paid "
            "feature that survives losing entitlement"
        )

        features.set_tier("pro")
        stuck_off = [f for f in pro_flags if not features.FEATURE_FLAGS[f]]
        assert not stuck_off, (
            f"Pro flags still disabled at pro tier: {stuck_off} — a paying "
            "customer is told these are locked"
        )
    finally:
        features.set_tier(original)


def test_pro_tier_flag_population_is_complete() -> None:
    """Any flag initialised with ``_pro_level()`` must be in ``_PRO_TIER_FLAGS``.

    The two are independent sources of truth — the per-flag initialiser decides
    the boot value, ``_PRO_TIER_FLAGS`` decides what refreshes — and nothing
    reconciled them. This is the static counterpart to the behavioural test
    above; it names the offending flag directly.
    """
    import ast
    from pathlib import Path

    import config.features as features

    src = Path(features.__file__).with_suffix(".py")
    tree = ast.parse(src.read_text(encoding="utf-8"))
    initialised = {
        key.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Dict)
        for key, value in zip(node.keys, node.values)
        if isinstance(key, ast.Constant)
        and isinstance(key.value, str)
        and isinstance(value, ast.Call)
        and getattr(value.func, "id", None) == "_pro_level"
    }
    assert initialised, "found no _pro_level() initialisers — parser drifted"

    missing = sorted(initialised - set(features._PRO_TIER_FLAGS))
    assert not missing, (
        f"Pro-initialised flags absent from _PRO_TIER_FLAGS: {missing}. "
        "_refresh_flags() will never update them, so they freeze at their "
        "import-time value. Add them to the non-bucketed set."
    )


def test_mac_native_bucket_is_community() -> None:
    """The mac_native bucket is community baseline, not Pro-gated."""
    from config.features import (
        FEATURE_TIER,
        is_bucket_enabled,
        set_tier,
    )

    original = FEATURE_TIER
    try:
        set_tier("community")
        assert is_bucket_enabled("mac_native"), (
            "mac_native must be enabled at community tier"
        )
    finally:
        set_tier(original)


def test_demoted_features_are_community() -> None:
    """Phase 1 demotions: image_understanding, parent_child_retrieval,
    docling_parser, audio_transcription_plain must be community."""
    from config.features import _get_feature_tier

    for flag in (
        "image_understanding",
        "parent_child_retrieval",
        "docling_parser",
        "audio_transcription_plain",
    ):
        assert _get_feature_tier(flag) == "community", (
            f"{flag} should be community-tier after the 2026-05-20 rebalance"
        )
