# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for app.db.neo4j.semantic_edges.

Unit tests — no live Neo4j required.  The driver is mocked with MagicMock
following the same pattern as test_community_detection.py.  Embeddings are
hand-set JSON-encoded float32 vectors so the cosine arithmetic is
deterministic and human-verifiable.
"""
from __future__ import annotations

import json
import math
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Helpers — build test embedding rows
# ---------------------------------------------------------------------------

def _l2_normalize(v: list[float]) -> list[float]:
    """Pure-Python L2 normalise for building test vectors."""
    norm = math.sqrt(sum(x * x for x in v))
    if norm < 1e-12:
        return v
    return [x / norm for x in v]


# Three entities:
#   A and B are near-identical (cosine ≈ 1.0 after normalization)
#   C is near-orthogonal to both (cosine ≈ 0.0)
_VEC_A = _l2_normalize([1.0, 0.0, 0.0])
_VEC_B = _l2_normalize([0.99, 0.14, 0.0])   # cosine(A,B) ≈ 0.99 > 0.8
_VEC_C = _l2_normalize([0.0, 0.0, 1.0])     # cosine(A,C) = 0, cosine(B,C) ≈ 0.0

def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))

_COSINE_AB = _cosine(_VEC_A, _VEC_B)  # > 0.8, so an edge should form
_COSINE_AC = _cosine(_VEC_A, _VEC_C)  # ≈ 0.0
_COSINE_BC = _cosine(_VEC_B, _VEC_C)  # ≈ 0.0


def _make_driver(
    entity_rows: list[dict],
    co_mentioned_pairs: list[dict] | None = None,
) -> MagicMock:
    """Build a mock driver whose session returns preset data.

    The session context manager returns a mock session; session.run dispatches by
    QUERY CONTENT (not call order) so the mock stays correct regardless of the
    order build_similarity_edges issues its statements. Queries used:
      * DELETE SIMILAR_TO edges   (consume only)
      * MATCH Entity.embedding     (iterable of entity_rows)
      * MATCH CO_MENTIONED pairs   (iterable of co_mentioned_pairs)
      * UNWIND MERGE SIMILAR_TO    (consume → relationships_created)

    NOTE: the fetch-Entity-embedding query now runs BEFORE the DELETE (AF-013
    data-readiness gate — never purge before confirming there's data to rebuild),
    so this content-based dispatch is deliberately order-independent.
    """
    driver = MagicMock()
    session = MagicMock()
    driver.session.return_value.__enter__.return_value = session
    driver.session.return_value.__exit__.return_value = False

    co_mentioned_pairs = co_mentioned_pairs or []

    # Mock counters for the MERGE SIMILAR_TO call.
    merge_counters = MagicMock()
    merge_counters.relationships_created = 1  # will be overridden per-test if needed
    merge_consume = MagicMock()
    merge_consume.counters = merge_counters

    def _run_side_effect(*args, **kwargs):
        query = str(args[0]) if args else ""
        mock_result = MagicMock()
        if "DELETE" in query and "SIMILAR_TO" in query:
            mock_result.consume.return_value = MagicMock()
        elif "MERGE" in query and "SIMILAR_TO" in query:
            mock_result.consume.return_value = merge_consume
        elif "CO_MENTIONED" in query:
            mock_result.__iter__ = lambda s: iter(co_mentioned_pairs)
            mock_result.data.return_value = co_mentioned_pairs
        elif "Entity" in query and "embedding" in query:
            mock_result.__iter__ = lambda s: iter(entity_rows)
            mock_result.data.return_value = entity_rows
        else:
            mock_result.__iter__ = lambda s: iter([])
            mock_result.data.return_value = []
        return mock_result

    # Override: decide edges_created based on whether any rows would be written.
    # For simplicity, set relationships_created to a sentinel; the test asserts
    # on result["edges_created"] which comes from .consume().counters.relationships_created.
    # We configure that to be 1 when edge_rows is non-empty and 0 when empty.
    # To allow the test to patch this correctly, we store the consume mock on driver.
    driver._merge_counters = merge_counters
    session.run.side_effect = _run_side_effect
    return driver


# ---------------------------------------------------------------------------
# Test: basic kNN edge creation
# ---------------------------------------------------------------------------


class TestBuildSimilarityEdgesBasic:
    def test_creates_edge_between_near_pair(self):
        """Near-identical A and B get a SIMILAR_TO edge; orthogonal C does not."""
        from app.db.neo4j.semantic_edges import build_similarity_edges

        entity_rows = [
            {"canonical_id": "a", "embedding": json.dumps(_VEC_A), "co_mention_degree": 0},
            {"canonical_id": "b", "embedding": json.dumps(_VEC_B), "co_mention_degree": 0},
            {"canonical_id": "c", "embedding": json.dumps(_VEC_C), "co_mention_degree": 0},
        ]
        driver = _make_driver(entity_rows)

        result = build_similarity_edges(driver, k=2, threshold=0.8)

        assert result["edges_created"] == 1
        assert result["entities_with_embeddings"] == 3

        # Verify exactly one edge row was sent to the MERGE call (not a mock counter artefact).
        session = driver.session.return_value.__enter__.return_value
        merge_calls = [
            c for c in session.run.call_args_list
            if "SIMILAR_TO" in str(c) and "MERGE" in str(c)
        ]
        assert merge_calls, "No MERGE SIMILAR_TO call found"
        rows_arg = None
        for c in merge_calls:
            kwargs = c.kwargs if c.kwargs else {}
            args = c.args if c.args else ()
            if "rows" in kwargs:
                rows_arg = kwargs["rows"]
            elif len(args) > 1:
                rows_arg = args[1]
        assert rows_arg is not None, "rows param not found in MERGE call"
        assert len(rows_arg) == 1, f"Expected exactly 1 edge row, got {len(rows_arg)}"

    def test_no_edge_to_orthogonal(self):
        """Pairs with cosine < threshold get no edge."""
        from app.db.neo4j.semantic_edges import build_similarity_edges

        entity_rows = [
            {"canonical_id": "a", "embedding": json.dumps(_VEC_A), "co_mention_degree": 0},
            {"canonical_id": "c", "embedding": json.dumps(_VEC_C), "co_mention_degree": 0},
        ]
        driver = _make_driver(entity_rows)

        result = build_similarity_edges(driver, k=2, threshold=0.8)

        assert result["edges_created"] == 0

    def test_score_stored_on_edge(self):
        """The score written to the MERGE call should match the true cosine."""
        from app.db.neo4j.semantic_edges import build_similarity_edges

        entity_rows = [
            {"canonical_id": "a", "embedding": json.dumps(_VEC_A), "co_mention_degree": 0},
            {"canonical_id": "b", "embedding": json.dumps(_VEC_B), "co_mention_degree": 0},
            {"canonical_id": "c", "embedding": json.dumps(_VEC_C), "co_mention_degree": 0},
        ]
        driver = _make_driver(entity_rows)
        build_similarity_edges(driver, k=2, threshold=0.8)

        # Find the MERGE SIMILAR_TO call and check the score parameter.
        session = driver.session.return_value.__enter__.return_value
        merge_calls = [
            c for c in session.run.call_args_list
            if "SIMILAR_TO" in str(c) and "MERGE" in str(c)
        ]
        assert merge_calls, "No MERGE SIMILAR_TO call found"
        # The rows param should contain our edge with score ≈ _COSINE_AB
        rows_arg = None
        for c in merge_calls:
            kwargs = c.kwargs if c.kwargs else {}
            args = c.args if c.args else ()
            # rows may be passed as positional or keyword
            if "rows" in kwargs:
                rows_arg = kwargs["rows"]
            elif len(args) > 1:
                rows_arg = args[1]
        assert rows_arg is not None, "rows param not found in MERGE call"
        assert len(rows_arg) == 1
        score = rows_arg[0]["score"]
        assert abs(score - _COSINE_AB) < 1e-4


# ---------------------------------------------------------------------------
# Test: idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_second_run_does_not_duplicate(self):
        """Running twice should produce the same edge count, not double it.

        Since each run starts with DELETE SIMILAR_TO, the count after the
        second run must equal the count after the first run.
        """
        from app.db.neo4j.semantic_edges import build_similarity_edges

        entity_rows = [
            {"canonical_id": "a", "embedding": json.dumps(_VEC_A), "co_mention_degree": 0},
            {"canonical_id": "b", "embedding": json.dumps(_VEC_B), "co_mention_degree": 0},
            {"canonical_id": "c", "embedding": json.dumps(_VEC_C), "co_mention_degree": 0},
        ]

        driver1 = _make_driver(entity_rows)
        r1 = build_similarity_edges(driver1, k=2, threshold=0.8)

        driver2 = _make_driver(entity_rows)
        r2 = build_similarity_edges(driver2, k=2, threshold=0.8)

        assert r1["edges_created"] == r2["edges_created"] == 1

        # Verify each run sent exactly one edge row to the MERGE (not a mock counter artefact).
        for drv in (driver1, driver2):
            session = drv.session.return_value.__enter__.return_value
            merge_calls = [
                c for c in session.run.call_args_list
                if "SIMILAR_TO" in str(c) and "MERGE" in str(c)
            ]
            assert merge_calls, "No MERGE SIMILAR_TO call found on a run"
            rows_arg = None
            for c in merge_calls:
                kwargs = c.kwargs if c.kwargs else {}
                args = c.args if c.args else ()
                if "rows" in kwargs:
                    rows_arg = kwargs["rows"]
                elif len(args) > 1:
                    rows_arg = args[1]
            assert rows_arg is not None, "rows param not found in MERGE call"
            assert len(rows_arg) == 1, f"Expected exactly 1 edge row per run, got {len(rows_arg)}"

    def test_delete_runs_first(self):
        """The DELETE SIMILAR_TO statement must appear before the MERGE."""
        from app.db.neo4j.semantic_edges import build_similarity_edges

        entity_rows = [
            {"canonical_id": "a", "embedding": json.dumps(_VEC_A), "co_mention_degree": 0},
            {"canonical_id": "b", "embedding": json.dumps(_VEC_B), "co_mention_degree": 0},
        ]
        driver = _make_driver(entity_rows)
        build_similarity_edges(driver, k=2, threshold=0.8)

        session = driver.session.return_value.__enter__.return_value
        cypher_calls = [str(c.args[0]) for c in session.run.call_args_list if c.args]
        delete_idx = next(
            (i for i, s in enumerate(cypher_calls) if "DELETE" in s and "SIMILAR_TO" in s),
            None,
        )
        merge_idx = next(
            (i for i, s in enumerate(cypher_calls) if "MERGE" in s and "SIMILAR_TO" in s),
            None,
        )
        assert delete_idx is not None, "DELETE SIMILAR_TO not found"
        assert merge_idx is not None, "MERGE SIMILAR_TO not found"
        assert delete_idx < merge_idx, "DELETE must come before MERGE"


# ---------------------------------------------------------------------------
# Test: CO_MENTIONED exclusion
# ---------------------------------------------------------------------------


class TestCoMentionedExclusion:
    def test_co_mentioned_pair_excluded(self):
        """A pair already linked by CO_MENTIONED should get no SIMILAR_TO edge."""
        from app.db.neo4j.semantic_edges import build_similarity_edges

        entity_rows = [
            {"canonical_id": "a", "embedding": json.dumps(_VEC_A), "co_mention_degree": 0},
            {"canonical_id": "b", "embedding": json.dumps(_VEC_B), "co_mention_degree": 0},
            {"canonical_id": "c", "embedding": json.dumps(_VEC_C), "co_mention_degree": 0},
        ]
        # A and B are already CO_MENTIONED — they must be excluded
        co_mentioned = [{"a_id": "a", "b_id": "b"}]

        # Build a driver that returns co_mentioned pairs
        driver = MagicMock()
        session = MagicMock()
        driver.session.return_value.__enter__.return_value = session
        driver.session.return_value.__exit__.return_value = False

        call_results = [
            MagicMock(),          # DELETE SIMILAR_TO
            entity_rows,          # entity fetch
            co_mentioned,         # co_mentioned pairs
            MagicMock(),          # MERGE (may not be called)
        ]
        run_iter = iter(call_results)

        def _run_side_effect(*args, **kwargs):
            result = next(run_iter, MagicMock())
            mock_result = MagicMock()
            if isinstance(result, list):
                mock_result.__iter__ = lambda s: iter(result)
                mock_result.data.return_value = result
            mock_result.consume.return_value = MagicMock()
            return mock_result

        session.run.side_effect = _run_side_effect

        result = build_similarity_edges(driver, k=2, threshold=0.8)

        # A–B pair is near (would normally produce an edge) but is excluded
        assert result["edges_created"] == 0


# ---------------------------------------------------------------------------
# Test: disabled no-op
# ---------------------------------------------------------------------------


class TestDisabledNoOp:
    def test_disabled_returns_skipped(self, monkeypatch):
        """When SEMANTIC_EDGE_ENABLED is False, the function is a no-op."""
        import config
        monkeypatch.setattr(config, "SEMANTIC_EDGE_ENABLED", False)

        from app.db.neo4j.semantic_edges import build_similarity_edges

        driver = MagicMock()
        result = build_similarity_edges(driver, k=5, threshold=0.82)

        assert result == {"skipped": "disabled"}
        driver.session.assert_not_called()


# ---------------------------------------------------------------------------
# Test: edge direction — lower canonical_id → higher (no double edges)
# ---------------------------------------------------------------------------


class TestEdgeDirection:
    def test_single_directed_edge_per_pair(self):
        """Only one directed edge per pair; direction = lower id → higher id."""
        from app.db.neo4j.semantic_edges import build_similarity_edges

        # Two near-identical entities; we'll verify the rows written
        entity_rows = [
            {"canonical_id": "alpha", "embedding": json.dumps(_VEC_A), "co_mention_degree": 0},
            {"canonical_id": "beta", "embedding": json.dumps(_VEC_B), "co_mention_degree": 0},
        ]
        driver = _make_driver(entity_rows)
        build_similarity_edges(driver, k=2, threshold=0.8)

        session = driver.session.return_value.__enter__.return_value
        # Find the MERGE call rows
        rows_arg = None
        for c in session.run.call_args_list:
            kwargs = c.kwargs if c.kwargs else {}
            args_list = c.args if c.args else ()
            if "SIMILAR_TO" in str(args_list[0] if args_list else "") and "MERGE" in str(args_list[0] if args_list else ""):
                if "rows" in kwargs:
                    rows_arg = kwargs["rows"]
                elif len(args_list) > 1:
                    rows_arg = args_list[1]
        assert rows_arg is not None
        # Exactly one row
        assert len(rows_arg) == 1
        row = rows_arg[0]
        # canonical direction: lower lexicographic id → higher
        assert row["from_id"] < row["to_id"]
