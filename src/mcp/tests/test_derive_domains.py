# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for the DeriveDomainsJob + /graph/domains endpoint.

Covers:
- A8 tie-break table-test: one case per rung
- Orphan REMOVE (no MENTIONS path → fields absent)
- Run-twice idempotency
- /graph/domains shape including derived_at null (pre-job)
- Strata key extension shape
- A4: subcategory signal measurement (share of non-default sub_category)
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Pure fold tests (no I/O)
# ---------------------------------------------------------------------------
from app.processor.jobs.derive_domains import _fold_distributions, _pick_primary_domain


class TestPickPrimaryDomain:
    """A8 tie-break ladder — one case per rung."""

    def test_rung1_highest_count_wins(self):
        """Rung 1: highest distinct-artifact count."""
        d_map = {
            "coding": {"n": 5, "latest": "2026-01-01"},
            "research": {"n": 3, "latest": "2026-06-01"},
        }
        assert _pick_primary_domain(d_map) == "coding"

    def test_rung2_non_general_beats_general(self):
        """Rung 2: non-general wins when counts are tied."""
        d_map = {
            "general": {"n": 4, "latest": "2026-06-01"},
            "coding": {"n": 4, "latest": "2026-01-01"},
        }
        assert _pick_primary_domain(d_map) == "coding"

    def test_rung2_general_vs_general_skips_to_rung3(self):
        """If both tied candidates are non-general (no general in the set),
        rung 2 doesn't help — falls through to rung 3."""
        d_map = {
            "coding": {"n": 3, "latest": "2026-01-01"},
            "research": {"n": 3, "latest": "2026-06-01"},
        }
        # rung3: most recent wins — research has later date
        assert _pick_primary_domain(d_map) == "research"

    def test_rung3_most_recent_updated_at_wins(self):
        """Rung 3: when counts tied and neither/both are general, latest wins."""
        d_map = {
            "projects": {"n": 2, "latest": "2025-12-01"},
            "finance": {"n": 2, "latest": "2026-05-15"},
        }
        assert _pick_primary_domain(d_map) == "finance"

    def test_rung4_lexicographic_ascending(self):
        """Rung 4: identical counts, both non-general, same latest → lex asc."""
        d_map = {
            "research": {"n": 2, "latest": "2026-06-01"},
            "coding": {"n": 2, "latest": "2026-06-01"},
        }
        assert _pick_primary_domain(d_map) == "coding"

    def test_rung2_general_only_returns_general(self):
        """If only general exists, it's returned (no non-general alternative)."""
        d_map = {"general": {"n": 1, "latest": None}}
        assert _pick_primary_domain(d_map) == "general"

    def test_rung3_none_latest_sorts_last(self):
        """None latest treated as '' → sorts before any real date → loses rung 3."""
        d_map = {
            "coding": {"n": 2, "latest": None},
            "projects": {"n": 2, "latest": "2026-01-01"},
        }
        assert _pick_primary_domain(d_map) == "projects"


