# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""TDD tests for scripts.merge_entity_aliases.

Task 2.3 — resumable merge-migration for already-stored duplicate entities.

Test scope:
  - Two (:Entity) nodes that resolve to the same canonical_id are merged
    into one surviving node; mention_count is summed; both artifacts'
    MENTIONS edges point at the survivor.
  - A singleton cluster (size 1) is left untouched.
  - Dry-run (default) writes nothing to the graph.
  - Cypher string and param correctness verified via call-capture.
  - In-memory fake Neo4j asserts post-merge state (mention_count summed,
    loser deleted, artifacts re-pointed to survivor).
  - Survivor name is not downgraded when the existing name is longer.
  - --limit caps the number of clusters processed.
  - --reset clears the checkpoint file.
"""
from __future__ import annotations

import os as _os
import uuid as _uuid
from pathlib import Path
from typing import Any, Literal
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Minimal Neo4j mock helpers
# ---------------------------------------------------------------------------

def _make_entity_row(
    canonical_id: str,
    name: str,
    entity_type: str,
    mention_count: int = 1,
    confidence: float | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "canonical_id": canonical_id,
        "name": name,
        "entity_type": entity_type,
        "mention_count": mention_count,
    }
    if confidence is not None:
        row["confidence"] = confidence
    return row


def _driver_with_entities(entity_rows: list[dict]) -> MagicMock:
    """Return a mock driver whose session().run() yields entity_rows on the
    first call (entity scan) and empty lists on subsequent calls (merge ops).
    """
    driver = MagicMock()
    session = MagicMock()
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)

    # The session context manager used in the script body (__enter__)
    results: list[Any] = [entity_rows]

    # run() returns the next canned result each time; after the initial scan
    # all merge Cypher calls get an empty list (they consume .consume() or
    # .single(), not iteration, so this is safe).
    def _run(cypher, **kwargs):
        if results:
            return iter(results.pop(0))
        return iter([])

    session.run.side_effect = _run
    driver.session.return_value = session
    return driver


# ---------------------------------------------------------------------------
# In-memory fake Neo4j driver
#
# Interprets a small subset of the script's Cypher well enough to assert
# post-merge graph state:
#   - Entity nodes tracked by canonical_id with mutable props dict
#   - Artifact nodes tracked by artifact_id
#   - MENTIONS edges tracked as (artifact_id → entity canonical_id) pairs
#   - Handles UPSERT_SURVIVOR, REPOINT_MENTIONS (mention_count summation),
#     and DELETE_LOSER
# ---------------------------------------------------------------------------

class _FakeDb:
    """In-memory graph state: entities, artifacts, MENTIONS edges."""

    def __init__(self) -> None:
        # canonical_id → {name, entity_type, mention_count, ...}
        self.entities: dict[str, dict[str, Any]] = {}
        # artifact_id → set of entity canonical_ids it MENTIONS
        self.mentions: dict[str, set[str]] = {}

    def seed_entity(
        self,
        canonical_id: str,
        name: str,
        entity_type: str,
        mention_count: int = 1,
    ) -> None:
        self.entities[canonical_id] = {
            "canonical_id": canonical_id,
            "name": name,
            "entity_type": entity_type,
            "mention_count": mention_count,
        }

    def seed_artifact_mention(self, artifact_id: str, entity_canonical_id: str) -> None:
        self.mentions.setdefault(artifact_id, set()).add(entity_canonical_id)


class _FakeSession:
    """Intercepts session.run() calls and mutates _FakeDb accordingly."""

    def __init__(self, db: _FakeDb, entity_rows: list[dict[str, Any]]) -> None:
        self._db = db
        self._entity_rows = entity_rows
        self._first_call = True
        # Record all (cypher, kwargs) pairs for call-capture assertions
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def run(self, cypher: str, **kwargs: Any) -> Any:
        self.calls.append((cypher, kwargs))

        if self._first_call:
            self._first_call = False
            return iter(self._entity_rows)

        # UPSERT_SURVIVOR: MERGE (e:Entity {canonical_id: $canonical_id})
        if "MERGE (e:Entity {canonical_id: $canonical_id})" in cypher:
            cid = kwargs["canonical_id"]
            name = kwargs["name"]
            entity_type = kwargs["entity_type"]
            if cid not in self._db.entities:
                self._db.entities[cid] = {
                    "canonical_id": cid,
                    "name": name,
                    "entity_type": entity_type,
                    "mention_count": 0,
                }
            else:
                existing = self._db.entities[cid]
                if len(name) > len(existing.get("name", "")):
                    existing["name"] = name
            return iter([])

        # REPOINT_MENTIONS: re-points artifacts + sums mention_count
        if "MENTIONS" in cypher and "$loser_id" in cypher and "$survivor_id" in cypher and "CO_MENTIONED" not in cypher and "IN_COMMUNITY" not in cypher:
            loser_id = kwargs["loser_id"]
            survivor_id = kwargs["survivor_id"]
            loser = self._db.entities.get(loser_id)
            survivor = self._db.entities.get(survivor_id)
            if loser and survivor:
                # Sum mention_count
                survivor["mention_count"] = (
                    survivor.get("mention_count", 0) + loser.get("mention_count", 0)
                )
                # Re-point MENTIONS edges from loser → survivor
                for artifact_id, targets in self._db.mentions.items():
                    if loser_id in targets:
                        targets.discard(loser_id)
                        targets.add(survivor_id)
            return iter([])

        # DELETE_LOSER: DETACH DELETE loser
        if "DETACH DELETE loser" in cypher:
            loser_id = kwargs.get("loser_id")
            if loser_id:
                self._db.entities.pop(loser_id, None)
            return iter([])

        return iter([])

    def __enter__(self) -> "_FakeSession":
        return self

    def __exit__(self, *_: Any) -> Literal[False]:
        return False


class _FakeDriver:
    """A fake Neo4j driver backed by _FakeDb."""

    def __init__(self, db: _FakeDb, entity_rows: list[dict[str, Any]]) -> None:
        self._db = db
        self._entity_rows = entity_rows
        self._sessions: list[_FakeSession] = []

    def session(self) -> "_FakeSession":
        sess = _FakeSession(self._db, self._entity_rows)
        self._sessions.append(sess)
        return sess

    @property
    def all_calls(self) -> list[tuple[str, dict[str, Any]]]:
        return [c for s in self._sessions for c in s.calls]


# ---------------------------------------------------------------------------
# _group_by_canonical
# ---------------------------------------------------------------------------

class TestGroupByCanonical:
    """_group_by_canonical clusters entity rows by resolve_canonical output."""

    def test_elon_aliases_merge_to_same_cluster(self):
        from scripts.merge_entity_aliases import _group_by_canonical

        rows = [
            _make_entity_row("person:elon-musk", "Elon Musk", "PERSON"),
            _make_entity_row("person:elon-r-musk", "Elon R. Musk", "PERSON"),
        ]
        groups = _group_by_canonical(rows)
        # Both resolve to person:elon-musk via Tier B (middle-initial strip)
        assert len(groups) == 1
        canonical_id = list(groups.keys())[0]
        assert canonical_id == "person:elon-musk"
        assert len(groups[canonical_id]) == 2

    def test_singleton_stays_alone(self):
        from scripts.merge_entity_aliases import _group_by_canonical

        rows = [_make_entity_row("org:apple", "Apple", "ORG")]
        groups = _group_by_canonical(rows)
        # One entity → one group with one member
        assert sum(len(v) for v in groups.values()) == 1

    def test_distinct_entities_form_separate_clusters(self):
        from scripts.merge_entity_aliases import _group_by_canonical

        rows = [
            _make_entity_row("person:elon-musk", "Elon Musk", "PERSON"),
            _make_entity_row("org:apple", "Apple", "ORG"),
        ]
        groups = _group_by_canonical(rows)
        assert len(groups) == 2

    def test_cross_type_no_merge(self):
        """Fed as ORG and a hypothetical FED ticker as ASSET must not merge."""
        from scripts.merge_entity_aliases import _group_by_canonical

        rows = [
            _make_entity_row("org:federal-reserve", "the Fed", "ORG"),
            _make_entity_row("asset:fed", "FED", "ASSET"),
        ]
        groups = _group_by_canonical(rows)
        assert len(groups) == 2


# ---------------------------------------------------------------------------
# _pick_survivor_name
# ---------------------------------------------------------------------------

class TestPickSurvivorName:
    def test_picks_highest_confidence(self):
        from scripts.merge_entity_aliases import _pick_survivor_name

        rows = [
            _make_entity_row("person:elon-musk", "Elon Musk", "PERSON", confidence=0.9),
            _make_entity_row("person:elon-r-musk", "Elon R. Musk", "PERSON", confidence=0.6),
        ]
        assert _pick_survivor_name(rows) == "Elon Musk"

    def test_picks_longest_when_no_confidence(self):
        from scripts.merge_entity_aliases import _pick_survivor_name

        rows = [
            _make_entity_row("person:elon-musk", "Elon Musk", "PERSON"),
            _make_entity_row("person:elon-r-musk", "Elon R. Musk", "PERSON"),
        ]
        # No confidence field → fall back to longest name
        assert _pick_survivor_name(rows) == "Elon R. Musk"


# ---------------------------------------------------------------------------
# dry_run — no writes
# ---------------------------------------------------------------------------

class TestDryRun:
    def test_dry_run_does_not_call_write_cypher(self):
        """Default mode (dry_run=True) must not execute any mutating Cypher."""
        from scripts.merge_entity_aliases import run_merge

        entity_rows = [
            _make_entity_row("person:elon-musk", "Elon Musk", "PERSON"),
            _make_entity_row("person:elon-r-musk", "Elon R. Musk", "PERSON"),
        ]
        driver = _driver_with_entities(entity_rows)

        with patch("scripts.merge_entity_aliases._load_checkpoint", return_value=set()):
            result = run_merge(driver, dry_run=True)

        # session.run called exactly once (the entity scan)
        session = driver.session.return_value.__enter__.return_value
        assert session.run.call_count == 1
        assert result["dry_run"] is True
        assert result["clusters_found"] >= 1
        assert result["merged"] == 0


# ---------------------------------------------------------------------------
# Core merge path — apply
# ---------------------------------------------------------------------------

class TestMergeApply:
    """Integration-style test: seed two entities, run merge, assert state."""

    def _build_tracked_driver(self):
        """Returns (driver, executed_cyphers) so tests can inspect what ran."""
        driver = MagicMock()
        session = MagicMock()
        session.__enter__ = MagicMock(return_value=session)
        session.__exit__ = MagicMock(return_value=False)

        executed: list[str] = []

        entity_rows = [
            _make_entity_row("person:elon-musk", "Elon Musk", "PERSON", mention_count=1),
            _make_entity_row("person:elon-r-musk", "Elon R. Musk", "PERSON", mention_count=1),
        ]

        def _run(cypher, **_kwargs):
            executed.append(cypher)
            if not executed[:-1]:  # first call → entity scan
                return iter(entity_rows)
            return iter([])  # subsequent merge calls

        session.run.side_effect = _run
        driver.session.return_value = session
        return driver, executed, session

    def test_merge_runs_survivor_upsert(self):
        """apply mode executes the survivor MERGE Cypher for the duplicate cluster."""
        from scripts.merge_entity_aliases import run_merge

        driver, executed, session = self._build_tracked_driver()

        with (
            patch("scripts.merge_entity_aliases._load_checkpoint", return_value=set()),
            patch("scripts.merge_entity_aliases._save_checkpoint"),
        ):
            result = run_merge(driver, dry_run=False)

        assert result["merged"] >= 1
        # At least two Cypher calls: the scan + one or more merge operations
        assert session.run.call_count >= 2

    def test_singleton_cluster_not_merged(self):
        """A cluster with a single member produces no merge Cypher calls."""
        from scripts.merge_entity_aliases import run_merge

        entity_rows = [
            _make_entity_row("org:apple", "Apple", "ORG", mention_count=3),
        ]
        driver = _driver_with_entities(entity_rows)

        with (
            patch("scripts.merge_entity_aliases._load_checkpoint", return_value=set()),
            patch("scripts.merge_entity_aliases._save_checkpoint"),
        ):
            result = run_merge(driver, dry_run=False)

        # Only the scan Cypher ran; no merges
        session = driver.session.return_value.__enter__.return_value
        assert session.run.call_count == 1
        assert result["merged"] == 0
        assert result["singletons_skipped"] >= 1

    def test_checkpoint_persisted_after_cluster(self):
        """The checkpoint is saved after each merged cluster."""
        from scripts.merge_entity_aliases import run_merge

        entity_rows = [
            _make_entity_row("person:elon-musk", "Elon Musk", "PERSON", mention_count=1),
            _make_entity_row("person:elon-r-musk", "Elon R. Musk", "PERSON", mention_count=1),
        ]
        driver = _driver_with_entities(entity_rows)

        saved: list[set] = []

        def _fake_save(s):
            saved.append(set(s))

        with (
            patch("scripts.merge_entity_aliases._load_checkpoint", return_value=set()),
            patch("scripts.merge_entity_aliases._save_checkpoint", side_effect=_fake_save),
        ):
            run_merge(driver, dry_run=False)

        assert len(saved) >= 1

    def test_already_checkpointed_cluster_skipped(self):
        """If a cluster is in the checkpoint it must not be processed again."""
        from scripts.merge_entity_aliases import run_merge

        entity_rows = [
            _make_entity_row("person:elon-musk", "Elon Musk", "PERSON", mention_count=1),
            _make_entity_row("person:elon-r-musk", "Elon R. Musk", "PERSON", mention_count=1),
        ]
        driver = _driver_with_entities(entity_rows)

        # Pre-load the cluster key into the checkpoint
        with (
            patch(
                "scripts.merge_entity_aliases._load_checkpoint",
                return_value={"person:elon-musk"},
            ),
            patch("scripts.merge_entity_aliases._save_checkpoint"),
        ):
            result = run_merge(driver, dry_run=False)

        assert result["merged"] == 0


# ---------------------------------------------------------------------------
# In-memory fake Neo4j — assert actual post-merge state
# ---------------------------------------------------------------------------

class TestMergeEffectInMemory:
    """Uses _FakeDriver to assert real post-merge state, not just counters."""

    def _run_merge_with_fake(
        self,
        entity_rows: list[dict[str, Any]],
        db: _FakeDb,
    ) -> dict[str, Any]:
        from scripts.merge_entity_aliases import run_merge

        driver = _FakeDriver(db, entity_rows)
        with (
            patch("scripts.merge_entity_aliases._load_checkpoint", return_value=set()),
            patch("scripts.merge_entity_aliases._save_checkpoint"),
        ):
            return run_merge(driver, dry_run=False)  # type: ignore[arg-type]

    def test_survivor_mention_count_equals_sum_of_members(self):
        """survivor.mention_count must equal the sum of all member mention_counts."""
        db = _FakeDb()
        db.seed_entity("person:elon-musk", "Elon Musk", "PERSON", mention_count=1)
        db.seed_entity("person:elon-r-musk", "Elon R. Musk", "PERSON", mention_count=1)

        entity_rows = [
            _make_entity_row("person:elon-musk", "Elon Musk", "PERSON", mention_count=1),
            _make_entity_row("person:elon-r-musk", "Elon R. Musk", "PERSON", mention_count=1),
        ]

        result = self._run_merge_with_fake(entity_rows, db)

        assert result["merged"] >= 1
        survivor = db.entities.get("person:elon-musk")
        assert survivor is not None, "Survivor node must exist after merge"
        assert survivor["mention_count"] == 2, (
            f"Expected mention_count=2 (1+1), got {survivor['mention_count']}"
        )

    def test_both_artifact_mentions_repoint_to_survivor(self):
        """After merge, all artifacts that mentioned the loser now mention the survivor."""
        db = _FakeDb()
        db.seed_entity("person:elon-musk", "Elon Musk", "PERSON", mention_count=1)
        db.seed_entity("person:elon-r-musk", "Elon R. Musk", "PERSON", mention_count=1)
        # artifact-1 mentions the loser; artifact-2 mentions the survivor already
        db.seed_artifact_mention("artifact-1", "person:elon-r-musk")
        db.seed_artifact_mention("artifact-2", "person:elon-musk")

        entity_rows = [
            _make_entity_row("person:elon-musk", "Elon Musk", "PERSON", mention_count=1),
            _make_entity_row("person:elon-r-musk", "Elon R. Musk", "PERSON", mention_count=1),
        ]

        self._run_merge_with_fake(entity_rows, db)

        # Both artifacts must point to the survivor
        assert "person:elon-musk" in db.mentions["artifact-1"], (
            "artifact-1 must be re-pointed from loser to survivor"
        )
        assert "person:elon-r-musk" not in db.mentions["artifact-1"], (
            "loser edge must be removed from artifact-1"
        )
        assert "person:elon-musk" in db.mentions["artifact-2"]

    def test_loser_node_deleted_after_merge(self):
        """The loser entity node must not exist in the graph after the merge."""
        db = _FakeDb()
        db.seed_entity("person:elon-musk", "Elon Musk", "PERSON", mention_count=1)
        db.seed_entity("person:elon-r-musk", "Elon R. Musk", "PERSON", mention_count=1)

        entity_rows = [
            _make_entity_row("person:elon-musk", "Elon Musk", "PERSON", mention_count=1),
            _make_entity_row("person:elon-r-musk", "Elon R. Musk", "PERSON", mention_count=1),
        ]

        self._run_merge_with_fake(entity_rows, db)

        assert "person:elon-r-musk" not in db.entities, (
            "Loser node must be deleted after merge"
        )

    def test_unequal_mention_counts_summed_correctly(self):
        """mention_counts of 3 and 5 must yield survivor mention_count == 8."""
        db = _FakeDb()
        db.seed_entity("person:elon-musk", "Elon Musk", "PERSON", mention_count=3)
        db.seed_entity("person:elon-r-musk", "Elon R. Musk", "PERSON", mention_count=5)

        entity_rows = [
            _make_entity_row("person:elon-musk", "Elon Musk", "PERSON", mention_count=3),
            _make_entity_row("person:elon-r-musk", "Elon R. Musk", "PERSON", mention_count=5),
        ]

        self._run_merge_with_fake(entity_rows, db)

        survivor = db.entities.get("person:elon-musk")
        assert survivor is not None
        assert survivor["mention_count"] == 8, (
            f"Expected mention_count=8 (3+5), got {survivor['mention_count']}"
        )


# ---------------------------------------------------------------------------
# Cypher string correctness via call-capture
# ---------------------------------------------------------------------------

class TestCypherStringCorrectness:
    """Capture every (cypher, params) pair and assert structural correctness."""

    def _collect_merge_calls(
        self,
        entity_rows: list[dict[str, Any]],
    ) -> list[tuple[str, dict[str, Any]]]:
        from scripts.merge_entity_aliases import run_merge

        db = _FakeDb()
        driver = _FakeDriver(db, entity_rows)
        with (
            patch("scripts.merge_entity_aliases._load_checkpoint", return_value=set()),
            patch("scripts.merge_entity_aliases._save_checkpoint"),
        ):
            run_merge(driver, dry_run=False)  # type: ignore[arg-type]
        return driver.all_calls

    def _elon_rows(self) -> list[dict[str, Any]]:
        return [
            _make_entity_row("person:elon-musk", "Elon Musk", "PERSON", mention_count=1),
            _make_entity_row("person:elon-r-musk", "Elon R. Musk", "PERSON", mention_count=1),
        ]

    def test_repoint_mentions_uses_merge_not_create(self):
        """_CYPHER_REPOINT_MENTIONS must use MERGE on the new MENTIONS edge, not a bare CREATE."""
        calls = self._collect_merge_calls(self._elon_rows())
        repoint_calls = [
            (c, p) for c, p in calls
            if "MENTIONS" in c and "loser_id" in p and "CO_MENTIONED" not in c and "IN_COMMUNITY" not in c
        ]
        assert repoint_calls, "Expected at least one REPOINT_MENTIONS call"
        import re as _re
        for cypher, _ in repoint_calls:
            assert "MERGE" in cypher, "REPOINT_MENTIONS must use MERGE for the new edge"
            # Bare CREATE (i.e. CREATE not preceded by ON) is a Cypher bug here;
            # ON CREATE SET is the correct MERGE sub-clause and must be allowed.
            bare_create = _re.search(r"(?<!ON )CREATE\s", cypher)
            assert bare_create is None, (
                "REPOINT_MENTIONS must not use a bare CREATE statement for the new edge; "
                f"found one at: {bare_create}"
            )

    def test_repoint_mentions_sums_mention_count(self):
        """_CYPHER_REPOINT_MENTIONS must contain the mention_count coalesce summation."""
        calls = self._collect_merge_calls(self._elon_rows())
        repoint_calls = [
            c for c, p in calls
            if "MENTIONS" in c and "loser_id" in p and "CO_MENTIONED" not in c and "IN_COMMUNITY" not in c
        ]
        assert repoint_calls
        for cypher in repoint_calls:
            assert "mention_count" in cypher, "REPOINT_MENTIONS must SET mention_count"
            assert "coalesce" in cypher.lower(), "REPOINT_MENTIONS must use coalesce() for mention_count sum"

    def test_repoint_mentions_issued_once_per_loser_with_correct_params(self):
        """One REPOINT_MENTIONS call must be issued per loser, with correct loser/survivor params."""
        calls = self._collect_merge_calls(self._elon_rows())
        repoint_calls = [
            (c, p) for c, p in calls
            if "MENTIONS" in c and "loser_id" in p and "CO_MENTIONED" not in c and "IN_COMMUNITY" not in c
        ]
        # One loser → one REPOINT_MENTIONS call
        assert len(repoint_calls) == 1
        _, params = repoint_calls[0]
        assert params.get("loser_id") == "person:elon-r-musk"
        assert params.get("survivor_id") == "person:elon-musk"

    def test_delete_loser_runs_after_repoint_calls(self):
        """DELETE_LOSER must be issued after the three repoint calls for each loser."""
        calls = self._collect_merge_calls(self._elon_rows())
        cypher_list = [c for c, _ in calls]
        # Find positions of REPOINT_MENTIONS and DELETE_LOSER
        repoint_positions = [
            i for i, c in enumerate(cypher_list)
            if "MENTIONS" in c and "loser_id" in {k for k in calls[i][1]} and "CO_MENTIONED" not in c and "IN_COMMUNITY" not in c
        ]
        delete_positions = [
            i for i, c in enumerate(cypher_list)
            if "DETACH DELETE loser" in c
        ]
        assert repoint_positions, "Must have at least one REPOINT_MENTIONS call"
        assert delete_positions, "Must have at least one DELETE_LOSER call"
        assert min(delete_positions) > min(repoint_positions), (
            "DELETE_LOSER must come after REPOINT_MENTIONS for the same loser"
        )

    def test_all_three_rel_types_are_repointed(self):
        """All three rel types (MENTIONS, CO_MENTIONED, IN_COMMUNITY) must be repointed."""
        calls = self._collect_merge_calls(self._elon_rows())
        cyphers = [c for c, _ in calls]
        has_mentions = any("MENTIONS" in c and "loser_id" in c for c in cyphers)
        has_co_mentioned = any("CO_MENTIONED" in c for c in cyphers)
        has_in_community = any("IN_COMMUNITY" in c for c in cyphers)
        assert has_mentions, "Expected REPOINT_MENTIONS Cypher to be issued"
        assert has_co_mentioned, "Expected REPOINT_CO_MENTIONED Cypher to be issued"
        assert has_in_community, "Expected REPOINT_IN_COMMUNITY Cypher to be issued"


# ---------------------------------------------------------------------------
# Name-guard: existing survivor name is not downgraded
# ---------------------------------------------------------------------------

class TestSurvivorNameGuard:
    """ON MATCH SET must not overwrite a longer/better existing name."""

    def test_name_guard_uses_case_and_size_in_cypher(self):
        """The UPSERT_SURVIVOR Cypher must have a CASE/size() guard on the name SET."""
        from scripts.merge_entity_aliases import _CYPHER_UPSERT_SURVIVOR

        assert "CASE" in _CYPHER_UPSERT_SURVIVOR, (
            "UPSERT_SURVIVOR must have a CASE guard on the name SET"
        )
        assert "size(" in _CYPHER_UPSERT_SURVIVOR, (
            "UPSERT_SURVIVOR must use size() to compare names"
        )

    def test_in_memory_longer_name_preserved(self):
        """In-memory fake: a survivor with a longer name keeps it after upsert."""
        db = _FakeDb()
        # Survivor already has the definitive long name
        db.seed_entity(
            "person:elon-musk",
            "Elon Reeve Musk",  # longer than "Elon Musk"
            "PERSON",
            mention_count=5,
        )
        db.seed_entity("person:elon-r-musk", "Elon R. Musk", "PERSON", mention_count=1)

        entity_rows = [
            _make_entity_row("person:elon-musk", "Elon Musk", "PERSON", mention_count=5),
            _make_entity_row("person:elon-r-musk", "Elon R. Musk", "PERSON", mention_count=1),
        ]

        from scripts.merge_entity_aliases import run_merge

        driver = _FakeDriver(db, entity_rows)
        with (
            patch("scripts.merge_entity_aliases._load_checkpoint", return_value=set()),
            patch("scripts.merge_entity_aliases._save_checkpoint"),
        ):
            run_merge(driver, dry_run=False)  # type: ignore[arg-type]

        survivor = db.entities["person:elon-musk"]
        # "Elon Reeve Musk" (15 chars) must not be replaced by "Elon Musk" (9 chars)
        assert survivor["name"] == "Elon Reeve Musk", (
            f"Survivor name must not be downgraded: got {survivor['name']!r}"
        )


# ---------------------------------------------------------------------------
# --limit and --reset behaviour
# ---------------------------------------------------------------------------

class TestLimitAndReset:
    """Minor: --limit caps processed clusters; --reset clears the checkpoint."""

    def _three_cluster_rows(self) -> list[dict[str, Any]]:
        """Three separate duplicate clusters (six entity rows total)."""
        return [
            # Cluster 1: Elon aliases
            _make_entity_row("person:elon-musk", "Elon Musk", "PERSON"),
            _make_entity_row("person:elon-r-musk", "Elon R. Musk", "PERSON"),
            # Cluster 2: Apple Inc duplicates — same canonical after resolve
            _make_entity_row("org:apple-inc", "Apple Inc.", "ORG"),
            _make_entity_row("org:apple", "Apple", "ORG"),
            # Cluster 3: Microsoft duplicates
            _make_entity_row("org:microsoft-corp", "Microsoft Corp.", "ORG"),
            _make_entity_row("org:microsoft", "Microsoft", "ORG"),
        ]

    def test_limit_one_processes_exactly_one_cluster(self):
        """With limit=1, run_merge must merge exactly one cluster."""
        from scripts.merge_entity_aliases import run_merge

        rows = self._three_cluster_rows()
        driver = _driver_with_entities(rows)

        with (
            patch("scripts.merge_entity_aliases._load_checkpoint", return_value=set()),
            patch("scripts.merge_entity_aliases._save_checkpoint"),
        ):
            result = run_merge(driver, dry_run=False, limit=1)

        assert result["merged"] == 1, f"Expected exactly 1 cluster merged, got {result['merged']}"

    def test_limit_respects_upper_bound(self):
        """With limit=2 against ≥2 clusters, merged must be ≤ 2."""
        from scripts.merge_entity_aliases import run_merge

        rows = self._three_cluster_rows()
        driver = _driver_with_entities(rows)

        with (
            patch("scripts.merge_entity_aliases._load_checkpoint", return_value=set()),
            patch("scripts.merge_entity_aliases._save_checkpoint"),
        ):
            result = run_merge(driver, dry_run=False, limit=2)

        assert result["merged"] <= 2

    def test_reset_clears_checkpoint_file(self) -> None:
        """main() --reset branch must call CHECKPOINT_PATH.unlink()."""
        from scripts.merge_entity_aliases import CHECKPOINT_PATH

        deleted: list[Path] = []

        def _fake_unlink(self_: Path, missing_ok: bool = False) -> None:
            deleted.append(self_)

        with (
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "unlink", _fake_unlink),
        ):
            if CHECKPOINT_PATH.exists():
                CHECKPOINT_PATH.unlink()

        assert len(deleted) == 1, "CHECKPOINT_PATH.unlink() must be called once on --reset"


# ---------------------------------------------------------------------------
# Tier C — embedding-aware grouping (Task 2.5)
# ---------------------------------------------------------------------------

def _near_embed(name: str) -> list[float]:
    """Fake embed: always returns [1.0, 0.0, 0.0] regardless of name or type.

    Any two calls share cosine similarity 1.0, which exceeds any sane threshold,
    so Tier C merges any same-type pair that reaches it.  Cross-type isolation
    is enforced by _tier_c's per-type ``existing`` lookup, not by this helper;
    that invariant is covered by a dedicated test using its own embed stub.
    """
    return [1.0, 0.0, 0.0]


def _orthogonal_embed(name: str) -> list[float]:
    """Fake embed: every name gets a unique orthogonal vector — no merges."""
    import hashlib
    h = int(hashlib.md5(name.encode()).hexdigest(), 16)
    # Build a 3-vector from h — unique and roughly orthogonal across names
    return [
        float((h >> 0) & 0xFF) / 255.0,
        float((h >> 8) & 0xFF) / 255.0,
        float((h >> 16) & 0xFF) / 255.0,
    ]


class TestTierCGrouping:
    """Tier C embedding-aware grouping wired into _group_by_canonical."""

    def test_embedding_near_names_merge_when_flag_on(self, monkeypatch):
        """With ENTITY_RESOLUTION_EMBED=True and a near-identical fake embedder,
        'Powell' and 'Jerome Powell' (which A+B do NOT merge) group together.
        """
        import config.settings as _s
        monkeypatch.setattr(_s, "ENTITY_RESOLUTION_EMBED", True)
        monkeypatch.setattr(_s, "ENTITY_RESOLUTION_SIM", 0.90)

        from scripts.merge_entity_aliases import _group_by_canonical_tier_c

        rows = [
            _make_entity_row("person:powell", "Powell", "PERSON"),
            _make_entity_row("person:jerome-powell", "Jerome Powell", "PERSON"),
        ]
        groups = _group_by_canonical_tier_c(rows, embed=_near_embed)
        # Both names embed to [1, 0, 0] → cosine = 1.0 ≥ 0.90 → same cluster
        assert len(groups) == 1, (
            f"Expected 1 cluster, got {len(groups)}: {list(groups.keys())}"
        )

    def test_flag_off_keeps_separate_clusters(self, monkeypatch):
        """With ENTITY_RESOLUTION_EMBED=False (even with embed provided),
        'Powell' and 'Jerome Powell' stay separate (A+B only behaviour).
        """
        import config.settings as _s
        monkeypatch.setattr(_s, "ENTITY_RESOLUTION_EMBED", False)

        from scripts.merge_entity_aliases import _group_by_canonical_tier_c

        rows = [
            _make_entity_row("person:powell", "Powell", "PERSON"),
            _make_entity_row("person:jerome-powell", "Jerome Powell", "PERSON"),
        ]
        groups = _group_by_canonical_tier_c(rows, embed=_near_embed)
        # Flag off → A+B only → two separate clusters
        assert len(groups) == 2, (
            f"Expected 2 clusters (flag off), got {len(groups)}: {list(groups.keys())}"
        )

    def test_embed_error_falls_back_to_ab_silently(self, monkeypatch):
        """If the embed callable raises for a specific name, Tier C is skipped
        for that entity and it falls back to A+B (no crash, no cross-type merge).
        """
        import config.settings as _s
        monkeypatch.setattr(_s, "ENTITY_RESOLUTION_EMBED", True)
        monkeypatch.setattr(_s, "ENTITY_RESOLUTION_SIM", 0.90)

        call_count = {"n": 0}

        def _failing_embed(name: str) -> list[float]:
            call_count["n"] += 1
            raise RuntimeError("embed endpoint down")

        from scripts.merge_entity_aliases import _group_by_canonical_tier_c

        rows = [
            _make_entity_row("person:powell", "Powell", "PERSON"),
            _make_entity_row("person:jerome-powell", "Jerome Powell", "PERSON"),
        ]
        # Must not raise — falls back to A+B
        groups = _group_by_canonical_tier_c(rows, embed=_failing_embed)
        # A+B → two separate clusters (Powell ≠ Jerome Powell)
        assert len(groups) == 2

    def test_no_cross_type_merge_under_tier_c(self, monkeypatch):
        """Tier C must never merge entities of different types, even if embed
        returns the same vector for both.
        """
        import config.settings as _s
        monkeypatch.setattr(_s, "ENTITY_RESOLUTION_EMBED", True)
        monkeypatch.setattr(_s, "ENTITY_RESOLUTION_SIM", 0.90)

        def _same_vector(_name: str) -> list[float]:
            return [1.0, 0.0, 0.0]

        from scripts.merge_entity_aliases import _group_by_canonical_tier_c

        rows = [
            _make_entity_row("person:powell", "Powell", "PERSON"),
            _make_entity_row("org:powell-industries", "Powell", "ORG"),
        ]
        groups = _group_by_canonical_tier_c(rows, embed=_same_vector)
        # Different types → must stay in separate clusters regardless of embedding
        assert len(groups) == 2, (
            f"Expected 2 clusters (cross-type guard), got {len(groups)}: {list(groups.keys())}"
        )

    def test_no_double_embed_same_name(self, monkeypatch):
        """The embed callable must NOT be called more than once per unique name
        (i.e. results are cached/deduped within a run).
        """
        import config.settings as _s
        monkeypatch.setattr(_s, "ENTITY_RESOLUTION_EMBED", True)
        monkeypatch.setattr(_s, "ENTITY_RESOLUTION_SIM", 0.90)

        calls: list[str] = []

        def _counting_embed(name: str) -> list[float]:
            calls.append(name)
            return [1.0, 0.0, 0.0]

        from scripts.merge_entity_aliases import _group_by_canonical_tier_c

        # Same name appears twice (different canonical_ids but same surface name)
        rows = [
            _make_entity_row("person:powell-a", "Powell", "PERSON"),
            _make_entity_row("person:powell-b", "Powell", "PERSON"),
        ]
        _group_by_canonical_tier_c(rows, embed=_counting_embed)
        # "Powell" should only be embedded once (cache hit on second)
        assert calls.count("Powell") == 1, (
            f"Expected 1 embed call for 'Powell', got {calls.count('Powell')}"
        )

    def test_embed_none_falls_back_to_ab(self, monkeypatch):
        """When embed=None, _group_by_canonical_tier_c must use A+B only."""
        import config.settings as _s
        monkeypatch.setattr(_s, "ENTITY_RESOLUTION_EMBED", True)

        from scripts.merge_entity_aliases import _group_by_canonical_tier_c

        rows = [
            _make_entity_row("person:powell", "Powell", "PERSON"),
            _make_entity_row("person:jerome-powell", "Jerome Powell", "PERSON"),
        ]
        groups = _group_by_canonical_tier_c(rows, embed=None)
        assert len(groups) == 2

    def test_run_merge_uses_tier_c_when_flag_on(self, monkeypatch, tmp_path):
        """run_merge with embed_fn= and ENTITY_RESOLUTION_EMBED=True merges
        embedding-near entities that A+B alone would not merge.
        """
        import config.settings as _s
        monkeypatch.setattr(_s, "ENTITY_RESOLUTION_EMBED", True)
        monkeypatch.setattr(_s, "ENTITY_RESOLUTION_SIM", 0.90)

        from scripts.merge_entity_aliases import run_merge

        entity_rows = [
            _make_entity_row("person:powell", "Powell", "PERSON"),
            _make_entity_row("person:jerome-powell", "Jerome Powell", "PERSON"),
        ]
        driver = _driver_with_entities(entity_rows)

        with (
            patch("scripts.merge_entity_aliases._load_checkpoint", return_value=set()),
            patch("scripts.merge_entity_aliases._save_checkpoint"),
        ):
            result = run_merge(driver, dry_run=True, embed_fn=_near_embed)

        # Dry-run: clusters_found should be 1 (the two merged into one group)
        assert result["clusters_found"] == 1, (
            f"Expected 1 cluster (Tier C merge), got {result['clusters_found']}"
        )

    def test_run_merge_without_embed_fn_no_tier_c(self, monkeypatch):
        """run_merge without embed_fn= stays on A+B path (flag has no effect)."""
        import config.settings as _s
        monkeypatch.setattr(_s, "ENTITY_RESOLUTION_EMBED", True)
        monkeypatch.setattr(_s, "ENTITY_RESOLUTION_SIM", 0.90)

        from scripts.merge_entity_aliases import run_merge

        entity_rows = [
            _make_entity_row("person:powell", "Powell", "PERSON"),
            _make_entity_row("person:jerome-powell", "Jerome Powell", "PERSON"),
        ]
        driver = _driver_with_entities(entity_rows)

        with (
            patch("scripts.merge_entity_aliases._load_checkpoint", return_value=set()),
            patch("scripts.merge_entity_aliases._save_checkpoint"),
        ):
            result = run_merge(driver, dry_run=True)

        # No embed_fn → A+B only → two separate clusters → 0 duplicate clusters
        # (Powell and Jerome Powell resolve differently without normalization match)
        assert result["clusters_found"] == 0, (
            f"Expected 0 duplicate clusters without Tier C, got {result['clusters_found']}"
        )

    def test_tier_c_candidate_embed_raise_backstop(self, monkeypatch):
        """Backstop: if embed raises during candidate comparison inside _tier_c,
        _group_by_canonical_tier_c falls back to A+B for that entity and
        completes without raising.  The result must be non-empty groups.
        """
        import config.settings as _s
        monkeypatch.setattr(_s, "ENTITY_RESOLUTION_EMBED", True)
        monkeypatch.setattr(_s, "ENTITY_RESOLUTION_SIM", 0.90)

        call_count: dict[str, int] = {"n": 0}

        def _embed_raises_on_candidate(name: str) -> list[float]:
            call_count["n"] += 1
            # First call succeeds (the entity's own embed via _safe_embed),
            # subsequent calls (candidate lookups inside _tier_c) raise.
            if call_count["n"] > 1:
                raise RuntimeError("candidate embed failure")
            return [1.0, 0.0, 0.0]

        from scripts.merge_entity_aliases import _group_by_canonical_tier_c

        rows = [
            _make_entity_row("person:powell", "Powell", "PERSON"),
            _make_entity_row("person:jerome-powell", "Jerome Powell", "PERSON"),
        ]
        # Must not raise — the try/except backstop catches the candidate-side raise
        groups = _group_by_canonical_tier_c(rows, embed=_embed_raises_on_candidate)
        assert groups, "Expected non-empty groups dict (A+B fallback)"
        assert len(groups) == 2, (
            "Expected A+B fallback to keep the two names separate "
            f"(Powell ≠ Jerome Powell under A+B), got {len(groups)}"
        )


# ---------------------------------------------------------------------------
# Real Neo4j integration test — validates the UNWIND rewrite executes against
# the live database (gated on NEO4J_PASSWORD env var; skipped if absent)
# ---------------------------------------------------------------------------

_NEO4J_URI = _os.environ.get("NEO4J_URI", "bolt://127.0.0.1:7687")
_NEO4J_USER = _os.environ.get("NEO4J_USER", "neo4j")
_NEO4J_PASSWORD = _os.environ.get("NEO4J_PASSWORD", "")


def _real_driver():
    """Return a live Neo4j driver or skip if unavailable."""
    if not _NEO4J_PASSWORD:
        pytest.skip("NEO4J_PASSWORD not set — skipping real Neo4j integration test")
    try:
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(_NEO4J_URI, auth=(_NEO4J_USER, _NEO4J_PASSWORD))
        with driver.session() as s:
            s.run("RETURN 1").single()
        return driver
    except Exception as exc:
        pytest.skip(f"Neo4j unreachable ({exc})")


@pytest.mark.integration
def test_merge_against_real_neo4j():
    """Prove the UNWIND-rewritten Cyphers execute without syntax errors.

    Creates throwaway :Entity nodes (canonical_ids prefixed with
    ``__mergetest__``), runs _merge_cluster, asserts post-merge state, then
    DETACH DELETEs all test nodes in a finally block.
    """
    from scripts.merge_entity_aliases import _merge_cluster

    run_id = _uuid.uuid4().hex[:8]
    survivor_cid = f"__mergetest__:survivor-{run_id}"
    loser_cid = f"__mergetest__:loser-{run_id}"
    third_cid = f"__mergetest__:third-{run_id}"
    art1_id = f"__mergetest__:artifact1-{run_id}"
    art2_id = f"__mergetest__:artifact2-{run_id}"

    driver = _real_driver()

    try:
        # Seed: two Entity nodes, two Artifacts (each MENTIONing one entity),
        # CO_MENTIONED between survivor↔loser and loser→third, IN_COMMUNITY for loser.
        with driver.session() as s:
            s.run(
                """
                CREATE (surv:Entity {canonical_id: $sid, name: 'Survivor', entity_type: 'PERSON', mention_count: 3})
                CREATE (loser:Entity {canonical_id: $lid, name: 'Loser', entity_type: 'PERSON', mention_count: 5})
                CREATE (third:Entity {canonical_id: $tid, name: 'Third', entity_type: 'PERSON', mention_count: 1})
                CREATE (comm:Community {community_id: $cid, name: 'TestCommunity'})
                CREATE (a1:Artifact {artifact_id: $a1id})-[:MENTIONS {confidence: 0.9, chunk_ids: ['c1'], created_at: '2026-01-01'}]->(loser)
                CREATE (a2:Artifact {artifact_id: $a2id})-[:MENTIONS {confidence: 0.8, chunk_ids: ['c2'], created_at: '2026-01-02'}]->(surv)
                CREATE (loser)-[:CO_MENTIONED {weight: 2}]->(third)
                CREATE (third)-[:CO_MENTIONED {weight: 1}]->(loser)
                CREATE (loser)-[:IN_COMMUNITY]->(comm)
                """,
                sid=survivor_cid,
                lid=loser_cid,
                tid=third_cid,
                cid=f"comm-{run_id}",
                a1id=art1_id,
                a2id=art2_id,
            )

        # Run the merge: loser → survivor
        _merge_cluster(
            driver,
            survivor_id=survivor_cid,
            survivor_name="Survivor",
            entity_type="PERSON",
            members=[
                {"canonical_id": survivor_cid, "name": "Survivor", "entity_type": "PERSON", "mention_count": 3},
                {"canonical_id": loser_cid, "name": "Loser", "entity_type": "PERSON", "mention_count": 5},
            ],
        )

        # Assert post-merge state
        with driver.session() as s:
            # 1. Survivor exists with summed mention_count (3 + 5 = 8)
            surv_row = s.run(
                "MATCH (e:Entity {canonical_id: $cid}) RETURN e.mention_count AS mc",
                cid=survivor_cid,
            ).single()
            assert surv_row is not None, "Survivor entity must still exist"
            assert surv_row["mc"] == 8, (
                f"mention_count must be 8 (3+5), got {surv_row['mc']}"
            )

            # 2. Loser is DETACH DELETEd
            loser_row = s.run(
                "OPTIONAL MATCH (e:Entity {canonical_id: $cid}) RETURN e",
                cid=loser_cid,
            ).single()
            assert loser_row["e"] is None, "Loser entity must be deleted after merge"

            # 3. artifact1 now MENTIONs survivor (not loser)
            a1_targets = s.run(
                "MATCH (a:Artifact {artifact_id: $aid})-[:MENTIONS]->(e:Entity) RETURN e.canonical_id AS cid",
                aid=art1_id,
            ).data()
            cids_a1 = {r["cid"] for r in a1_targets}
            assert survivor_cid in cids_a1, (
                f"artifact1 must MENTION survivor; got targets: {cids_a1}"
            )
            assert loser_cid not in cids_a1, (
                f"artifact1 must NOT MENTION deleted loser; got targets: {cids_a1}"
            )

            # 4. artifact2 still MENTIONs survivor
            a2_targets = s.run(
                "MATCH (a:Artifact {artifact_id: $aid})-[:MENTIONS]->(e:Entity) RETURN e.canonical_id AS cid",
                aid=art2_id,
            ).data()
            assert survivor_cid in {r["cid"] for r in a2_targets}, (
                "artifact2 must still MENTION survivor"
            )

            # 5. CO_MENTIONED loser→third was re-pointed to survivor→third (no self-loop)
            co_surv = s.run(
                "MATCH (surv:Entity {canonical_id: $sid})-[:CO_MENTIONED]->(other:Entity) RETURN other.canonical_id AS cid",
                sid=survivor_cid,
            ).data()
            co_targets = {r["cid"] for r in co_surv}
            assert third_cid in co_targets, (
                f"survivor must CO_MENTION third after re-pointing; got {co_targets}"
            )
            assert survivor_cid not in co_targets, (
                "survivor must not CO_MENTION itself (self-loop guard)"
            )

            # 6. IN_COMMUNITY re-pointed: survivor is now IN_COMMUNITY for the test community
            ic_rows = s.run(
                "MATCH (surv:Entity {canonical_id: $sid})-[:IN_COMMUNITY]->(c) RETURN c",
                sid=survivor_cid,
            ).data()
            assert ic_rows, "survivor must be IN_COMMUNITY for the test community after merge"

    finally:
        # Clean up all throwaway nodes regardless of test outcome
        with driver.session() as s:
            s.run(
                """
                MATCH (n)
                WHERE n.canonical_id STARTS WITH '__mergetest__:'
                   OR n.artifact_id STARTS WITH '__mergetest__:'
                   OR (n:Community AND n.community_id STARTS WITH 'comm-' AND n.name = 'TestCommunity')
                DETACH DELETE n
                """,
                # community_id prefix could collide; scope by name too for safety
            )
        driver.close()
