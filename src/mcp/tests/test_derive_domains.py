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

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Pure fold tests (no I/O)
# ---------------------------------------------------------------------------
from app.processor.jobs.derive_domains import (
    _fold_distributions,
    _pick_primary_domain,
    _recency_decay,
)

# Fixed injected clock — the fold takes `now` explicitly so salience (which
# decays with recency) is deterministic across runs.
_NOW = datetime(2026, 6, 13, tzinfo=timezone.utc)


class TestPickPrimaryDomain:
    """A8 tie-break ladder — one case per rung. The picker keys rung 1 on
    salience (Slice 6.1); rungs 2-4 (non-general → latest → lex) are unchanged.
    Each slot carries {n, latest, salience}; n is retained for downstream
    domain_mix but is no longer the rung-1 selector."""

    def test_recency_decay_neutral_on_bad_half_life(self):
        """A misconfigured (zero/negative) half-life returns neutral 0.5 instead
        of crashing the job or inverting decay to growth (boundary guard)."""
        assert _recency_decay("2026-05-01", _NOW, 0) == 0.5
        assert _recency_decay("2026-05-01", _NOW, -30) == 0.5
        # sane half-life still decays a past date below 1.0
        assert 0.0 < _recency_decay("2026-05-01", _NOW, 30) < 1.0

    def test_rung1_highest_salience_wins(self):
        """Rung 1: highest salience (not raw count)."""
        d_map = {
            "coding": {"n": 5, "latest": "2026-01-01", "salience": 5.0},
            "research": {"n": 3, "latest": "2026-06-01", "salience": 3.0},
        }
        assert _pick_primary_domain(d_map) == "coding"

    def test_rung1_salience_overrides_raw_count(self):
        """A lower-count domain with higher salience beats a high-count one —
        this is the whole point of 6.1 (distinctiveness/quality reweighting)."""
        d_map = {
            "general": {"n": 60, "latest": "2026-06-01", "salience": 11.25},
            "finance": {"n": 30, "latest": "2026-06-01", "salience": 45.0},
        }
        assert _pick_primary_domain(d_map) == "finance"

    def test_rung2_non_general_beats_general(self):
        """Rung 2: non-general wins when salience is tied."""
        d_map = {
            "general": {"n": 4, "latest": "2026-06-01", "salience": 4.0},
            "coding": {"n": 4, "latest": "2026-01-01", "salience": 4.0},
        }
        assert _pick_primary_domain(d_map) == "coding"

    def test_rung2_general_vs_general_skips_to_rung3(self):
        """If both tied candidates are non-general (no general in the set),
        rung 2 doesn't help — falls through to rung 3."""
        d_map = {
            "coding": {"n": 3, "latest": "2026-01-01", "salience": 3.0},
            "research": {"n": 3, "latest": "2026-06-01", "salience": 3.0},
        }
        # rung3: most recent wins — research has later date
        assert _pick_primary_domain(d_map) == "research"

    def test_rung3_most_recent_updated_at_wins(self):
        """Rung 3: when salience tied and neither/both are general, latest wins."""
        d_map = {
            "projects": {"n": 2, "latest": "2025-12-01", "salience": 2.0},
            "finance": {"n": 2, "latest": "2026-05-15", "salience": 2.0},
        }
        assert _pick_primary_domain(d_map) == "finance"

    def test_rung4_lexicographic_ascending(self):
        """Rung 4: identical salience, both non-general, same latest → lex asc."""
        d_map = {
            "research": {"n": 2, "latest": "2026-06-01", "salience": 2.0},
            "coding": {"n": 2, "latest": "2026-06-01", "salience": 2.0},
        }
        assert _pick_primary_domain(d_map) == "coding"

    def test_rung2_general_only_returns_general(self):
        """If only general exists, it's returned (no non-general alternative)."""
        d_map = {"general": {"n": 1, "latest": None, "salience": 0.125}}
        assert _pick_primary_domain(d_map) == "general"

    def test_rung3_none_latest_sorts_last(self):
        """None latest treated as '' → sorts before any real date → loses rung 3."""
        d_map = {
            "coding": {"n": 2, "latest": None, "salience": 2.0},
            "projects": {"n": 2, "latest": "2026-01-01", "salience": 2.0},
        }
        assert _pick_primary_domain(d_map) == "projects"


