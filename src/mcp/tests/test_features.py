# Copyright 2026 Cerid AI. Apache-2.0 license.
"""Tests for feature flag system: is_feature_enabled, require_feature, check_tier, tier hierarchy."""

from unittest.mock import patch

import pytest

from config.features import (
    _TIER_LEVELS,
    FEATURE_FLAGS,
    check_feature,
    check_tier,
    is_feature_enabled,
    is_tier_met,
    require_feature,
)
from errors import FeatureGateError

# ---------------------------------------------------------------------------
# Tests: is_feature_enabled
# ---------------------------------------------------------------------------

class TestIsFeatureEnabled:
    def test_community_features_always_enabled(self):
        """Community features like truth_audit should always return True."""
        assert is_feature_enabled("truth_audit") is True
        assert is_feature_enabled("file_upload_gui") is True
        assert is_feature_enabled("live_metrics") is True

    def test_unknown_feature_returns_false(self):
        """Unknown feature names should return False."""
        assert is_feature_enabled("nonexistent_feature_xyz") is False

    def test_returns_bool(self):
        """Should always return a boolean."""
        for name in FEATURE_FLAGS:
            result = is_feature_enabled(name)
            assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# Tests: check_feature (sync, raises FeatureGateError)
# ---------------------------------------------------------------------------

class TestCheckFeature:
    def test_enabled_feature_no_raise(self):
        """Enabled features should not raise."""
        check_feature("truth_audit")  # Should not raise

    def test_disabled_feature_raises(self):
        """Disabled features should raise FeatureGateError."""
        with patch.dict(FEATURE_FLAGS, {"test_gated": False}):
            with pytest.raises(FeatureGateError):
                check_feature("test_gated")


# ---------------------------------------------------------------------------
# Tests: require_feature decorator
# ---------------------------------------------------------------------------

class TestRequireFeature:
    @pytest.mark.asyncio
    async def test_decorator_allows_enabled_feature(self):
        """Decorated function should execute when feature is enabled."""
        @require_feature("truth_audit")
        async def my_endpoint():
            return {"status": "ok"}

        result = await my_endpoint()
        assert result == {"status": "ok"}

    @pytest.mark.asyncio
    async def test_decorator_blocks_disabled_feature(self):
        """Decorated function should raise 403 when feature is disabled."""
        from fastapi import HTTPException

        @require_feature("sso_saml")  # Enterprise-only, disabled on community
        async def my_endpoint():
            return {"status": "ok"}

        with patch.dict(FEATURE_FLAGS, {"sso_saml": False}):
            with pytest.raises(HTTPException) as exc_info:
                await my_endpoint()
            assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# Tests: check_tier and is_tier_met
# ---------------------------------------------------------------------------

class TestTierHierarchy:
    def test_tier_levels_exist(self):
        """All three tiers should be defined."""
        assert "community" in _TIER_LEVELS
        assert "pro" in _TIER_LEVELS
        assert "enterprise" in _TIER_LEVELS

    def test_tier_ordering(self):
        """Enterprise > Pro > Community."""
        assert _TIER_LEVELS["enterprise"] > _TIER_LEVELS["pro"]
        assert _TIER_LEVELS["pro"] > _TIER_LEVELS["community"]

    @patch("config.features.FEATURE_TIER", "community")
    def test_community_tier_meets_community(self):
        assert is_tier_met("community") is True

    @patch("config.features.FEATURE_TIER", "community")
    def test_community_tier_does_not_meet_pro(self):
        assert is_tier_met("pro") is False

    @patch("config.features.FEATURE_TIER", "enterprise")
    def test_enterprise_meets_all(self):
        assert is_tier_met("community") is True
        assert is_tier_met("pro") is True
        assert is_tier_met("enterprise") is True

    @patch("config.features.FEATURE_TIER", "community")
    def test_check_tier_raises_on_insufficient(self):
        with pytest.raises(FeatureGateError):
            check_tier("pro")

    @patch("config.features.FEATURE_TIER", "pro")
    def test_check_tier_passes_on_sufficient(self):
        check_tier("pro")  # Should not raise
        check_tier("community")  # Should not raise


class TestSetTierSync:
    """set_tier must keep config.FEATURE_TIER (star-imported copy) in sync (OPEN-15)."""

    def test_set_tier_syncs_config_namespace(self):
        import config
        import config.features as feat

        original = feat.FEATURE_TIER
        try:
            feat.set_tier("enterprise")
            # Both the canonical global AND the star-imported copy must agree.
            assert feat.FEATURE_TIER == "enterprise"
            assert config.FEATURE_TIER == "enterprise"
            assert feat.current_tier() == "enterprise"

            feat.set_tier("community")
            assert config.FEATURE_TIER == "community"
            assert feat.current_tier() == "community"
        finally:
            feat.set_tier(original)

    def test_set_tier_rejects_invalid(self):
        import config.features as feat

        with pytest.raises(ValueError, match="Invalid tier"):
            feat.set_tier("platinum")


class TestProVisualizationFlags:
    """pro_visualization_* must be registered so the tour/atlas gates resolve (PE-01)."""

    def test_flags_registered(self):
        for flag in (
            "pro_visualization_tour",
            "pro_visualization_analytics",
            "pro_visualization_timeline",
        ):
            assert flag in FEATURE_FLAGS, flag

    @patch("config.features.FEATURE_TIER", "enterprise")
    def test_tour_enabled_at_enterprise(self):
        import config.features as feat

        feat._refresh_flags()
        assert is_feature_enabled("pro_visualization_tour") is True
