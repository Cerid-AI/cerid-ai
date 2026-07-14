# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for app/db/neo4j/entity.py — Phase 4.3 re-ingest hygiene.

``remove_mentions_for_artifact`` is the stale-MENTIONS cleanup helper
_reingest_artifact calls when an artifact's content actually changes.
These tests pin two things at the unit level: (1) it removes exactly the
calling artifact's MENTIONS edges, and (2) it never mentions any other
edge type in its Cypher — the human-curated / cross-artifact graph
(RELATES_TO, WIKILINKS_TO, TAGGED_WITH, BELONGS_TO, HAS_ATTACHMENT) that
the O1 re-ingest contract preserves.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from app.db.neo4j.entity import remove_mentions_for_artifact

# Edge types that are human-curated or cross-artifact graph structure —
# _reingest_artifact's docstring promise ("preserves relationships") is
# about these, and remove_mentions_for_artifact must never reference them.
_PRESERVED_EDGE_TYPES = (
    "RELATES_TO",
    "WIKILINKS_TO",
    "EMBEDS",
    "TAGGED_WITH",
    "BELONGS_TO",
    "HAS_ATTACHMENT",
    "REPLIES_TO",
)


def _mock_driver_with_count(removed: int):
    driver = MagicMock()
    session = MagicMock()
    driver.session.return_value.__enter__ = MagicMock(return_value=session)
    driver.session.return_value.__exit__ = MagicMock(return_value=False)
    record = MagicMock()
    record.__getitem__ = lambda self, key: removed if key == "removed" else None
    session.run.return_value.single.return_value = record
    return driver, session


class TestRemoveMentionsForArtifact:
    def test_deletes_only_this_artifacts_mentions_edges(self):
        driver, session = _mock_driver_with_count(3)

        removed = remove_mentions_for_artifact(driver, "art-1")

        assert removed == 3
        session.run.assert_called_once()
        cypher = session.run.call_args.args[0]
        kwargs = session.run.call_args.kwargs
        assert kwargs["artifact_id"] == "art-1"
        assert "MENTIONS" in cypher
        assert "DELETE m" in cypher
        # Scoped to the artifact_id parameter, not a blanket match.
        assert "{id: $artifact_id}" in cypher

    def test_never_touches_preserved_relationship_types(self):
        """The Cypher text must not name any human-curated/graph edge type —
        this is the O1 'preserves relationships' contract, made precise:
        MENTIONS (content-derived provenance) is the only thing in scope."""
        driver, session = _mock_driver_with_count(0)
        remove_mentions_for_artifact(driver, "art-1")

        cypher = session.run.call_args.args[0]
        for edge_type in _PRESERVED_EDGE_TYPES:
            assert edge_type not in cypher, (
                f"remove_mentions_for_artifact's Cypher references {edge_type} "
                "— it must only ever touch MENTIONS"
            )

    def test_never_deletes_the_entity_node_itself(self):
        """Orphaned entities (0 remaining MENTIONS) are left for the existing
        nightly DeriveDomainsJob sweep to handle — this helper deletes only
        the relationship, never `e` itself."""
        driver, session = _mock_driver_with_count(1)
        remove_mentions_for_artifact(driver, "art-1")

        cypher = session.run.call_args.args[0]
        assert "DELETE m" in cypher
        assert "DELETE m, e" not in cypher
        assert "DETACH DELETE" not in cypher

    def test_returns_zero_when_no_matching_edges(self):
        driver, session = _mock_driver_with_count(0)
        assert remove_mentions_for_artifact(driver, "no-mentions-art") == 0

    def test_returns_zero_on_empty_result(self):
        """No matching row (e.g. artifact has zero MENTIONS) → single()
        returns None; must not raise."""
        driver = MagicMock()
        session = MagicMock()
        driver.session.return_value.__enter__ = MagicMock(return_value=session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)
        session.run.return_value.single.return_value = None

        assert remove_mentions_for_artifact(driver, "art-1") == 0