class TestFoldDistributions:
    """Integration tests for the full fold: orphan removal, idempotency."""

    def _make_row(
        self, cid: str, domain: str, sub: str, n: int, latest: str,
        qsum: float | None = None,
    ) -> dict:
        # qsum defaults to None to exercise the pre-quality fallback-to-n path
        # (legacy artifacts have no quality_score property → NULL aggregate).
        return {"cid": cid, "domain": domain, "sub": sub, "n": n, "latest": latest, "qsum": qsum}

    def _make_tag_row(
        self, cid: str, tag: str, n: int, qsum: float | None, latest: str,
    ) -> dict:
        return {"cid": cid, "tag": tag, "n": n, "qsum": qsum, "latest": latest}

    def test_orphan_entities_in_remove_list(self):
        """Entities with no MENTIONS path land in orphan_ids, not update_rows."""
        mention_rows = [
            self._make_row("e1", "coding", "general", 2, "2026-01-01"),
        ]
        all_ids = {"e1", "e2_orphan", "e3_orphan"}
        update_rows, orphan_ids = _fold_distributions(mention_rows, all_ids, _NOW)
        assert [r["cid"] for r in update_rows] == ["e1"]
        assert sorted(orphan_ids) == ["e2_orphan", "e3_orphan"]

    def test_orphan_never_coerced_to_general(self):
        """Orphan entities are not silently assigned 'general'."""
        update_rows, orphan_ids = _fold_distributions([], {"orphan_a"}, _NOW)
        assert orphan_ids == ["orphan_a"]
        assert update_rows == []

    def test_run_twice_idempotency(self):
        """Running fold twice on the same input + same `now` produces identical
        output — the job's core property. Salience is rounded so floats don't
        drift in the last digits across runs."""
        mention_rows = [
            self._make_row("e1", "research", "papers", 5, "2026-06-01"),
            self._make_row("e1", "coding", "general", 3, "2026-05-01"),
            self._make_row("e2", "general", "general", 1, "2026-01-01"),
        ]
        all_ids = {"e1", "e2"}
        r1, o1 = _fold_distributions(mention_rows, all_ids, _NOW)
        r2, o2 = _fold_distributions(mention_rows, all_ids, _NOW)
        # Sort by cid for stable comparison
        r1_sorted = sorted(r1, key=lambda x: x["cid"])
        r2_sorted = sorted(r2, key=lambda x: x["cid"])
        assert r1_sorted == r2_sorted  # includes domain_salience JSON byte-equality
        assert o1 == o2

    def test_domain_mix_sorted_desc_then_name(self):
        """domain_mix JSON stays raw integer counts, sorted by count desc then
        name asc (salience rides in a separate field, NOT in domain_mix)."""
        mention_rows = [
            self._make_row("e1", "coding", "general", 5, "2026-01-01"),
            self._make_row("e1", "research", "papers", 2, "2026-05-01"),
            self._make_row("e1", "finance", "general", 2, "2026-03-01"),
        ]
        update_rows, _ = _fold_distributions(mention_rows, {"e1"}, _NOW)
        assert len(update_rows) == 1
        mix = json.loads(update_rows[0]["domain_mix"])
        keys = list(mix.keys())
        # coding (5) first, then finance (2) < research (2) lex asc
        assert keys[0] == "coding"
        assert keys[1] == "finance"
        assert keys[2] == "research"
        # domain_mix values remain ints, never floats
        assert all(isinstance(v, int) for v in mix.values())

    def test_domain_salience_structure(self):
        """domain_salience is a separate float map: keys ⊆ domain_mix keys,
        sorted desc, every value rounded to <=4 decimal places."""
        mention_rows = [
            self._make_row("e1", "coding", "general", 5, "2026-06-01"),
            self._make_row("e1", "research", "papers", 2, "2026-05-01"),
            self._make_row("e1", "finance", "general", 2, "2026-03-01"),
        ]
        update_rows, _ = _fold_distributions(mention_rows, {"e1"}, _NOW)
        row = update_rows[0]
        sal = json.loads(row["domain_salience"])
        mix = json.loads(row["domain_mix"])
        # keys are a subset of domain_mix keys (same domains, no extras)
        assert set(sal.keys()) <= set(mix.keys())
        assert set(sal.keys()) == {"coding", "research", "finance"}
        # sorted descending by salience
        values = list(sal.values())
        assert values == sorted(values, reverse=True)
        # rounded to <=4 dp — idempotency guard
        for v in sal.values():
            assert round(v, 4) == v
        # primary_domain is the salience argmax
        assert row["primary_domain"] == max(sal, key=lambda k: sal[k])

    def test_qsum_null_falls_back_to_count(self):
        """A pre-quality artifact (qsum NULL) still contributes via the raw
        count fallback — its entity gets a non-zero salience and a domain."""
        rows_null = [self._make_row("legacy", "finance", "general", 4, "2026-06-01", qsum=None)]
        update_rows, _ = _fold_distributions(rows_null, {"legacy"}, _NOW)
        sal = json.loads(update_rows[0]["domain_salience"])
        assert update_rows[0]["primary_domain"] == "finance"
        assert sal["finance"] > 0

    def test_quality_mass_outweighs_raw_count(self):
        """With equal counts + recency (so distinctiveness cancels), summed
        quality mass drives salience — high-quality mentions outrank
        low-quality ones of the same volume."""
        mention_rows = [
            # equal counts (4 each), equal recency → only quality differs
            self._make_row("e1", "coding", "general", 4, "2026-06-01", qsum=0.4),
            self._make_row("e1", "finance", "general", 4, "2026-06-01", qsum=3.6),
        ]
        update_rows, _ = _fold_distributions(mention_rows, {"e1"}, _NOW)
        assert update_rows[0]["primary_domain"] == "finance"

    def test_specificity_downweights_general(self):
        """A high-count 'general' loses to a lower-count specific domain because
        specificity down-weights ambient/uncategorised domains (60 generic < 30
        finance)."""
        mention_rows = [
            self._make_row("e1", "general", "general", 60, "2026-06-01"),
            self._make_row("e1", "finance", "general", 30, "2026-06-01"),
        ]
        update_rows, _ = _fold_distributions(mention_rows, {"e1"}, _NOW)
        # raw count would pick general (60 > 30); salience picks finance
        assert update_rows[0]["primary_domain"] == "finance"

    def test_distinctiveness_rewards_rare_domain(self):
        """Equal local counts, but a globally-rare domain outranks a globally-
        common one (a mention of the rare domain is more telling)."""
        mention_rows = [
            # e1 mentions coding and finance equally (same count, same recency)
            self._make_row("e1", "coding", "general", 5, "2026-06-01"),
            self._make_row("e1", "finance", "general", 5, "2026-06-01"),
            # many other entities make 'coding' globally common, 'finance' rare
            self._make_row("e2", "coding", "general", 95, "2026-06-01"),
        ]
        update_rows, _ = _fold_distributions(mention_rows, {"e1", "e2"}, _NOW)
        e1 = next(r for r in update_rows if r["cid"] == "e1")
        assert e1["primary_domain"] == "finance"

    def test_same_now_is_deterministic(self):
        """The explicit-`now` contract: identical rows + identical now produce
        byte-identical domain_salience (no wall-clock read in the fold)."""
        mention_rows = [
            self._make_row("e1", "finance", "investments", 3, "2026-05-01", qsum=2.1),
        ]
        a, _ = _fold_distributions(mention_rows, {"e1"}, _NOW)
        b, _ = _fold_distributions(mention_rows, {"e1"}, _NOW)
        assert a[0]["domain_salience"] == b[0]["domain_salience"]

    # --- Slice 6.3: top_tags rollup ---------------------------------------

    def test_top_tags_vocabulary_only(self):
        """Only controlled-vocabulary tags surface; free-form tags are dropped
        even when they out-count a vocabulary tag."""
        mention_rows = [self._make_row("e1", "coding", "general", 3, "2026-06-01")]
        tag_rows = [
            self._make_tag_row("e1", "python", 3, None, "2026-06-01"),
            self._make_tag_row("e1", "some-freeform-xyz", 9, None, "2026-06-01"),
        ]
        update_rows, _ = _fold_distributions(mention_rows, {"e1"}, _NOW, tag_rows)
        assert json.loads(update_rows[0]["top_tags"]) == ["python"]

    def test_top_tags_salience_weighted(self):
        """Tags rank by quality-and-recency weight, not raw count — a
        high-quality tag outranks a higher-count low-quality one."""
        mention_rows = [self._make_row("e1", "coding", "general", 3, "2026-06-01")]
        tag_rows = [
            self._make_tag_row("e1", "docker", 2, 1.8, "2026-06-01"),  # high quality
            self._make_tag_row("e1", "api", 5, 0.5, "2026-06-01"),     # high count, low quality
        ]
        update_rows, _ = _fold_distributions(mention_rows, {"e1"}, _NOW, tag_rows)
        assert json.loads(update_rows[0]["top_tags"])[0] == "docker"

    def test_top_tags_capped_at_n(self):
        """At most _TOP_TAGS_N (5) tags surface; the lowest-weight one drops."""
        mention_rows = [self._make_row("e1", "coding", "general", 6, "2026-06-01")]
        tags = ["python", "docker", "api", "testing", "git", "security"]
        tag_rows = [
            self._make_tag_row("e1", t, n, None, "2026-06-01")
            for n, t in zip([60, 50, 40, 30, 20, 10], tags)
        ]
        update_rows, _ = _fold_distributions(mention_rows, {"e1"}, _NOW, tag_rows)
        top = json.loads(update_rows[0]["top_tags"])
        assert len(top) == 5
        assert top[0] == "python"
        assert "security" not in top  # lowest weight dropped at the N cap

    def test_top_tags_empty_without_tag_rows(self):
        """No tag_rows → empty top_tags (honest absence, not omitted)."""
        mention_rows = [self._make_row("e1", "coding", "general", 3, "2026-06-01")]
        update_rows, _ = _fold_distributions(mention_rows, {"e1"}, _NOW)
        assert json.loads(update_rows[0]["top_tags"]) == []

    def test_top_tags_deterministic_with_lex_tiebreak(self):
        """Equal-weight tags break ties lexicographically and are byte-stable
        across runs (idempotency)."""
        mention_rows = [self._make_row("e1", "coding", "general", 3, "2026-06-01")]
        tag_rows = [
            self._make_tag_row("e1", "python", 3, 1.2, "2026-05-01"),
            self._make_tag_row("e1", "docker", 3, 1.2, "2026-05-01"),
        ]
        a, _ = _fold_distributions(mention_rows, {"e1"}, _NOW, tag_rows)
        b, _ = _fold_distributions(mention_rows, {"e1"}, _NOW, tag_rows)
        assert a[0]["top_tags"] == b[0]["top_tags"]
        assert json.loads(a[0]["top_tags"]) == ["docker", "python"]

    def test_primary_subcategory_null_when_all_default(self):
        """primary_subcategory is None when all contributing artifacts have 'general' sub."""
        mention_rows = [
            self._make_row("e1", "research", "general", 3, "2026-01-01"),
        ]
        update_rows, _ = _fold_distributions(mention_rows, {"e1"}, _NOW)
        assert update_rows[0]["primary_subcategory"] is None

    def test_primary_subcategory_non_null_when_signal_present(self):
        """primary_subcategory is set when a non-default sub is the (salience-
        weighted) mode."""
        mention_rows = [
            self._make_row("e1", "research", "papers", 3, "2026-01-01"),
            self._make_row("e1", "research", "general", 1, "2026-02-01"),
        ]
        update_rows, _ = _fold_distributions(mention_rows, {"e1"}, _NOW)
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

    def test_salience_passthrough_and_default(self, domains_client):
        """Slice 6.2: per-domain salience flows through to the response, and
        defaults to 0.0 when get_domain_counts omits it (pre-job backend)."""
        with (
            patch("app.routers.graph.get_neo4j") as mock_get_neo4j,
            patch("app.db.neo4j.taxonomy.get_domain_counts") as mock_counts,
        ):
            mock_get_neo4j.return_value = MagicMock()
            mock_counts.return_value = {
                "domains": [
                    {"name": "finance", "salience": 45.0, "entity_count": 30,
                     "artifact_count": 0, "in_taxonomy": True, "sub_categories": []},
                    {"name": "general", "entity_count": 60,  # no salience key
                     "artifact_count": 0, "in_taxonomy": True, "sub_categories": []},
                ],
                "uncategorized_entities": 0,
                "derived_at": "2026-06-13T21:02:58Z",
            }
            resp = domains_client.get("/graph/domains")
        assert resp.status_code == 200
        data = resp.json()
        assert data["domains"][0]["salience"] == 45.0
        assert data["domains"][1]["salience"] == 0.0  # default when absent


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


