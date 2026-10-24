# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for app.db.neo4j.community_detection.

These are pure unit tests — the live GDS Leiden run is exercised in the
integration suite (preservation harness has the real neo4j+gds stack).
Here we focus on the deterministic helpers and the Cypher-shape
contract.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, create_autospec

import pytest

from app.db.neo4j.community_detection import (
    _projection_name,
    detect_communities,
    list_communities,
)

# ---------------------------------------------------------------------------
# graphdatascience 2.x doubles
# ---------------------------------------------------------------------------

def _install_gds_v2(
    monkeypatch,
    *,
    leiden_error: Exception | None = None,
    projection_exists: bool = False,
) -> SimpleNamespace:
    """Patch ``graphdatascience.GraphDataScience`` with doubles specced on the
    real 2.x client.

    autospec checks every call against the installed signatures, so 1.x shapes
    fail here: ``GraphDataScience.from_neo4j_driver`` does not exist, the
    callable ``gds.graph.project(...)`` became ``gds.graph.project.native``,
    ``gds.leiden.write`` takes snake_case keywords, it returns a typed
    ``LeidenWriteResult`` (no ``.get``), and ``gds.graph.exists`` returns a
    bool rather than a row with an ``"exists"`` key.
    """
    import graphdatascience
    from graphdatascience import GraphDataScience
    from graphdatascience.graph.graph_api import Graph
    from graphdatascience.procedure_surface.api.community.leiden_endpoints import (
        LeidenWriteResult,
    )
    from graphdatascience.procedure_surface.cypher.catalog.catalog_cypher_endpoints import (
        CatalogCypherEndpoints,
    )
    from graphdatascience.procedure_surface.cypher.catalog.projection_cypher_endpoints import (
        GraphWithProjectResult,
        ProjectCypherEndpoints,
    )
    from graphdatascience.procedure_surface.cypher.community.leiden_cypher_endpoints import (
        LeidenCypherEndpoints,
    )

    graph = create_autospec(Graph, instance=True)
    graph.node_count.return_value = 2
    graph.relationship_count.return_value = 1

    project = create_autospec(ProjectCypherEndpoints, instance=True)
    project.native.return_value = GraphWithProjectResult(graph, MagicMock())

    catalog = create_autospec(CatalogCypherEndpoints, instance=True)
    catalog.project = project
    catalog.exists.return_value = projection_exists

    leiden = create_autospec(LeidenCypherEndpoints, instance=True)
    if leiden_error is not None:
        leiden.write.side_effect = leiden_error
    else:
        leiden.write.return_value = LeidenWriteResult(
            community_count=1,
            community_distribution={},
            compute_millis=1,
            configuration={},
            did_converge=True,
            modularities=[0.4, 0.5],
            modularity=0.5,
            node_count=2,
            node_properties_written=2,
            post_processing_millis=0,
            pre_processing_millis=0,
            ran_levels=2,
            write_millis=1,
        )

    gds = create_autospec(GraphDataScience, instance=True)
    gds.graph = catalog
    gds.leiden = leiden

    gds_cls = create_autospec(GraphDataScience)
    gds_cls.return_value = gds
    monkeypatch.setattr(graphdatascience, "GraphDataScience", gds_cls)
    return SimpleNamespace(
        cls=gds_cls, gds=gds, catalog=catalog, project=project,
        leiden=leiden, graph=graph,
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
    every degree-0 orphan.  The sentinel must be the exact string 'isolated',
    NOT in the Leiden '{level}:{native_id}' format.

    The NULL test alone is not sufficient and used to be the whole condition.
    gds.graph.project(name, "Entity", {...}) is a native LABEL projection: it
    includes every Entity, edges or not, so Leiden gives each orphan its own
    singleton community and the write-back stamps a real id on it. Nothing was
    ever NULL, so the sentinel was never assigned — caught by the live
    AF-087 invariant once it stopped being skipped in CI.
    """
    import inspect

    import app.db.neo4j.community_detection as mod
    src = inspect.getsource(mod)
    assert "SET e.community_id = 'isolated'" in src
    # Orphan-ness is asked of the graph, not inferred from a NULL that a label
    # projection guarantees will not be there.
    assert "NOT (e)-[:CO_MENTIONED]-()" in src


def test_partition_reset_cypher_in_module():
    """AF-034: detect_communities must clear last run's partition markers
    (leiden_communityIds + community_id) before Leiden re-writes them, so an
    entity that dropped out of this run's projection can't keep a stale
    community_id (or resurrect its old Community via the write-back MERGE) — it
    falls through to the 'isolated' sentinel instead.
    """
    import inspect

    import app.db.neo4j.community_detection as mod
    src = inspect.getsource(mod)
    assert "REMOVE e.leiden_communityIds, e.community_id" in src


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
        # 4. AF-034: REMOVE leiden_communityIds, community_id (up-front reset)
        # 5. DELETE IN_COMMUNITY
        # 6. SELECT leiden_communityIds rows → 2 connected entities
        # 7-8. MERGE Community + SET e.community_id for each connected entity
        # 9. DETACH DELETE stale communities
        # 10. tiny-community prune (min_community_size=1 default → skipped in test)
        # 11. DELETE IN_COMMUNITY for orphans (no CO_MENTIONED edge)
        # 12. SET community_id = 'isolated' for NULL-or-orphan entities → 1 affected
        count_result = MagicMock()
        count_result.single.return_value = {"n": 3}

        delete_co = MagicMock()
        merge_co = MagicMock()
        clear_partition = MagicMock()  # AF-034: up-front leiden/community_id reset
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

        # orphan IN_COMMUNITY detach (no labelled Community delete follows it —
        # see test_detection_preserves_community_summaries)
        orphan_detach = MagicMock()

        # isolated sentinel: counters.properties_set = 1 (SET e.community_id sets a property)
        isolated_result = MagicMock()
        isolated_summary = MagicMock()
        isolated_summary.counters.properties_set = 1
        isolated_result.consume.return_value = isolated_summary

        session_ctx.run.side_effect = [
            count_result,       # MATCH (e:Entity) RETURN count(e)
            delete_co,          # DELETE CO_MENTIONED
            merge_co,           # MERGE CO_MENTIONED
            clear_partition,    # AF-034: REMOVE leiden_communityIds, community_id
            delete_in_comm,     # DELETE IN_COMMUNITY
            rows_result,        # SELECT leiden_communityIds
            merge_comm_result,  # MERGE Community for ent-a
            merge_comm_result,  # MERGE Community for ent-b
            prune_result,       # DETACH DELETE stale
            orphan_detach,      # DELETE IN_COMMUNITY for orphans
            isolated_result,    # SET community_id = 'isolated'
        ]
        return driver

    def test_isolated_sentinel_stats_key_present(self, monkeypatch):
        """stats['isolated_assigned'] equals the count of entities SET."""
        _, result = self._run_detect(monkeypatch)

        assert "isolated_assigned" in result
        assert result["isolated_assigned"] == 1

    def _run_detect(self, monkeypatch) -> tuple[MagicMock, dict]:
        """Helper: run detect_communities with GDS mocked; return (session_ctx, result)."""
        _install_gds_v2(monkeypatch)
        driver = self._make_driver()
        session_ctx = driver.session.return_value.__enter__.return_value
        result = detect_communities(driver, min_community_size=1)
        return session_ctx, result

    def test_isolated_sentinel_cypher_uses_isolated_string(self, monkeypatch):
        """The last session.run call must contain the literal string 'isolated'.

        This verifies the actual value flowing into Cypher — not just that the
        code path exists (already checked by test_isolated_sentinel_cypher_in_module)
        but that the mock was invoked with a query embedding ``'isolated'``.
        """
        session_ctx, _ = self._run_detect(monkeypatch)
        # Searched rather than indexed: this used to assert on the LAST call,
        # which broke the moment a statement was added after the sentinel. The
        # contract is that the sentinel is issued, not that it is issued last.
        cyphers = [c.args[0] for c in session_ctx.run.call_args_list if c.args]
        sentinel = [q for q in cyphers if "'isolated'" in q]
        assert sentinel, (
            "Expected one session.run call embedding \"'isolated'\"; "
            f"got: {cyphers!r}"
        )

    def test_connected_entity_merge_uses_level0_cid(self, monkeypatch):
        """MERGE Community for a connected entity must pass cid='0:42'.

        The fixture supplies leiden_communityIds=[42] for both connected
        entities. Level-0 community_id = f'0:{42}' = '0:42'. This asserts
        that the value flowing into the ``cid`` kwarg of the MERGE call is
        exactly ``'0:42'`` — not a raw integer, not None, and not 'isolated'.
        """
        session_ctx, _ = self._run_detect(monkeypatch)
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


class TestDetectCommunitiesGdsV2Api:
    """The GDS client calls match the graphdatascience 2.x API."""

    def _driver(self, entity_count: int = 3) -> MagicMock:
        driver = MagicMock()
        session = driver.session.return_value.__enter__.return_value
        session.run.return_value.single.return_value = {"n": entity_count}
        session.run.return_value.__iter__ = MagicMock(return_value=iter([]))
        return driver

    def test_client_built_from_driver_over_bolt(self, monkeypatch):
        doubles = _install_gds_v2(monkeypatch)
        driver = self._driver()

        detect_communities(driver, neo4j_database="graphdb")

        doubles.cls.assert_called_once_with(driver, database="graphdb", arrow=False)

    def test_native_projection_and_snake_case_leiden_write(self, monkeypatch):
        doubles = _install_gds_v2(monkeypatch)

        stats = detect_communities(self._driver(), max_levels=4)

        args = doubles.project.native.call_args.args
        assert args[0] == stats["projection"]
        assert args[1] == "Entity"
        assert args[2] == {
            "CO_MENTIONED": {"orientation": "UNDIRECTED", "properties": ["weight"]}
        }
        doubles.leiden.write.assert_called_once_with(
            doubles.graph,
            write_property="leiden_communityIds",
            relationship_weight_property="weight",
            include_intermediate_communities=True,
            max_levels=4,
        )
        assert stats["nodes_projected"] == 2
        assert stats["edges_projected"] == 1
        assert stats["modularity"] == 0.5
        assert stats["levels_produced"] == 2
        doubles.graph.drop.assert_called_once_with()

    def test_failed_leiden_drops_projection(self, monkeypatch):
        doubles = _install_gds_v2(
            monkeypatch, leiden_error=RuntimeError("leiden failed"),
            projection_exists=True,
        )

        with pytest.raises(RuntimeError, match="leiden failed"):
            detect_communities(self._driver())

        proj_name = doubles.project.native.call_args.args[0]
        doubles.catalog.exists.assert_called_once_with(proj_name)
        doubles.catalog.drop.assert_called_once_with(proj_name)

    def test_cleanup_skips_drop_when_projection_gone(self, monkeypatch):
        doubles = _install_gds_v2(monkeypatch, projection_exists=False)

        detect_communities(self._driver())

        doubles.catalog.exists.assert_called_once()
        doubles.catalog.drop.assert_not_called()
