# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for memory salience scoring, classification, and migration."""

import math
from unittest.mock import MagicMock

import pytest

from agents.memory import calculate_memory_score

# ---------------------------------------------------------------------------
# calculate_memory_score — decay curves per type
# ---------------------------------------------------------------------------


class TestEmpiricalMemory:
    """Empirical facts should never decay."""

    def test_no_decay_at_zero_age(self):
        score = calculate_memory_score(1.0, 0, 0.0, memory_type="empirical")
        assert score == pytest.approx(1.0)

    def test_no_decay_at_one_year(self):
        score = calculate_memory_score(1.0, 0, 365.0, memory_type="empirical")
        assert score == pytest.approx(1.0)

    def test_source_authority_applied(self):
        score = calculate_memory_score(1.0, 0, 100.0, memory_type="empirical", source_authority=0.5)
        assert score == pytest.approx(0.5)

    def test_access_count_ignored(self):
        """Access count should not change empirical scores."""
        s1 = calculate_memory_score(1.0, 0, 0.0, memory_type="empirical")
        s2 = calculate_memory_score(1.0, 100, 0.0, memory_type="empirical")
        assert s1 == s2


class TestTemporalMemory:
    """Temporal facts use step function: full score before event, 0.1 residual after."""

    def test_full_score_before_event(self):
        score = calculate_memory_score(1.0, 0, -1.0, memory_type="temporal")
        assert score == pytest.approx(1.0)

    def test_residual_after_event(self):
        score = calculate_memory_score(1.0, 0, 1.0, memory_type="temporal")
        assert score == pytest.approx(0.1)

    def test_residual_long_after_event(self):
        score = calculate_memory_score(1.0, 0, 365.0, memory_type="temporal")
        assert score == pytest.approx(0.1)


class TestDecisionMemory:
    """Decisions use power-law decay with stability=90 days."""

    def test_fresh_decision_no_decay(self):
        score = calculate_memory_score(1.0, 0, 0.0, stability_days=90.0, memory_type="decision")
        # Reinforcement: min(1 + log2(1+0), 5) = 1.0
        assert score == pytest.approx(1.0)

    def test_power_law_at_stability(self):
        """At t=S, power-law should retain ~71%."""
        score = calculate_memory_score(1.0, 0, 90.0, stability_days=90.0, memory_type="decision")
        expected_decay = (1.0 + 90.0 / (9.0 * 90.0)) ** (-0.5)  # (1+1/9)^-0.5 ≈ 0.9487
        assert score == pytest.approx(expected_decay, rel=0.01)

    def test_power_law_long_tail(self):
        """At 365 days, decision with S=90 should still retain significant score."""
        score = calculate_memory_score(1.0, 0, 365.0, stability_days=90.0, memory_type="decision")
        # (1 + 365/(9*90))^(-0.5) = (1 + 0.4506)^-0.5 ≈ 0.83
        assert score > 0.1, "Power-law should preserve long tail"

    def test_power_law_vs_exponential_long_tail(self):
        """Power-law should retain much more than exponential at long time scales."""
        power_score = calculate_memory_score(
            1.0, 0, 365.0, stability_days=90.0, memory_type="decision",
        )
        exp_score = calculate_memory_score(
            1.0, 0, 365.0, stability_days=90.0, memory_type="project_context",
        )
        assert power_score > exp_score * 5, "Power-law should massively beat exponential at 365d"


class TestPreferenceMemory:
    """Preferences use power-law decay with stability=60 days."""

    def test_decays_slower_than_exponential(self):
        pref_score = calculate_memory_score(1.0, 0, 180.0, stability_days=60.0, memory_type="preference")
        # Should still be significant at 180 days
        assert pref_score > 0.1


class TestProjectContextMemory:
    """Project context uses exponential decay with stability=14 days."""

    def test_half_life(self):
        """At t=S, exponential retains exactly 50%."""
        score = calculate_memory_score(1.0, 0, 14.0, stability_days=14.0, memory_type="project_context")
        assert score == pytest.approx(0.5, rel=0.01)

    def test_fast_fade(self):
        """At 4x stability, only ~6.25% remains."""
        score = calculate_memory_score(1.0, 0, 56.0, stability_days=14.0, memory_type="project_context")
        assert score == pytest.approx(0.0625, rel=0.01)


class TestConversationalMemory:
    """Conversational memory uses exponential with stability=3 days (very fast fade)."""

    def test_nearly_gone_in_two_weeks(self):
        score = calculate_memory_score(1.0, 0, 14.0, stability_days=3.0, memory_type="conversational")
        # 2^(-14/3) ≈ 0.04
        assert score < 0.05

    def test_half_life_3_days(self):
        score = calculate_memory_score(1.0, 0, 3.0, stability_days=3.0, memory_type="conversational")
        assert score == pytest.approx(0.5, rel=0.01)


# ---------------------------------------------------------------------------
# Reinforcement (access count weighting)
# ---------------------------------------------------------------------------


