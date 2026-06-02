# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Contract tests for ``POST /sdk/v1/llm/complete`` — slo_budget_ms behaviour.

The systemic shape under test:

  * ``slo_budget_ms`` is a wall-clock filter on tier eligibility — the
    smart_router won't silently downgrade to a faster tier behind the
    caller's back. If no tier fits, the handler converts the
    ``BudgetUnsatisfiableError`` into a 503 with a ``Retry-After`` header
    and a structured detail body so cerid-trading-agent can fail-fast and
    route to direct providers (xAI / Anthropic) instead of waiting on a
    slow tier.

  * On success, the response includes ``tier_p95_ms`` so the caller can
    tune adaptive client-side timeouts off the actual latency profile of
    the tier they routed to.

These tests run in the default ``test`` job (no live stack required).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from app.routers import sdk

    app = FastAPI()
    app.include_router(sdk.router)
    return TestClient(app, raise_server_exceptions=False)


class TestSdkLlmCompleteBudget:
    def test_returns_200_with_tier_p95_ms_on_success(self, client):
        """Happy path — handler surfaces tier_p95_ms so callers can tune
        adaptive timeouts off the actual tier latency profile."""
        from core.routing.smart_router import RouteDecision

        decision = RouteDecision(
            model="openai/gpt-4o-mini",
            provider="openrouter_paid",
            reason="dedicated verification model",
            estimated_cost_per_1k=0.00015,
            tier_p95_ms=10_000,
        )
        with patch(
            "core.utils.llm_client.route_and_call",
            new=AsyncMock(return_value=("ok", decision)),
        ):
            res = client.post(
                "/sdk/v1/llm/complete",
                json={
                    "messages": [{"role": "user", "content": "hi"}],
                    "task_type": "verification",
                    "slo_budget_ms": 12_000,
                },
            )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["content"] == "ok"
        assert body["tier_p95_ms"] == 10_000

    def test_explicit_null_slo_and_response_format_tolerated(self, client):
        """Clients (and JSON serializers) often send explicit null for optional
        fields; null must be treated as omitted, never a 422/500. Verifies the
        reported P0.4 500 is not reproducible with the current Pydantic v2 model
        (slo_budget_ms / response_format are `... | None` with default None)."""
        from core.routing.smart_router import RouteDecision

        decision = RouteDecision(
            model="m", provider="ollama", reason="r",
            estimated_cost_per_1k=0.0, tier_p95_ms=5000,
        )
        with patch(
            "core.utils.llm_client.route_and_call",
            new=AsyncMock(return_value=("ok", decision)),
        ):
            res = client.post(
                "/sdk/v1/llm/complete",
                json={
                    "messages": [{"role": "user", "content": "hi"}],
                    "task_type": "internal",
                    "slo_budget_ms": None,
                    "response_format": None,
                },
            )
        assert res.status_code == 200, res.text
        assert res.json()["content"] == "ok"

    def test_returns_503_with_retry_after_when_budget_unsatisfiable(self, client):
        """When the smart_router rejects the budget, the handler must
        return 503 + Retry-After + structured detail. Silent downgrades
        would hide quality drops from the caller."""
        from core.routing.smart_router import BudgetUnsatisfiableError

        async def _budget_fail(*_args, **_kwargs):
            raise BudgetUnsatisfiableError(
                retry_after_ms=75_000,
                eligible_tier="verification_expert",
                floor_p95_ms=75_000,
            )

        with patch(
            "core.utils.llm_client.route_and_call",
            new=_budget_fail,
        ):
            res = client.post(
                "/sdk/v1/llm/complete",
                json={
                    "messages": [{"role": "user", "content": "verify this"}],
                    "task_type": "verification_expert",
                    "slo_budget_ms": 2_000,
                },
            )
        assert res.status_code == 503
        # Retry-After in seconds per RFC 7231
        assert res.headers.get("Retry-After") == "75"
        body = res.json()
        detail = body["detail"]
        assert detail["error"] == "slo_budget_exceeded"
        assert detail["retry_after_ms"] == 75_000
        assert detail["floor_p95_ms"] == 75_000
        assert detail["eligible_tier"] == "verification_expert"

    def test_no_budget_passes_through_unchanged(self, client):
        """Omitting slo_budget_ms keeps the legacy behaviour — the param
        flows to the router as None and no filter is applied."""
        from core.routing.smart_router import RouteDecision

        decision = RouteDecision(
            model="openai/gpt-4o-mini",
            provider="openrouter_paid",
            reason="dedicated verification model",
            estimated_cost_per_1k=0.00015,
            tier_p95_ms=10_000,
        )
        with patch(
            "core.utils.llm_client.route_and_call",
            new=AsyncMock(return_value=("ok", decision)),
        ) as mock_route:
            res = client.post(
                "/sdk/v1/llm/complete",
                json={
                    "messages": [{"role": "user", "content": "hi"}],
                    "task_type": "verification",
                },
            )
        assert res.status_code == 200
        # Verify the param was forwarded as None to the router
        kwargs = mock_route.call_args.kwargs
        assert kwargs["slo_budget_ms"] is None

    def test_openapi_exposes_slo_budget_ms_constraints(self, client):
        """SDK consumers read the OpenAPI spec to generate clients. The
        ``ge=100, le=600_000`` bounds must surface so generated clients
        validate up-front rather than hitting 422s in production."""
        spec = client.get("/openapi.json").json()
        schema = spec["components"]["schemas"]["SDKLLMCompleteRequest"]
        field = schema["properties"]["slo_budget_ms"]
        # Pydantic emits ``anyOf: [{type: integer, minimum: 100, maximum: 600000}, {type: null}]``
        # for ``int | None`` — accept either flat or anyOf shape.
        candidates = field.get("anyOf", [field])
        int_branch = next((c for c in candidates if c.get("type") == "integer"), None)
        assert int_branch is not None, f"slo_budget_ms missing integer schema: {field}"
        assert int_branch["minimum"] == 100
        assert int_branch["maximum"] == 600_000