# ---------------------------------------------------------------------------
# Slice 6.2: per-domain salience aggregation (pure helper)
# ---------------------------------------------------------------------------


class TestAggregateDomainSalience:
    """_aggregate_domain_salience sums per-entity domain_salience maps into a
    corpus-level per-domain salience mass."""

    def test_sums_across_entities(self):
        from app.db.neo4j.taxonomy import _aggregate_domain_salience

        rows = [
            '{"finance": 10.0, "general": 2.0}',
            '{"finance": 5.0, "coding": 3.0}',
        ]
        totals = _aggregate_domain_salience(rows)
        assert totals["finance"] == 15.0
        assert totals["general"] == 2.0
        assert totals["coding"] == 3.0

    def test_accepts_already_parsed_dicts(self):
        from app.db.neo4j.taxonomy import _aggregate_domain_salience

        totals = _aggregate_domain_salience([{"finance": 1.5}, {"finance": 2.5}])
        assert totals["finance"] == 4.0

    def test_skips_malformed_rows(self):
        from app.db.neo4j.taxonomy import _aggregate_domain_salience

        rows = [None, "", "not-json", "[1,2,3]", '{"finance": "x"}', '{"finance": 4.0}']
        totals = _aggregate_domain_salience(rows)
        # only the last valid row contributes; bad value "x" is skipped
        assert totals == {"finance": 4.0}

    def test_empty_input_is_empty(self):
        from app.db.neo4j.taxonomy import _aggregate_domain_salience

        assert _aggregate_domain_salience([]) == {}