class TestFoldDistributions:
    """Integration tests for the full fold: orphan removal, idempotency."""

    def _make_row(self, cid: str, domain: str, sub: str, n: int, latest: str) -> dict:
        return {"cid": cid, "domain": domain, "sub": sub, "n": n, "latest": latest}

    def test_orphan_entities_in_remove_list(self):
        """Entities with no MENTIONS path land in orphan_ids, not update_rows."""
        mention_rows = [
            self._make_row("e1", "coding", "general", 2, "2026-01-01"),
        ]
        all_ids = {"e1", "e2_orphan", "e3_orphan"}
        update_rows, orphan_ids = _fold_distributions(mention_rows, all_ids)
        assert [r["cid"] for r in update_rows] == ["e1"]
        assert sorted(orphan_ids) == ["e2_orphan", "e3_orphan"]

    def test_orphan_never_coerced_to_general(self):
        """Orphan entities are not silently assigned 'general'."""
        update_rows, orphan_ids = _fold_distributions([], {"orphan_a"})
        assert orphan_ids == ["orphan_a"]
        assert update_rows == []

    def test_run_twice_idempotency(self):
        """Running fold twice on the same input produces identical output."""
        mention_rows = [
            self._make_row("e1", "research", "papers", 5, "2026-06-01"),
            self._make_row("e1", "coding", "general", 3, "2026-05-01"),
            self._make_row("e2", "general", "general", 1, "2026-01-01"),
        ]
        all_ids = {"e1", "e2"}
        r1, o1 = _fold_distributions(mention_rows, all_ids)
        r2, o2 = _fold_distributions(mention_rows, all_ids)
        # Sort by cid for stable comparison
        r1_sorted = sorted(r1, key=lambda x: x["cid"])
        r2_sorted = sorted(r2, key=lambda x: x["cid"])
        assert r1_sorted == r2_sorted
        assert o1 == o2

    def test_domain_mix_sorted_desc_then_name(self):
        """domain_mix JSON is sorted by count desc, then name asc for stability."""
        import json
        mention_rows = [
            self._make_row("e1", "coding", "general", 5, "2026-01-01"),
            self._make_row("e1", "research", "papers", 2, "2026-05-01"),
            self._make_row("e1", "finance", "general", 2, "2026-03-01"),
        ]
        update_rows, _ = _fold_distributions(mention_rows, {"e1"})
        assert len(update_rows) == 1
        mix = json.loads(update_rows[0]["domain_mix"])
        keys = list(mix.keys())
        # coding (5) first, then finance (2) < research (2) lex asc
        assert keys[0] == "coding"
        assert keys[1] == "finance"
        assert keys[2] == "research"

    def test_primary_subcategory_null_when_all_default(self):
        """primary_subcategory is None when all contributing artifacts have 'general' sub."""
        mention_rows = [
            self._make_row("e1", "research", "general", 3, "2026-01-01"),
        ]
        update_rows, _ = _fold_distributions(mention_rows, {"e1"})
        assert update_rows[0]["primary_subcategory"] is None

    def test_primary_subcategory_non_null_when_signal_present(self):
        """primary_subcategory is set when a non-default sub is the mode."""
        mention_rows = [
            self._make_row("e1", "research", "papers", 3, "2026-01-01"),
            self._make_row("e1", "research", "general", 1, "2026-02-01"),
        ]
        update_rows, _ = _fold_distributions(mention_rows, {"e1"})
        assert update_rows[0]["primary_subcategory"] == "papers"


# ---------------------------------------------------------------------------
# /graph/domains endpoint tests
# ---------------------------------------------------------------------------


@pytest.fixture
def domains_client():
    from app.routers.graph import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestGraphDomainsEndpoint:
    def _mock_neo4j(self, domain_counts_return: dict):
        mock_driver = MagicMock()
        return mock_driver, domain_counts_return

    def test_derived_at_null_pre_job(self, domains_client):
        """Before DeriveDomainsJob has run, derived_at is null."""
        with (
            patch("app.routers.graph.get_neo4j") as mock_get_neo4j,
            patch("app.db.neo4j.taxonomy.get_domain_counts") as mock_counts,
        ):
            mock_get_neo4j.return_value = MagicMock()
            mock_counts.return_value = {
                "domains": [],
                "uncategorized_entities": 0,
                "derived_at": None,
            }
            resp = domains_client.get("/graph/domains")
        assert resp.status_code == 200
        data = resp.json()
        assert data["derived_at"] is None
        assert data["uncategorized_entities"] == 0
        assert data["domains"] == []

    def test_response_shape_with_domains(self, domains_client):
        """Response includes name/icon/description/in_taxonomy/entity_count fields."""
        with (
            patch("app.routers.graph.get_neo4j") as mock_get_neo4j,
            patch("app.db.neo4j.taxonomy.get_domain_counts") as mock_counts,
        ):
            mock_get_neo4j.return_value = MagicMock()
            mock_counts.return_value = {
                "domains": [
                    {
                        "name": "research",
                        "icon": None,
                        "description": None,
                        "in_taxonomy": False,
                        "artifact_count": 5183,
                        "entity_count": 1515,
                        "sub_categories": [
                            {"name": "papers", "artifact_count": 812, "entity_count": 240},
                        ],
                    },
                    {
                        "name": "coding",
                        "icon": "code",
                        "description": "Code and software",
                        "in_taxonomy": True,
                        "artifact_count": 212,
                        "entity_count": 496,
                        "sub_categories": [],
                    },
                ],
                "uncategorized_entities": 32,
                "derived_at": "2026-06-10T03:02:11Z",
            }
            resp = domains_client.get("/graph/domains")
        assert resp.status_code == 200
        data = resp.json()
        assert data["derived_at"] == "2026-06-10T03:02:11Z"
        assert data["uncategorized_entities"] == 32
        assert len(data["domains"]) == 2
        research = data["domains"][0]
        assert research["name"] == "research"
        assert research["icon"] is None
        assert research["in_taxonomy"] is False
        assert research["entity_count"] == 1515
        assert len(research["sub_categories"]) == 1
        assert research["sub_categories"][0]["name"] == "papers"
        coding = data["domains"][1]
        assert coding["in_taxonomy"] is True
        assert coding["icon"] == "code"

    def test_neo4j_unavailable_degrades_gracefully(self, domains_client):
        """When Neo4j is unavailable, returns empty response, not 500."""
        with patch("app.routers.graph.get_neo4j") as mock_get_neo4j:
            mock_get_neo4j.return_value = None
            resp = domains_client.get("/graph/domains")
        assert resp.status_code == 200
        data = resp.json()
        assert data["derived_at"] is None
        assert data["domains"] == []


