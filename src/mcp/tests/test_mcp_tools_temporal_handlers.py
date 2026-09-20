# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Handler-level coverage for the five temporal / hygiene MCP tools (F101).

``test_temporal.py`` and ``test_temporal_filter.py`` both import
``core.utils.temporal`` — a different module. Nothing anywhere called
``pkb_timeline``, ``pkb_trending``, ``pkb_revisit_due``,
``pkb_privacy_audit`` or ``pkb_quarantine``, including ``pkb_quarantine``,
which soft-deletes an artifact and schedules it for purge.

Two things here are worth more than the envelope shapes:

* ``pkb_timeline`` and ``pkb_trending`` interpolate ``granularity`` and a
  domain clause straight into the Cypher f-string. The validation in front of
  each is the only thing between a caller and the query, so it gets pinned
  explicitly rather than incidentally.
* ``pkb_privacy_audit`` must redact its own output. A leak-detector that
  prints the leak in full is a second leak.
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


class _FakeSession:
    def __init__(self, driver: "_FakeDriver") -> None:
        self._driver = driver

    def __enter__(self) -> "_FakeSession":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def run(self, query: str, **params: Any) -> list[dict[str, Any]]:
        self._driver.calls.append((query, params))
        if self._driver.raises is not None:
            raise self._driver.raises
        return self._driver.rows.pop(0) if self._driver.rows else []


class _FakeDriver:
    def __init__(
        self, rows: list[list[dict[str, Any]]] | None = None, raises: Exception | None = None
    ) -> None:
        self.rows = list(rows or [])
        self.raises = raises
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def session(self) -> _FakeSession:
        return _FakeSession(self)


def _neo4j(fake: _FakeDriver):
    return patch("app.mcp_tools.temporal.get_neo4j", return_value=fake)


# --------------------------------------------------------------- pkb_timeline

class TestPkbTimeline:
    @pytest.mark.asyncio
    async def test_buckets_and_totals_come_from_the_rows(self) -> None:
        from app.mcp_tools.temporal import pkb_timeline

        fake = _FakeDriver([[
            {"date": "2026-08-01", "artifacts": [{"id": "a1"}, {"id": "a2"}]},
            {"date": "2026-08-02", "artifacts": [{"id": "a3"}]},
        ]])
        with _neo4j(fake):
            out = await pkb_timeline(query="vesting", period="30d", granularity="week")

        query, params = fake.calls[0]
        assert params["q"] == "vesting"
        assert params["cap"] == 200
        # granularity is interpolated, not bound — so it must appear literally.
        assert "date.truncate('week'" in query
        assert out["total_artifacts"] == 3
        assert len(out["timeline"]) == 2

    @pytest.mark.asyncio
    @pytest.mark.parametrize("granularity", ["hour", "'); MATCH (n) DETACH DELETE n //", ""])
    async def test_rejects_granularity_outside_the_whitelist(self, granularity: str) -> None:
        """The whitelist is the only guard on an f-string interpolation into
        Cypher — nothing downstream escapes it."""
        from app.mcp_tools.temporal import pkb_timeline

        with pytest.raises(InvalidParamsError):
            await pkb_timeline(query="x", granularity=granularity)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("period", ["30", "30y", "abcd", ""])
    async def test_rejects_unparseable_period(self, period: str) -> None:
        from app.mcp_tools.temporal import pkb_timeline

        with pytest.raises(InvalidParamsError):
            await pkb_timeline(query="x", period=period)

    @pytest.mark.asyncio
    async def test_rejects_blank_query(self) -> None:
        from app.mcp_tools.temporal import pkb_timeline

        with pytest.raises(InvalidParamsError):
            await pkb_timeline(query="   ")

    @pytest.mark.asyncio
    async def test_neo4j_failure_surfaces_as_upstream_unavailable(self) -> None:
        from app.mcp_tools.temporal import pkb_timeline

        with _neo4j(_FakeDriver(raises=RuntimeError("refused"))), pytest.raises(
            UpstreamUnavailableError
        ):
            await pkb_timeline(query="x")


# --------------------------------------------------------------- pkb_trending

