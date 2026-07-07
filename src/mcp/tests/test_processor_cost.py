# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for core.processor.cost."""
from __future__ import annotations

from decimal import Decimal

import pytest

from core.processor.cost import CostEstimate, PricingTable, estimate

KNOWN_MODELS = [
    "anthropic/claude-opus-4-7",
    "anthropic/claude-sonnet-4-6",
    "anthropic/claude-haiku-4-5",
    "openai/gpt-5",
    "ollama/local",
]


class TestEstimateFunction:
    def test_returns_cost_estimate_instance(self) -> None:
        result = estimate("anthropic/claude-sonnet-4-6", 1000, 500)
        assert isinstance(result, CostEstimate)

    def test_token_counts_propagated(self) -> None:
        result = estimate("anthropic/claude-sonnet-4-6", 1000, 500)
        assert result.estimated_tokens_in == 1000
        assert result.estimated_tokens_out == 500

    def test_model_propagated(self) -> None:
        result = estimate("anthropic/claude-haiku-4-5", 100, 50)
        assert result.model == "anthropic/claude-haiku-4-5"

    def test_confidence_default_is_medium(self) -> None:
        result = estimate("openai/gpt-5", 100, 100)
        assert result.confidence == "medium"

    def test_confidence_override(self) -> None:
        result = estimate("openai/gpt-5", 100, 100, confidence="high")
        assert result.confidence == "high"

    @pytest.mark.parametrize("model", KNOWN_MODELS)
    def test_all_known_models_return_estimate(self, model: str) -> None:
        result = estimate(model, 1000, 200)
        assert isinstance(result.estimated_usd, Decimal)

    def test_unknown_model_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unknown model"):
            estimate("unknown/model-x", 100, 100)

    def test_estimated_usd_is_decimal_not_float(self) -> None:
        result = estimate("anthropic/claude-opus-4-7", 500, 250)
        assert isinstance(result.estimated_usd, Decimal)

    def test_ollama_local_is_zero_cost(self) -> None:
        result = estimate("ollama/local", 10_000, 5_000)
        assert result.estimated_usd == Decimal("0.00")

    def test_opus_costs_more_than_haiku_same_tokens(self) -> None:
        opus = estimate("anthropic/claude-opus-4-7", 1000, 500)
        haiku = estimate("anthropic/claude-haiku-4-5", 1000, 500)
        assert opus.estimated_usd > haiku.estimated_usd

    def test_cost_arithmetic_is_decimal_clean(self) -> None:
        # Verify repeated summation doesn't accumulate floating-point drift.
        total = Decimal("0")
        for _ in range(100):
            total += estimate("anthropic/claude-sonnet-4-6", 100, 50).estimated_usd
        single = estimate("anthropic/claude-sonnet-4-6", 100, 50).estimated_usd
        assert total == single * 100

    def test_custom_pricing_table(self) -> None:
        from core.processor.cost import _PricingRow
        custom_table = PricingTable(
            rows={
                "custom/model": _PricingRow(
                    usd_per_1k_in=Decimal("1.000"),
                    usd_per_1k_out=Decimal("2.000"),
                )
            }
        )
        result = estimate("custom/model", 1000, 1000, table=custom_table)
        assert result.estimated_usd == Decimal("3.000")


class TestPricingTable:
    def test_default_table_has_all_known_models(self) -> None:
        table = PricingTable()
        for model in KNOWN_MODELS:
            row = table.get_row(model)
            assert row is not None

    def test_get_row_raises_for_unknown(self) -> None:
        table = PricingTable()
        with pytest.raises(ValueError):
            table.get_row("not/a/model")

    def test_registered_models_returns_list(self) -> None:
        table = PricingTable()
        models = table.registered_models()
        assert isinstance(models, list)
        assert set(KNOWN_MODELS).issubset(set(models))


class TestOpenRouterAndFreeModelNormalization:
    """Task 2.5b fix: routed model ids must price, so the monthly cap accrues.

    ``config.settings.CATEGORIZE_MODELS`` ships ids with an
    ``openrouter/`` prefix and, for the "pro" tier, a dot-form Sonnet
    version string the pricing table originally spelled with a hyphen.
    Both must resolve without raising, and ``:free`` models must be
    exactly zero cost regardless of prefix.
    """

    def test_openrouter_prefixed_pro_model_matches_hyphen_row(self) -> None:
        prefixed = estimate("openrouter/anthropic/claude-sonnet-4.6", 1000, 500)
        bare = estimate("anthropic/claude-sonnet-4-6", 1000, 500)
        assert prefixed.estimated_usd > Decimal("0")
        assert prefixed.estimated_usd == bare.estimated_usd

    def test_openrouter_prefixed_free_model_is_zero_cost(self) -> None:
        result = estimate(
            "openrouter/meta-llama/llama-3.3-70b-instruct:free", 10_000, 5_000
        )
        assert result.estimated_usd == Decimal("0")

    def test_bare_free_model_is_zero_cost(self) -> None:
        result = estimate("some-vendor/some-model:free", 10_000, 5_000)
        assert result.estimated_usd == Decimal("0")

    def test_openrouter_prefixed_known_model_resolves(self) -> None:
        result = estimate("openrouter/openai/gpt-5", 100, 100)
        assert result.estimated_usd > Decimal("0")

    def test_genuinely_unknown_model_still_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown model"):
            estimate("openrouter/some-vendor/unpriced-model", 100, 100)
