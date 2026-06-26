# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for app.db.neo4j.community_detection.

These are pure unit tests — the live GDS Leiden run is exercised in the
integration suite (preservation harness has the real neo4j+gds stack).
Here we focus on the deterministic helpers and the Cypher-shape
contract.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.db.neo4j.community_detection import (
    _projection_name,
    detect_communities,
    list_communities,
)

# ---------------------------------------------------------------------------
# _projection_name
# ---------------------------------------------------------------------------

class TestProjectionName:
    def test_starts_with_prefix(self):
        assert _projection_name().startswith("cerid-entities-")

    def test_unique_each_call(self):
        names = {_projection_name() for _ in range(10)}
        assert len(names) == 10  # uuid-suffixed → vanishingly low collision


# ---------------------------------------------------------------------------
# detect_communities — empty-graph short-circuit
# ---------------------------------------------------------------------------

class TestDetectCommunitiesEmpty:
    def test_zero_entities_returns_skipped(self):
        """Empty entity graph → no Leiden, no exception."""
        driver = MagicMock()
        session = driver.session.return_value.__enter__.return_value
        session.run.return_value.single.return_value = {"n": 0}

        out = detect_communities(driver)
        assert out == {"skipped": "no_entities"}
        # Critical: never imported graphdatascience for the empty case
        # (no GDS round-trip = fast scheduler ticks when graph empty).


# ---------------------------------------------------------------------------
# list_communities — readback shape
# ---------------------------------------------------------------------------

class TestListCommunities:
    def test_returns_dicts_with_expected_keys(self):
        driver = MagicMock()
        session = driver.session.return_value.__enter__.return_value
        session.run.return_value = [
            {"id": "0:5", "level": 0, "native_id": 5, "size": 3,
             "members": ["a", "b", "c"]},
            {"id": "0:7", "level": 0, "native_id": 7, "size": 2,
             "members": ["d", "e"]},
        ]
        out = list_communities(driver, level=0)
        assert len(out) == 2
        assert {"id", "level", "native_id", "size", "members"} <= set(out[0].keys())
        assert out[0]["size"] == 3

    def test_no_level_filter_does_not_send_level_param(self):
        driver = MagicMock()
        session = driver.session.return_value.__enter__.return_value
        session.run.return_value = []
        list_communities(driver)
        # Cypher invoked, but `level` not passed as a parameter
        call_kwargs = session.run.call_args.kwargs
        assert "level" not in call_kwargs

    def test_level_filter_passes_param(self):
        driver = MagicMock()
        session = driver.session.return_value.__enter__.return_value
        session.run.return_value = []
        list_communities(driver, level=2)
        call_kwargs = session.run.call_args.kwargs
        assert call_kwargs["level"] == 2


# ---------------------------------------------------------------------------
# Cypher-shape contracts
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "fragment",
    [
        "MATCH (e1:Entity)<-[:MENTIONS]-",   # co-mention pre-step
        "MERGE (e1)-[r:CO_MENTIONED]->",      # materialised undirected edges
    ],
)
def test_co_mention_cypher_in_module(fragment):
    """Sanity: the module text contains the documented Cypher fragments.

    Catches accidental regression of the materialise-then-project path
    (where Leiden requires UNDIRECTED relationships).
    """
    import inspect

    import app.db.neo4j.community_detection as mod
    src = inspect.getsource(mod)
    assert fragment in src


def test_detection_preserves_community_summaries():
    """Re-detection must NOT blanket-delete Community nodes.

    Regression: the old code deleted every Community node up front, wiping
    the cached LLM `.summary` each run and defeating the summary cost-guard
    (community_summaries: WHERE c.summary IS NULL). The fix keeps recurring
    communities (MERGE preserves .summary) and prunes only stale ids AFTER
    the rebuild.
    """
    import inspect

    import app.db.neo4j.community_detection as mod
    src = inspect.getsource(mod)
    # The unconditional pre-rebuild node wipe must be gone (the labelled
    # `(c:Community)` form — the tiny-community cleanup keeps an unlabelled
    # `(c)` variant, which is fine).
    assert "MATCH (c:Community) WHERE NOT (c)<-[:IN_COMMUNITY]-() DELETE c" not in src
    # The stale-only prune (scoped to ids absent from this run) must exist.
    assert "WHERE NOT c.id IN $seen DETACH DELETE c" in src


