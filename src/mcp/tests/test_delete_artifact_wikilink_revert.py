# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""AF-068 — delete_artifact must revert inbound WIKILINKS_TO/EMBEDS edges to
a PendingArtifact placeholder before detaching the artifact, so re-ingesting
a same-named note can resolve via resolve_pending_artifacts instead of
finding no placeholder.

Offline unit coverage of the Cypher shape (MagicMock session — no live
Neo4j); the round-trip semantics are covered live in
``test_wikilink_neo4j_resolution.py::TestDeleteRevertsInboundWikilinksToPending``.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from app.db.neo4j.artifacts import delete_artifact


def _driver_with_fetch_record(record: dict) -> tuple[MagicMock, MagicMock]:
    driver = MagicMock()
    session = MagicMock()
    driver.session.return_value.__enter__ = MagicMock(return_value=session)
    driver.session.return_value.__exit__ = MagicMock(return_value=False)
    session.run.return_value.single.return_value = record
    return driver, session


class TestDeleteArtifactIssuesRevertQueries:
    def test_issues_revert_queries_for_both_rel_types_before_detach_delete(self) -> None:
        driver, session = _driver_with_fetch_record({
            "chunk_ids": '["c1"]', "domain": "coding",
            "filename": "Target.md", "child_chunk_ids": [],
        })

        delete_artifact(driver, "art-1")

        queries = [" ".join(c.args[0].split()) for c in session.run.call_args_list]
        # Fetch, WIKILINKS_TO revert, EMBEDS revert, then DETACH DELETE — in order.
        assert len(queries) == 4
        assert "RETURN a.chunk_ids AS chunk_ids" in queries[0]
        assert "WIKILINKS_TO" in queries[1] and "PendingArtifact" in queries[1]
        assert "EMBEDS" in queries[2] and "PendingArtifact" in queries[2]
        assert "DETACH DELETE" in queries[3]
        # The revert queries run BEFORE the artifact (and its edges) are gone.
        wikilinks_idx = next(i for i, q in enumerate(queries) if "WIKILINKS_TO" in q)
        detach_idx = next(i for i, q in enumerate(queries) if "DETACH DELETE" in q)
        assert wikilinks_idx < detach_idx

    def test_revert_queries_key_the_placeholder_by_filename_stem(self) -> None:
        driver, session = _driver_with_fetch_record({
            "chunk_ids": "[]", "domain": "coding",
            "filename": "Notes/My Note.md", "child_chunk_ids": [],
        })

        delete_artifact(driver, "art-2")

        stems = [c.kwargs.get("stem") for c in session.run.call_args_list if "stem" in c.kwargs]
        assert stems == ["My Note", "My Note"]

    def test_no_revert_queries_when_filename_is_missing(self) -> None:
        """An artifact with no filename has no meaningful stem to key a
        placeholder by — must not emit a WIKILINKS_TO/EMBEDS revert query."""
        driver, session = _driver_with_fetch_record({
            "chunk_ids": "[]", "domain": "coding",
            "filename": None, "child_chunk_ids": [],
        })

        delete_artifact(driver, "art-3")

        queries = [" ".join(c.args[0].split()) for c in session.run.call_args_list]
        assert len(queries) == 2  # fetch + DETACH DELETE only
        assert "DETACH DELETE" in queries[1]

    def test_not_found_short_circuits_before_any_revert_query(self) -> None:
        driver, session = _driver_with_fetch_record(None)

        result = delete_artifact(driver, "missing-id")

        assert result == {"deleted": False, "reason": "not_found"}
        assert session.run.call_count == 1  # only the fetch attempt
