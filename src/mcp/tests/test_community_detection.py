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