# ---------------------------------------------------------------------------
# Isolated-entity sentinel (Task 1.1)
# ---------------------------------------------------------------------------


def test_isolated_sentinel_cypher_in_module():
    """After prune, detect_communities must SET community_id = 'isolated' on
    any Entity whose community_id is still NULL (degree-0 orphans that GDS
    never projected).  The sentinel must be the exact string 'isolated', NOT
    in the Leiden '{level}:{native_id}' format.
    """
    import inspect

    import app.db.neo4j.community_detection as mod
    src = inspect.getsource(mod)
    # Exact Cypher that plugs the NULL gap for orphans.
    assert "WHERE e.community_id IS NULL SET e.community_id = 'isolated'" in src


def test_isolated_sentinel_stats_recorded():
    """detect_communities must record stats['isolated_assigned'] so callers
    can observe the orphan count without a separate graph query.
    """
    import inspect

    import app.db.neo4j.community_detection as mod
    src = inspect.getsource(mod)
    assert 'stats["isolated_assigned"]' in src


class TestDetectCommunitiesIsolatedSentinel:
    """Functional contract: orphan entities end with community_id='isolated';
    Leiden-assigned entities end with community_id in '0:<n>' form.

    Full GDS pipeline is mocked so this runs without a live Neo4j + GDS stack.
    The mock mirrors what the real driver produces after a Leiden run with
    two connected entities and one isolated entity:
      - connected entities: leiden_communityIds = [42] (level-0 native id 42)
      - isolated entity:    leiden_communityIds = None  (never in projection)
    """

    def _make_driver(self) -> MagicMock:
        """Build a driver mock that simulates:
        - 3 entities total (ent_count = 3)
        - GDS Leiden assigns communityIds to the two connected entities
        - The isolated entity has no leiden_communityIds → stays NULL
        - The sentinel Cypher affects 1 entity (counters.nodes_set = 1)
        """
        driver = MagicMock()
        session_ctx = driver.session.return_value.__enter__.return_value

        # Call sequence on session.run:
        # 1. count entities → 3
        # 2. DELETE CO_MENTIONED
        # 3. MERGE CO_MENTIONED
        # 4. DELETE IN_COMMUNITY
        # 5. SELECT leiden_communityIds rows → 2 connected entities
        # 6-7. MERGE Community + SET e.community_id for each connected entity
        # 8. DETACH DELETE stale communities
        # 9. tiny-community prune (min_community_size=1 default → skipped in test)
        # 10. SET community_id = 'isolated' for NULL entities → 1 affected
        count_result = MagicMock()
        count_result.single.return_value = {"n": 3}

        delete_co = MagicMock()
        merge_co = MagicMock()
        delete_in_comm = MagicMock()

        # leiden_communityIds rows: two connected entities
        row_a = {"canonical_id": "ent-a", "ids": [42]}
        row_b = {"canonical_id": "ent-b", "ids": [42]}
        rows_result = MagicMock()
        rows_result.__iter__ = MagicMock(return_value=iter([row_a, row_b]))

        # community MERGE result for each entity (counters.relationships_created=1)
        merge_comm_result = MagicMock()
        merge_comm_result.consume.return_value.counters.relationships_created = 1

        # stale-community prune
        prune_result = MagicMock()

        # isolated sentinel: counters.properties_set = 1 (SET e.community_id sets a property)
        isolated_result = MagicMock()
        isolated_summary = MagicMock()
        isolated_summary.counters.properties_set = 1
        isolated_result.consume.return_value = isolated_summary

        session_ctx.run.side_effect = [
            count_result,       # MATCH (e:Entity) RETURN count(e)
            delete_co,          # DELETE CO_MENTIONED
            merge_co,           # MERGE CO_MENTIONED
            delete_in_comm,     # DELETE IN_COMMUNITY
            rows_result,        # SELECT leiden_communityIds
            merge_comm_result,  # MERGE Community for ent-a
            merge_comm_result,  # MERGE Community for ent-b
            prune_result,       # DETACH DELETE stale
            isolated_result,    # SET community_id = 'isolated'
        ]
        return driver

    def test_isolated_sentinel_stats_key_present(self, monkeypatch):
        """stats['isolated_assigned'] equals the count of entities SET."""
        import sys
        from unittest.mock import patch

        gds_mock = MagicMock()
        gds_cls = MagicMock(return_value=gds_mock)
        gds_cls.from_neo4j_driver = MagicMock(return_value=gds_mock)

        graph_mock = MagicMock()
        graph_mock.node_count.return_value = 2
        graph_mock.relationship_count.return_value = 1
        gds_mock.graph.project.return_value = (graph_mock, MagicMock())
        gds_mock.leiden.write.return_value = {"modularity": 0.5, "ranLevels": 1}
        gds_mock.graph.exists.return_value = {"exists": False}

        driver = self._make_driver()

        gds_module = MagicMock()
        gds_module.GraphDataScience = gds_cls
        with patch.dict(sys.modules, {"graphdatascience": gds_module}):
            result = detect_communities(driver, min_community_size=1)

        assert "isolated_assigned" in result
        assert result["isolated_assigned"] == 1

    def _run_detect(self) -> tuple[MagicMock, dict]:
        """Helper: run detect_communities with GDS mocked; return (session_ctx, result)."""
        import sys
        from unittest.mock import patch

        gds_mock = MagicMock()
        gds_cls = MagicMock(return_value=gds_mock)
        gds_cls.from_neo4j_driver = MagicMock(return_value=gds_mock)

        graph_mock = MagicMock()
        graph_mock.node_count.return_value = 2
        graph_mock.relationship_count.return_value = 1
        gds_mock.graph.project.return_value = (graph_mock, MagicMock())
        gds_mock.leiden.write.return_value = {"modularity": 0.5, "ranLevels": 1}
        gds_mock.graph.exists.return_value = {"exists": False}

        driver = self._make_driver()
        session_ctx = driver.session.return_value.__enter__.return_value

        gds_module = MagicMock()
        gds_module.GraphDataScience = gds_cls
        with patch.dict(sys.modules, {"graphdatascience": gds_module}):
            result = detect_communities(driver, min_community_size=1)

        return session_ctx, result

    def test_isolated_sentinel_cypher_uses_isolated_string(self):
        """The last session.run call must contain the literal string 'isolated'.

        This verifies the actual value flowing into Cypher — not just that the
        code path exists (already checked by test_isolated_sentinel_cypher_in_module)
        but that the mock was invoked with a query embedding ``'isolated'``.
        """
        session_ctx, _ = self._run_detect()
        # The isolated-sentinel SET is the final session.run call.
        all_calls = session_ctx.run.call_args_list
        last_cypher: str = all_calls[-1].args[0]
        assert "'isolated'" in last_cypher, (
            f"Expected sentinel Cypher to contain \"'isolated'\"; got: {last_cypher!r}"
        )

    def test_connected_entity_merge_uses_level0_cid(self):
        """MERGE Community for a connected entity must pass cid='0:42'.

        The fixture supplies leiden_communityIds=[42] for both connected
        entities. Level-0 community_id = f'0:{42}' = '0:42'. This asserts
        that the value flowing into the ``cid`` kwarg of the MERGE call is
        exactly ``'0:42'`` — not a raw integer, not None, and not 'isolated'.
        """
        session_ctx, _ = self._run_detect()
        all_calls = session_ctx.run.call_args_list
        # Calls 5 and 6 (0-indexed) are the two MERGE Community calls.
        # Find them by looking for calls that passed cid= as a kwarg.
        merge_cids = [
            call.kwargs["cid"]
            for call in all_calls
            if "cid" in call.kwargs
        ]
        assert merge_cids, "No session.run call received a 'cid' kwarg — MERGE not reached"
        assert all(
            cid == "0:42" for cid in merge_cids
        ), f"Expected all MERGE cid values to be '0:42'; got: {merge_cids}"