class TestPkbTrending:
    @pytest.mark.asyncio
    async def test_computes_the_envelope_from_row_counts(self) -> None:
        from app.mcp_tools.temporal import pkb_trending

        fake = _FakeDriver([[
            {"concept": "vesting", "current_count": 9, "prior_count": 3, "growth_factor": 3.0},
        ]])
        with _neo4j(fake):
            out = await pkb_trending(period="7d", k=5)

        query, params = fake.calls[0]
        assert params["k"] == 5
        # No domain given → no domain clause interpolated.
        assert "a.domain = $domain" not in query
        assert params["prior_start"] < params["current_start"]
        assert out["trending"][0] == {
            "concept": "vesting",
            "current_count": 9,
            "prior_count": 3,
            "growth_factor": 3.0,
        }

    @pytest.mark.asyncio
    async def test_domain_filter_is_interpolated_only_for_a_known_domain(self) -> None:
        import config
        from app.mcp_tools.temporal import pkb_trending

        domain = sorted(config.DOMAINS)[0]
        fake = _FakeDriver([[]])
        with _neo4j(fake):
            await pkb_trending(domain=domain)

        assert "a.domain = $domain" in fake.calls[0][0]
        assert fake.calls[0][1]["domain"] == domain

    @pytest.mark.asyncio
    async def test_rejects_an_unknown_domain(self) -> None:
        from app.mcp_tools.temporal import pkb_trending

        with pytest.raises(InvalidParamsError):
            await pkb_trending(domain="not-a-domain")

    @pytest.mark.asyncio
    async def test_k_is_clamped_rather_than_rejected(self) -> None:
        from app.mcp_tools.temporal import pkb_trending

        fake = _FakeDriver([[], []])
        with _neo4j(fake):
            hi = await pkb_trending(k=9999)
            lo = await pkb_trending(k=0)
        assert hi["k"] == 50
        assert lo["k"] == 1


# ------------------------------------------------------------ pkb_revisit_due

class TestPkbRevisitDue:
    @pytest.mark.asyncio
    async def test_cutoff_and_caps_reach_the_query(self) -> None:
        from app.mcp_tools.temporal import pkb_revisit_due

        fake = _FakeDriver([[]])
        with _neo4j(fake):
            await pkb_revisit_due(max_results=9999, min_days_since=30)

        params = fake.calls[0][1]
        assert params["cutoff"]
        assert params.get("limit", 100) <= 100

    @pytest.mark.asyncio
    async def test_rejects_an_unknown_domain(self) -> None:
        from app.mcp_tools.temporal import pkb_revisit_due

        with pytest.raises(InvalidParamsError):
            await pkb_revisit_due(domain="not-a-domain")


# ---------------------------------------------------------- pkb_privacy_audit

class TestPkbPrivacyAudit:
    @pytest.mark.asyncio
    async def test_finds_pii_in_the_artifact_summary(self) -> None:
        from app.mcp_tools.temporal import pkb_privacy_audit

        rows = [{"id": "a1", "filename": "notes.md", "summary": "reach me at bob@example.com"}]
        with _neo4j(_FakeDriver()), patch(
            "app.db.neo4j.list_artifacts", return_value=rows
        ):
            out = await pkb_privacy_audit(patterns=["email"])

        assert out["artifacts_scanned"] == 1
        assert out["patterns_checked"] == ["email"]
        assert [f["pattern"] for f in out["findings"]] == ["email"]
        assert out["findings"][0]["artifact_id"] == "a1"

    @pytest.mark.asyncio
    async def test_sample_is_truncated_so_the_report_is_not_a_second_leak(self) -> None:
        from app.mcp_tools.temporal import pkb_privacy_audit

        long_email = "a" * 60 + "@example.com"
        rows = [{"id": "a1", "filename": "x", "summary": long_email}]
        with _neo4j(_FakeDriver()), patch(
            "app.db.neo4j.list_artifacts", return_value=rows
        ):
            out = await pkb_privacy_audit(patterns=["email"])

        sample = out["findings"][0]["sample"]
        assert sample.endswith("...")
        assert len(sample) == 33
        assert long_email not in sample

    @pytest.mark.asyncio
    async def test_clean_artifact_produces_no_finding(self) -> None:
        from app.mcp_tools.temporal import pkb_privacy_audit

        rows = [{"id": "a1", "filename": "x", "summary": "nothing sensitive here"}]
        with _neo4j(_FakeDriver()), patch(
            "app.db.neo4j.list_artifacts", return_value=rows
        ):
            out = await pkb_privacy_audit(patterns=["email", "ssn_us"])

        assert out["findings"] == []
        assert out["artifacts_scanned"] == 1

    @pytest.mark.asyncio
    async def test_rejects_an_unknown_pattern_name(self) -> None:
        from app.mcp_tools.temporal import pkb_privacy_audit

        with pytest.raises(InvalidParamsError):
            await pkb_privacy_audit(patterns=["email", "not_a_pattern"])


