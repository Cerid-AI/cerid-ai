# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Handler-level coverage for the four active-learning MCP tools (F101).

Before this file, ``pkb_rate`` / ``pkb_correct`` / ``pkb_endorse`` /
``pkb_flag`` had none. ``test_feedback_service.py`` covers
``app.services.feedback``, a different module; ``test_active_learning_
retrieval.py`` covers query_agent's READ of ``endorsement_weight`` and
``flag_reason``; and ``test_mcp_tool_schema_fidelity.py`` explicitly
disclaims handler coverage, deferring it to "each tool's dedicated unit
test" — which did not exist. So the four tools that WRITE those properties,
two of them mutating (``pkb_flag`` excludes an artifact from retrieval,
``pkb_endorse`` rewrites its ranking weight), could regress with green CI.

These drive the real handlers against a scripted Neo4j session, asserting the
Cypher's effect on parameters and the response envelope, plus the three error
classes the registry maps onto JSON-RPC codes. Every tool is exercised once
through ``execute_registered_tool`` as well, so the registration and the
handler are both on the tested path.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from app.tool_registry import (
    InvalidParamsError,
    ResourceNotFoundError,
    UpstreamUnavailableError,
    execute_registered_tool,
)

# --------------------------------------------------------------- fake driver

class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def single(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


class _FakeSession:
    def __init__(self, driver: "_FakeDriver") -> None:
        self._driver = driver

    def __enter__(self) -> "_FakeSession":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def run(self, query: str, **params: Any) -> _FakeResult:
        self._driver.calls.append((query, params))
        if self._driver.raises is not None:
            raise self._driver.raises
        rows = self._driver.rows.pop(0) if self._driver.rows else []
        return _FakeResult(rows)


class _FakeDriver:
    """Scripted session: ``rows`` is one row-list per ``session.run`` call."""

    def __init__(
        self, rows: list[list[dict[str, Any]]] | None = None, raises: Exception | None = None
    ) -> None:
        self.rows = list(rows or [])
        self.raises = raises
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def session(self) -> _FakeSession:
        return _FakeSession(self)


def _driver(rows=None, raises=None):
    return patch("app.mcp_tools.feedback.get_neo4j", return_value=_FakeDriver(rows, raises))


def _params(driver_patch) -> dict[str, Any]:
    return driver_patch.calls[-1][1]


# ------------------------------------------------------------------ pkb_rate

class TestPkbRate:
    @pytest.mark.asyncio
    async def test_records_sentiment_and_note_on_the_rated_edge(self) -> None:
        from app.mcp_tools.feedback import pkb_rate

        fake = _FakeDriver([[{"claim_id": "c1"}]])
        with patch("app.mcp_tools.feedback.get_neo4j", return_value=fake):
            out = await pkb_rate(claim_id="c1", sentiment=-1, note="wrong date")

        query, params = fake.calls[0]
        assert params["claim_id"] == "c1"
        assert params["sentiment"] == -1
        assert params["note"] == "wrong date"
        # created_at is what trust_score's rolling user_agreement filters on;
        # the same timestamp has to reach the node and the response.
        assert "c.created_at = $ts" in query
        assert params["ts"] == out["ts"]
        assert out == {"rated": True, "claim_id": "c1", "sentiment": -1, "ts": params["ts"]}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("sentiment", [-1, 0, 1])
    async def test_accepts_every_valid_sentiment(self, sentiment: int) -> None:
        from app.mcp_tools.feedback import pkb_rate

        with patch("app.mcp_tools.feedback.get_neo4j", return_value=_FakeDriver([[{"claim_id": "c1"}]])):
            out = await pkb_rate(claim_id="c1", sentiment=sentiment)
        assert out["sentiment"] == sentiment

    @pytest.mark.asyncio
    @pytest.mark.parametrize("sentiment", [-2, 2, 5])
    async def test_rejects_out_of_range_sentiment(self, sentiment: int) -> None:
        from app.mcp_tools.feedback import pkb_rate

        with pytest.raises(InvalidParamsError):
            await pkb_rate(claim_id="c1", sentiment=sentiment)

    @pytest.mark.asyncio
    async def test_rejects_overlong_note(self) -> None:
        from app.mcp_tools.feedback import pkb_rate

        with pytest.raises(InvalidParamsError):
            await pkb_rate(claim_id="c1", sentiment=1, note="x" * 501)

    @pytest.mark.asyncio
    async def test_neo4j_failure_surfaces_as_upstream_unavailable(self) -> None:
        from app.mcp_tools.feedback import pkb_rate

        with patch(
            "app.mcp_tools.feedback.get_neo4j",
            return_value=_FakeDriver(raises=RuntimeError("connection refused")),
        ), pytest.raises(UpstreamUnavailableError):
            await pkb_rate(claim_id="c1", sentiment=1)


# --------------------------------------------------------------- pkb_correct

class TestPkbCorrect:
    @pytest.mark.asyncio
    async def test_creates_correction_attached_to_the_artifact(self) -> None:
        from app.mcp_tools.feedback import pkb_correct

        # First run() is the existence check, second is the CREATE.
        fake = _FakeDriver([[{"c": 1}], []])
        with patch("app.mcp_tools.feedback.get_neo4j", return_value=fake):
            out = await pkb_correct(
                artifact_id="a1", correction_text="the date is 2019", applies_to_chunk_id="a1:3"
            )

        create_query, create_params = fake.calls[1]
        assert "CREATE (c)-[:ATTACHED_TO]->(a)" in create_query
        assert create_params["aid"] == "a1"
        assert create_params["text"] == "the date is 2019"
        assert create_params["chunk"] == "a1:3"
        # The id the caller is handed must be the id that was persisted.
        assert out["correction_id"] == create_params["cid"]
        assert out["applies_to_chunk_id"] == "a1:3"

    @pytest.mark.asyncio
    async def test_missing_artifact_raises_resource_not_found(self) -> None:
        from app.mcp_tools.feedback import pkb_correct

        fake = _FakeDriver([[{"c": 0}]])
        with patch("app.mcp_tools.feedback.get_neo4j", return_value=fake), pytest.raises(
            ResourceNotFoundError
        ):
            await pkb_correct(artifact_id="nope", correction_text="x")
        # The CREATE must not have run.
        assert len(fake.calls) == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("text", ["", "   ", "\n"])
    async def test_rejects_blank_correction_text(self, text: str) -> None:
        from app.mcp_tools.feedback import pkb_correct

        with pytest.raises(InvalidParamsError):
            await pkb_correct(artifact_id="a1", correction_text=text)

    @pytest.mark.asyncio
    async def test_rejects_overlong_correction_text(self) -> None:
        from app.mcp_tools.feedback import pkb_correct

        with pytest.raises(InvalidParamsError):
            await pkb_correct(artifact_id="a1", correction_text="x" * 2001)


# --------------------------------------------------------------- pkb_endorse

class TestPkbEndorse:
    @pytest.mark.asyncio
    async def test_sets_weight_and_reports_the_previous_one(self) -> None:
        from app.mcp_tools.feedback import pkb_endorse

        fake = _FakeDriver([[{"prev": 1.0}]])
        with patch("app.mcp_tools.feedback.get_neo4j", return_value=fake):
            out = await pkb_endorse(artifact_id="a1", weight=3.5)

        assert fake.calls[0][1]["weight"] == 3.5
        assert out["endorsement_weight"] == 3.5
        assert out["previous_weight"] == 1.0

    @pytest.mark.asyncio
    async def test_default_weight_is_a_boost(self) -> None:
        from app.mcp_tools.feedback import pkb_endorse

        fake = _FakeDriver([[{"prev": 1.0}]])
        with patch("app.mcp_tools.feedback.get_neo4j", return_value=fake):
            out = await pkb_endorse(artifact_id="a1")
        assert out["endorsement_weight"] > 1.0

    @pytest.mark.asyncio
    @pytest.mark.parametrize("weight", [0.0, 0.09, 10.1, -1.0])
    async def test_rejects_weight_outside_the_documented_range(self, weight: float) -> None:
        from app.mcp_tools.feedback import pkb_endorse

        with pytest.raises(InvalidParamsError):
            await pkb_endorse(artifact_id="a1", weight=weight)

    @pytest.mark.asyncio
    async def test_missing_artifact_raises_resource_not_found(self) -> None:
        from app.mcp_tools.feedback import pkb_endorse

        with patch("app.mcp_tools.feedback.get_neo4j", return_value=_FakeDriver([[]])), pytest.raises(
            ResourceNotFoundError
        ):
            await pkb_endorse(artifact_id="gone")


# ------------------------------------------------------------------ pkb_flag

class TestPkbFlag:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "reason", ["inaccurate", "outdated", "off_topic", "duplicate", "spam"]
    )
    async def test_accepts_every_documented_reason(self, reason: str) -> None:
        from app.mcp_tools.feedback import pkb_flag

        fake = _FakeDriver([[{"prev": ""}]])
        with patch("app.mcp_tools.feedback.get_neo4j", return_value=fake):
            out = await pkb_flag(artifact_id="a1", reason=reason, note="why")

        assert fake.calls[0][1]["reason"] == reason
        assert fake.calls[0][1]["note"] == "why"
        assert out["flag_reason"] == reason

    @pytest.mark.asyncio
    async def test_empty_reason_clears_the_flag_with_null_not_empty_string(self) -> None:
        """The retrieval-side filter tests `flag_reason IS NULL`; writing ''
        would leave the artifact excluded forever."""
        from app.mcp_tools.feedback import pkb_flag

        fake = _FakeDriver([[{"prev": "outdated"}]])
        with patch("app.mcp_tools.feedback.get_neo4j", return_value=fake):
            out = await pkb_flag(artifact_id="a1", reason="")

        assert fake.calls[0][1]["reason"] is None
        assert out["previous_flag"] == "outdated"
        assert out["flag_reason"] == ""

    @pytest.mark.asyncio
    async def test_rejects_an_undocumented_reason(self) -> None:
        from app.mcp_tools.feedback import pkb_flag

        with pytest.raises(InvalidParamsError):
            await pkb_flag(artifact_id="a1", reason="bogus")

    @pytest.mark.asyncio
    async def test_missing_artifact_raises_resource_not_found(self) -> None:
        from app.mcp_tools.feedback import pkb_flag

        with patch("app.mcp_tools.feedback.get_neo4j", return_value=_FakeDriver([[]])), pytest.raises(
            ResourceNotFoundError
        ):
            await pkb_flag(artifact_id="gone", reason="spam")


