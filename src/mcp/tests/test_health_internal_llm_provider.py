# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""``/health`` and ``/health/status`` must name the configured local backend.

The status strip labels the local-pipeline indicator from
``internal_llm_provider``. The field was never in either payload, so the strip
fell back to its default and read "Ollama: active" on a quenchforge host.

The value comes from ``core.routing.provider_state.active_provider()`` — the
one runtime authority for provider identity, and the only sanctioned reader of
``INTERNAL_LLM_PROVIDER`` — so a runtime switch is reflected without a restart.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.routers.health import degradation_status, health_check


@pytest.fixture
def _connected_services():
    with (
        patch("app.routers.health.get_redis", return_value=MagicMock()),
        patch("app.routers.health.get_chroma", return_value=MagicMock()),
        patch("app.routers.health.get_neo4j", return_value=None),
    ):
        yield


@pytest.mark.parametrize("configured", ["quenchforge", "ollama", "openrouter"])
def test_health_check_reports_the_configured_provider(
    monkeypatch, _connected_services, configured,
):
    monkeypatch.setenv("INTERNAL_LLM_PROVIDER", configured)

    assert health_check()["internal_llm_provider"] == configured


def test_provider_is_normalised(monkeypatch, _connected_services):
    monkeypatch.setenv("INTERNAL_LLM_PROVIDER", "  QuenchForge  ")

    assert health_check()["internal_llm_provider"] == "quenchforge"


def test_health_status_reports_the_configured_provider(monkeypatch, _connected_services):
    monkeypatch.setenv("INTERNAL_LLM_PROVIDER", "quenchforge")

    assert degradation_status()["internal_llm_provider"] == "quenchforge"