# ---------------------------------------------------------------------------
# Strata key extension shape test
# ---------------------------------------------------------------------------


def test_strata_series_row_has_domain_field():
    """StrataSeriesRow now carries the `domain` field — cross-agent contract."""
    from app.routers.graph import StrataSeriesRow

    row = StrataSeriesRow(
        community_id="1:42",
        entity_type="Person",
        domain="research",
        buckets=[0, 1, 2],
        unverified_buckets=[0, 0, 0],
    )
    assert row.domain == "research"


def test_strata_track_has_primary_domain_field():
    """StrataTrack now carries the `primary_domain` field."""
    from app.routers.graph import StrataTrack

    track = StrataTrack(
        canonical_id="foo",
        name="Foo Entity",
        entity_type="Topic",
        community_id="1:1",
        trust_state="unknown",
        first_seen="2026-01-01",
        rank=1,
        total_mentions=5,
        buckets=[1, 2, 2],
        primary_domain="research",
    )
    assert track.primary_domain == "research"

    # None is valid (pre-job or orphan)
    track2 = StrataTrack(
        canonical_id="bar",
        name="Bar",
        entity_type="Topic",
        community_id="1:2",
        trust_state="unknown",
        first_seen="2026-01-01",
        rank=2,
        total_mentions=1,
        buckets=[1],
    )
    assert track2.primary_domain is None


# ---------------------------------------------------------------------------
# A4: subcategory signal measurement
# ---------------------------------------------------------------------------


def test_a4_subcategory_signal_measurement():
    """Measure share of artifacts with non-default sub_category on live data.

    This test connects to the live Neo4j via docker exec if available,
    otherwise reports 'skipped — live DB unavailable'.  The result gates
    the frontend categories-footer subcategory segment (renders in v1 only
    if ≥1% of artifacts carry a non-default sub_category).

    Run manually via:
        docker exec ai-companion-mcp python -m pytest \
            src/mcp/tests/test_derive_domains.py::test_a4_subcategory_signal_measurement -v
    """

    try:
        from app.deps import get_neo4j

        driver = get_neo4j()
        if driver is None:
            pytest.skip("live Neo4j not available")
    except Exception:
        pytest.skip("live Neo4j not available")

    cypher = """
        MATCH (a:Artifact)
        RETURN
            count(a) AS total,
            count(CASE WHEN a.sub_category IS NOT NULL
                       AND a.sub_category <> 'general'
                       THEN 1 END) AS non_default
    """
    try:
        with driver.session() as session:
            row = session.run(cypher).single()
    except Exception as exc:
        pytest.skip(f"query failed: {exc}")

    total = int(row["total"]) if row else 0
    non_default = int(row["non_default"]) if row else 0

    if total == 0:
        pytest.skip("no artifacts in live DB")

    share_pct = (non_default / total) * 100
    gate_passes = share_pct >= 1.0

    # Report always — the test captures the measured number
    print(f"\nA4 subcategory signal: {non_default}/{total} = {share_pct:.2f}%")
    print(f"Frontend categories-footer subcategory segment: {'RENDERS' if gate_passes else 'DEFERRED to v1.1'}")

    # The test always passes — it's a measurement, not an assertion.
    # The number is the deliverable (per spec).
    assert total > 0  # sanity: DB is non-empty