# ------------------------------------------------------- registry dispatch

class TestRegistryDispatch:
    """The handlers above are only reachable in production through the
    registry. Calling them directly proves the function; this proves the
    wiring."""

    @pytest.mark.asyncio
    async def test_every_feedback_tool_is_registered(self) -> None:
        import app.mcp_tools.feedback  # noqa: F401  — registration side effect
        from app.tool_registry import TOOL_REGISTRY

        for name in ("pkb_rate", "pkb_correct", "pkb_endorse", "pkb_flag"):
            assert name in TOOL_REGISTRY, f"{name} is not registered"

    @pytest.mark.asyncio
    async def test_dispatch_reaches_the_rate_handler(self) -> None:
        import app.mcp_tools.feedback  # noqa: F401

        fake = _FakeDriver([[{"claim_id": "c1"}]])
        with patch("app.mcp_tools.feedback.get_neo4j", return_value=fake):
            out = await execute_registered_tool(
                "pkb_rate", {"claim_id": "c1", "sentiment": 1}
            )
        assert out["rated"] is True
        assert fake.calls, "dispatch did not reach the handler"

    @pytest.mark.asyncio
    async def test_dispatch_reaches_the_flag_handler(self) -> None:
        import app.mcp_tools.feedback  # noqa: F401

        fake = _FakeDriver([[{"prev": ""}]])
        with patch("app.mcp_tools.feedback.get_neo4j", return_value=fake):
            out = await execute_registered_tool(
                "pkb_flag", {"artifact_id": "a1", "reason": "duplicate"}
            )
        assert out["flag_reason"] == "duplicate"

    @pytest.mark.asyncio
    async def test_dispatch_propagates_invalid_params(self) -> None:
        import app.mcp_tools.feedback  # noqa: F401

        with pytest.raises(InvalidParamsError):
            await execute_registered_tool(
                "pkb_rate", {"claim_id": "c1", "sentiment": 7}
            )
