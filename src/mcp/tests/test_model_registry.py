# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the dynamic model registry (utils/model_registry.py)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from utils.model_registry import (
    ACTIVE_MODELS,
    get_model,
    get_pricing,
    validate_models,
)

# ---------------------------------------------------------------------------
# ACTIVE_MODELS structure
# ---------------------------------------------------------------------------


class TestActiveModelsStructure:
    def test_has_all_roles(self):
        """Registry must contain all four role categories."""
        for role in ("chat", "internal", "verification", "tiers"):
            assert role in ACTIVE_MODELS, f"Missing role: {role}"

    def test_all_models_have_openrouter_prefix(self):
        """Every model ID in the registry must start with 'openrouter/'."""
        for role, models in ACTIVE_MODELS.items():
            if not isinstance(models, dict):
                continue
            for key, value in models.items():
                if isinstance(value, list):
                    for m in value:
                        assert m.startswith("openrouter/"), (
                            f"{role}.{key}: {m!r} missing openrouter/ prefix"
                        )
                elif isinstance(value, str):
                    assert value.startswith("openrouter/"), (
                        f"{role}.{key}: {value!r} missing openrouter/ prefix"
                    )

    def test_each_role_has_default(self):
        """Each role category should have a 'default' key."""
        for role in ("chat", "internal", "verification"):
            assert "default" in ACTIVE_MODELS[role], f"{role} missing 'default' key"


# ---------------------------------------------------------------------------
# get_model
# ---------------------------------------------------------------------------


class TestGetModel:
    def test_returns_string(self):
        result = get_model("chat", "default")
        assert isinstance(result, str)
        assert result.startswith("openrouter/")

    def test_returns_specific_model(self):
        assert get_model("chat", "advanced") == "openrouter/anthropic/claude-sonnet-4.6"

    def test_fallback_for_nonexistent_role(self):
        result = get_model("nonexistent")
        assert result == "openrouter/openai/gpt-4o-mini"

    def test_fallback_for_nonexistent_key(self):
        """Unknown key within a valid role falls back to that role's default."""
        result = get_model("chat", "nonexistent_key")
        assert result == ACTIVE_MODELS["chat"]["default"]

    def test_pool_key_returns_default(self):
        """Requesting a list-valued key (pool) returns the role default."""
        result = get_model("verification", "pool")
        assert result == ACTIVE_MODELS["verification"]["default"]


# ---------------------------------------------------------------------------
# get_pricing
# ---------------------------------------------------------------------------


class TestGetPricing:
    def test_fallback_pricing_known_model(self):
        inp, out = get_pricing("openrouter/openai/gpt-4o-mini")
        assert inp == 0.15
        assert out == 0.60

    def test_unknown_model_returns_zero(self):
        inp, out = get_pricing("openrouter/unknown/model-xyz")
        assert inp == 0.0
        assert out == 0.0


# ---------------------------------------------------------------------------
# validate_models
# ---------------------------------------------------------------------------


class TestValidateModels:
    @pytest.mark.asyncio
    async def test_handles_network_failure(self):
        """validate_models returns an error dict when the network is down."""
        with patch("utils.model_registry.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.get.side_effect = httpx.ConnectError("connection refused")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            result = await validate_models()

            assert "error" in result
            assert result["valid"] == []
            assert result["invalid"] == []

    @pytest.mark.asyncio
    async def test_identifies_valid_models(self):
        """validate_models marks models as valid when found in catalog."""
        # Build a mock catalog with just gpt-4o-mini
        mock_catalog = {
            "data": [
                {
                    "id": "openai/gpt-4o-mini",
                    "pricing": {"prompt": "0.00000015", "completion": "0.0000006"},
                },
                {
                    "id": "anthropic/claude-sonnet-4.6",
                    "pricing": {"prompt": "0.000003", "completion": "0.000015"},
                },
                {
                    "id": "google/gemini-2.5-flash",
                    "pricing": {"prompt": "0.00000015", "completion": "0.0000006"},
                },
                {
                    "id": "x-ai/grok-4.1-fast",
                    "pricing": {"prompt": "0.0000002", "completion": "0.0000005"},
                },
                {
                    "id": "x-ai/grok-4.1-fast:online",
                    "pricing": {"prompt": "0.0000002", "completion": "0.0000005"},
                },
                {
                    "id": "meta-llama/llama-3.3-70b-instruct:free",
                    "pricing": {"prompt": "0", "completion": "0"},
                },
                {
                    "id": "anthropic/claude-opus-4.6",
                    "pricing": {"prompt": "0.000015", "completion": "0.000075"},
                },
                {
                    "id": "openai/o3-mini",
                    "pricing": {"prompt": "0.0000011", "completion": "0.0000044"},
                },
            ],
        }

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = mock_catalog
        mock_response.raise_for_status = MagicMock()

        with patch("utils.model_registry.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            result = await validate_models()

            assert len(result["valid"]) > 0
            assert result["catalog_size"] == len(mock_catalog["data"])
