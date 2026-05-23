# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
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
    """A bucket is enabled iff ALL its member flags are enabled."""
    from config.features import (
        FEATURE_BUCKETS,
        FEATURE_FLAGS,
        is_bucket_enabled,
    )

    for bucket, flags in FEATURE_BUCKETS.items():
        expected = all(FEATURE_FLAGS.get(f, False) for f in flags)
        assert is_bucket_enabled(bucket) == expected, (
            f"Bucket {bucket!r} enabled-state ({is_bucket_enabled(bucket)}) "
            f"does not match all-of member flags ({expected})"
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