class TestReinforcement:
    """Access count reinforcement should boost scores but cap at 5x."""

    def test_zero_accesses(self):
        score = calculate_memory_score(1.0, 0, 0.0, stability_days=90.0, memory_type="decision")
        # reinforcement = min(1 + log2(1+0), 5) = 1.0
        assert score == pytest.approx(1.0)

    def test_moderate_accesses(self):
        score = calculate_memory_score(1.0, 10, 0.0, stability_days=90.0, memory_type="decision")
        expected = min(1.0 + math.log2(1.0 + 10.0), 5.0)
        assert score == pytest.approx(expected, rel=0.01)

    def test_cap_at_5x(self):
        """Even massive access counts should cap reinforcement at 5x."""
        score = calculate_memory_score(1.0, 100000, 0.0, stability_days=90.0, memory_type="decision")
        assert score <= 5.0

    def test_recency_weighted_access(self):
        """Recent accesses should matter more than old ones."""
        # 5 accesses yesterday vs 5 accesses 100 days ago
        recent_ages = [1.0] * 5
        old_ages = [100.0] * 5
        score_recent = calculate_memory_score(
            1.0, 5, 0.0, stability_days=90.0, memory_type="decision", access_ages=recent_ages,
        )
        score_old = calculate_memory_score(
            1.0, 5, 0.0, stability_days=90.0, memory_type="decision", access_ages=old_ages,
        )
        assert score_recent > score_old


# ---------------------------------------------------------------------------
# Source authority weighting
# ---------------------------------------------------------------------------


class TestSourceAuthority:
    """Source authority should scale the final score."""

    def test_full_authority(self):
        score = calculate_memory_score(1.0, 0, 0.0, memory_type="decision", source_authority=1.0)
        assert score == pytest.approx(1.0)

    def test_half_authority(self):
        score = calculate_memory_score(1.0, 0, 0.0, memory_type="decision", source_authority=0.5)
        assert score == pytest.approx(0.5)

    def test_web_search_authority(self):
        score = calculate_memory_score(1.0, 0, 0.0, memory_type="decision", source_authority=0.4)
        assert score == pytest.approx(0.4)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_negative_age_clamped(self):
        """Negative age (future timestamps) should not produce NaN or negative."""
        score = calculate_memory_score(1.0, 0, -5.0, stability_days=30.0, memory_type="decision")
        assert score >= 0.0
        assert not math.isnan(score)

    def test_zero_base_score(self):
        score = calculate_memory_score(0.0, 5, 10.0, stability_days=30.0, memory_type="decision")
        assert score == 0.0

    def test_unknown_type_uses_exponential(self):
        """Unknown memory types should fall through to exponential decay."""
        score = calculate_memory_score(1.0, 0, 30.0, stability_days=30.0, memory_type="unknown_type")
        # Exponential: 2^(-30/30) = 0.5
        assert score == pytest.approx(0.5, rel=0.01)

    def test_none_stability_uses_config_default(self):
        """When stability is None, should use config lookup or fallback."""
        score = calculate_memory_score(1.0, 0, 30.0, stability_days=None, memory_type="decision")
        assert score > 0.0
        assert not math.isnan(score)

    def test_score_always_non_negative(self):
        """Score should never go negative regardless of inputs."""
        for mem_type in ("empirical", "decision", "preference", "project_context", "temporal", "conversational"):
            score = calculate_memory_score(1.0, 0, 10000.0, stability_days=1.0, memory_type=mem_type)
            assert score >= 0.0, f"Negative score for type {mem_type}"


# ---------------------------------------------------------------------------
# Migration mappings
# ---------------------------------------------------------------------------


class TestMigrationMappings:
    """Verify legacy type migration mappings from config."""

    def test_fact_maps_to_empirical(self):
        import config

        assert config.MEMORY_TYPE_MIGRATION["fact"] == "empirical"

    def test_action_item_maps_to_project_context(self):
        import config

        assert config.MEMORY_TYPE_MIGRATION["action_item"] == "project_context"

    def test_decision_unchanged(self):
        import config

        assert "decision" not in config.MEMORY_TYPE_MIGRATION
        assert "decision" in config.MEMORY_TYPES

    def test_preference_unchanged(self):
        import config

        assert "preference" not in config.MEMORY_TYPE_MIGRATION
        assert "preference" in config.MEMORY_TYPES

    def test_all_six_types_defined(self):
        import config

        expected = {"empirical", "decision", "preference", "project_context", "temporal", "conversational"}
        assert config.MEMORY_TYPES == expected


# ---------------------------------------------------------------------------
# Neo4j migration function
# ---------------------------------------------------------------------------


class TestMigrateMemorySalience:
    """Test the Neo4j migration function."""

    def test_idempotent_no_unmigrated(self):
        """Should report 0 migrated when all artifacts already have source_authority."""
        from db.neo4j.migrations import migrate_memory_salience

        driver = MagicMock()
        session = MagicMock()
        driver.session.return_value.__enter__ = MagicMock(return_value=session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)

        # No unmigrated artifacts
        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter([]))
        session.run.return_value = mock_result

        result = migrate_memory_salience(driver)
        assert result["migrated"] == 0

    def test_migrates_legacy_fact_type(self):
        """Should map 'fact' → 'empirical' during migration."""
        from db.neo4j.migrations import migrate_memory_salience

        driver = MagicMock()
        session = MagicMock()
        driver.session.return_value.__enter__ = MagicMock(return_value=session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)

        # First call returns unmigrated records, subsequent calls are SET operations
        record = {"id": "art-1", "memory_type": "fact"}
        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter([record]))
        # list() is called on the result
        mock_list_result = [record]

        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # First call: SELECT unmigrated
                result = MagicMock()
                result.__iter__ = MagicMock(return_value=iter([record]))
                # list() conversion
                return mock_list_result
            # Second call: SET operation
            return MagicMock()

        session.run.side_effect = side_effect

        result = migrate_memory_salience(driver)
        assert result["migrated"] == 1
        # Verify the SET call used "empirical" (mapped from "fact")
        set_call = session.run.call_args_list[-1]
        assert set_call.kwargs.get("mem_type") == "empirical" or (
            len(set_call.args) > 0 and "empirical" in str(set_call)
        )