# ------------------------------------------------------------- pkb_quarantine

class TestPkbQuarantine:
    @pytest.mark.asyncio
    async def test_routes_the_hide_through_the_lifecycle_coordinator(self) -> None:
        """The archived write and the cache bust live in hide_content; writing
        `a.archived = true` directly here would leave stale query caches."""
        from app.mcp_tools.temporal import pkb_quarantine

        with _neo4j(_FakeDriver()), patch(
            "app.services.content_lifecycle.hide_content", return_value=True
        ) as hide:
            out = await pkb_quarantine(artifact_id="a1", retention_days=30, reason="pii")

        extra = hide.call_args.kwargs["extra_props"]
        assert hide.call_args.args[0] == "a1"
        assert extra["quarantine_reason"] == "pii"
        assert extra["purge_after"] == out["purge_after"]
        assert extra["quarantined_at"] == out["quarantined_at"]
        # 30 days of retention, not 90 and not 0.
        assert out["purge_after"] > out["quarantined_at"]
        assert out["retention_days"] == 30

    @pytest.mark.asyncio
    async def test_missing_artifact_raises_resource_not_found(self) -> None:
        from app.mcp_tools.temporal import pkb_quarantine

        with _neo4j(_FakeDriver()), patch(
            "app.services.content_lifecycle.hide_content", return_value=False
        ), pytest.raises(ResourceNotFoundError):
            await pkb_quarantine(artifact_id="gone")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("days", [0, -1, 366, 10_000])
    async def test_rejects_retention_outside_the_documented_range(self, days: int) -> None:
        from app.mcp_tools.temporal import pkb_quarantine

        with pytest.raises(InvalidParamsError):
            await pkb_quarantine(artifact_id="a1", retention_days=days)

    @pytest.mark.asyncio
    async def test_rejects_overlong_reason(self) -> None:
        from app.mcp_tools.temporal import pkb_quarantine

        with pytest.raises(InvalidParamsError):
            await pkb_quarantine(artifact_id="a1", reason="x" * 501)

    @pytest.mark.asyncio
    async def test_backend_failure_surfaces_as_upstream_unavailable(self) -> None:
        from app.mcp_tools.temporal import pkb_quarantine

        with _neo4j(_FakeDriver()), patch(
            "app.services.content_lifecycle.hide_content",
            side_effect=RuntimeError("refused"),
        ), pytest.raises(UpstreamUnavailableError):
            await pkb_quarantine(artifact_id="a1")


# ------------------------------------------------------------ registry wiring

class TestRegistryDispatch:
    def test_every_temporal_tool_is_registered(self) -> None:
        import app.mcp_tools.temporal  # noqa: F401  — registration side effect
        from app.tool_registry import TOOL_REGISTRY

        for name in (
            "pkb_timeline",
            "pkb_trending",
            "pkb_revisit_due",
            "pkb_privacy_audit",
            "pkb_quarantine",
        ):
            assert name in TOOL_REGISTRY, f"{name} is not registered"

    @pytest.mark.asyncio
    async def test_dispatch_reaches_the_quarantine_handler(self) -> None:
        import app.mcp_tools.temporal  # noqa: F401

        with _neo4j(_FakeDriver()), patch(
            "app.services.content_lifecycle.hide_content", return_value=True
        ) as hide:
            out = await execute_registered_tool(
                "pkb_quarantine", {"artifact_id": "a1", "retention_days": 7}
            )
        assert hide.called
        assert out["retention_days"] == 7

    @pytest.mark.asyncio
    async def test_dispatch_propagates_invalid_params(self) -> None:
        import app.mcp_tools.temporal  # noqa: F401

        with pytest.raises(InvalidParamsError):
            await execute_registered_tool(
                "pkb_timeline", {"query": "x", "granularity": "century"}
            )
