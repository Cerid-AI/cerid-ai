# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for Ollama model management — detection, recommendations, validation."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config.settings import PIPELINE_PROVIDERS
from utils.ollama_models import (
    RECOMMENDED_MODELS,
    check_model_availability,
    detect_available_models,
)


class TestRecommendedModels:
    """RECOMMENDED_MODELS covers all pipeline stages that can use Ollama."""

    def test_recommended_models_covers_all_stages(self):
        """Every PIPELINE_PROVIDERS stage (except always-bifrost stages) has a recommendation."""
        # Stages that are always routed to bifrost and never use Ollama
        bifrost_only = {"verification_complex", "chat_generation"}
        ollama_eligible = set(PIPELINE_PROVIDERS.keys()) - bifrost_only

        for stage in ollama_eligible:
            assert stage in RECOMMENDED_MODELS, (
                f"Pipeline stage '{stage}' has no entry in RECOMMENDED_MODELS"
            )


class TestDetectAvailableModels:
    """detect_available_models returns [] on failure, parsed list on success."""

    @pytest.mark.asyncio
    async def test_detect_returns_empty_on_failure(self):
        """When httpx raises, detect_available_models returns []."""
        with patch("utils.ollama_models.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.get.side_effect = OSError("connection refused")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            result = await detect_available_models()
            assert result == []

    @pytest.mark.asyncio
    async def test_detect_parses_response(self):
        """Successful API response is parsed into model dicts."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "models": [
                {"name": "llama3.2:3b", "size": 2_147_483_648, "modified_at": "2026-01-01"},
                {"name": "llama3.3:8b", "size": 4_294_967_296, "modified_at": "2026-02-01"},
            ]
        }

        with patch("utils.ollama_models.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            result = await detect_available_models()
            assert len(result) == 2
            assert result[0]["name"] == "llama3.2:3b"
            assert result[0]["size_gb"] == 2.0


class TestCheckAvailability:
    """check_model_availability reports missing and present models."""

    def test_check_availability_reports_missing(self):
        """When no models are available, all recommended are missing."""
        report = check_model_availability([])
        assert len(report["missing"]) == len(RECOMMENDED_MODELS)
        assert len(report["available"]) == 0
        assert len(report["warnings"]) == len(RECOMMENDED_MODELS)

    def test_check_availability_all_present(self):
        """When all recommended models are available, missing is empty."""
        available = [
            {"name": model, "size_gb": 1.0, "modified_at": ""}
            for model in set(RECOMMENDED_MODELS.values())
        ]
        report = check_model_availability(available)
        assert report["missing"] == []
        assert len(report["warnings"]) == 0

    def test_check_availability_partial(self):
        """Partial availability correctly splits present vs missing.

        The default model (from OLLAMA_DEFAULT_MODEL env or 'llama3.2:3b')
        is used for all pipeline stages. When only that model is available,
        nomic-embed-text (dedicated embedding model) should be missing.
        """
        from utils.ollama_models import get_recommended_models
        default_model = get_recommended_models()["claim_extraction"]
        available = [{"name": default_model, "size_gb": 2.0, "modified_at": ""}]
        report = check_model_availability(available)
        assert default_model in report["available"]
        assert "nomic-embed-text" in report["missing"]
