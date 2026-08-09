# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Round-trip tests for the full-surface sync export/import (Task 2.4a):
:Memory nodes, :Entity nodes (+ MENTIONS edges), and conversations.

Neo4j is mocked here (see ``mock_neo4j`` in tests/conftest.py — the whole
existing test_sync.py suite mocks the driver rather than hitting a live
Neo4j instance), so "round trip" is verified at the level the rest of the
sync test suite operates: export writes the correct JSONL content, and
import issues the correct idempotent Cypher against that content. This is
called out explicitly in the task report.
"""

import json
from unittest.mock import MagicMock, patch

from app.sync._helpers import (
    ENTITIES_JSONL,
    ENTITY_EDGES_JSONL,
    MEMORIES_JSONL,
    MEMORY_EDGES_JSONL,
    NEO4J_SUBDIR,
)

# ---------------------------------------------------------------------------
# Memories
# ---------------------------------------------------------------------------


class TestExportMemories:
    def test_exports_memory_nodes_and_edges(self, mock_neo4j, tmp_path):
        from app.sync.export import export_memories

        driver, session = mock_neo4j

        memory_records = [
            {
                "id": "m1",
                "props": {
                    "id": "m1",
                    "text": "the sky is blue",
                    "source": "extraction",
                    "memory_type": "decision",
                    "confidence": 0.9,
                    "access_count": 2,
                    "base_score": 1.0,
                    "created_at": "2026-01-01T00:00:00Z",
                    "last_accessed_at": "2026-01-02T00:00:00Z",
                    "decay_anchor": "2026-01-02T00:00:00Z",
                    "status": "active",
                },
            }
        ]
        edge_records = [
            {"source_id": "m1", "rel_type": "RELATES_TO", "target_id": "a1"},
        ]

        session.run.side_effect = [iter(memory_records), iter(edge_records)]

        result = export_memories(driver, str(tmp_path))

        assert result["memories"] == 1
        assert result["memory_edges"] == 1

        mem_file = tmp_path / NEO4J_SUBDIR / MEMORIES_JSONL
        assert mem_file.exists()
        row = json.loads(mem_file.read_text().strip())
        assert row["id"] == "m1"
        assert row["props"]["text"] == "the sky is blue"
        assert row["props"]["confidence"] == 0.9

        edge_file = tmp_path / NEO4J_SUBDIR / MEMORY_EDGES_JSONL
        assert edge_file.exists()
        edge_row = json.loads(edge_file.read_text().strip())
        assert edge_row == {"source_id": "m1", "rel_type": "RELATES_TO", "target_id": "a1"}

    def test_handles_neo4j_error(self, mock_neo4j, tmp_path):
        from app.sync.export import export_memories

        driver, session = mock_neo4j
        session.run.side_effect = RuntimeError("Neo4j down")

        result = export_memories(driver, str(tmp_path))
        assert "error" in result
        assert result["memories"] == 0

    def test_empty_graph_writes_empty_files(self, mock_neo4j, tmp_path):
        from app.sync.export import export_memories

        driver, session = mock_neo4j
        session.run.side_effect = [iter([]), iter([])]

        result = export_memories(driver, str(tmp_path))
        assert result["memories"] == 0
        assert result["memory_edges"] == 0


class TestImportMemories:
    def test_merges_memory_nodes_idempotently(self, mock_neo4j, tmp_path):
        from app.sync.import_ import import_memories

        driver, session = mock_neo4j
        neo4j_dir = tmp_path / NEO4J_SUBDIR
        neo4j_dir.mkdir(parents=True)
        row = {"id": "m1", "props": {"id": "m1", "text": "hello", "status": "active"}}
        (neo4j_dir / MEMORIES_JSONL).write_text(json.dumps(row) + "\n")
        (neo4j_dir / MEMORY_EDGES_JSONL).write_text("")

        result = import_memories(driver, str(tmp_path))

        assert result["memories_merged"] == 1
        assert result["edges_merged"] == 0

        # Idempotent MERGE, not a blind CREATE
        calls = [c.args[0] for c in session.run.call_args_list if c.args]
        assert any("MERGE (m:Memory" in c for c in calls)
        assert not any(c.strip().startswith("CREATE (m:Memory") for c in calls)

    def test_merges_memory_edges(self, mock_neo4j, tmp_path):
        from app.sync.import_ import import_memories

        driver, session = mock_neo4j
        neo4j_dir = tmp_path / NEO4J_SUBDIR
        neo4j_dir.mkdir(parents=True)
        (neo4j_dir / MEMORIES_JSONL).write_text("")
        edges = [
            {"source_id": "m1", "rel_type": "RELATES_TO", "target_id": "a1"},
            {"source_id": "m1", "rel_type": "EXTRACTED_FROM", "target_id": "conv1"},
        ]
        (neo4j_dir / MEMORY_EDGES_JSONL).write_text(
            "\n".join(json.dumps(e) for e in edges) + "\n"
        )

        result = import_memories(driver, str(tmp_path))

        assert result["memories_merged"] == 0
        assert result["edges_merged"] == 2

    def test_missing_files_return_zero_counts(self, mock_neo4j, tmp_path):
        from app.sync.import_ import import_memories

        driver, _session = mock_neo4j
        result = import_memories(driver, str(tmp_path))
        assert result["memories_merged"] == 0
        assert result["edges_merged"] == 0


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------


class TestExportEntities:
    def test_exports_entity_nodes_and_mentions_edges(self, mock_neo4j, tmp_path):
        from app.sync.export import export_entities

        driver, session = mock_neo4j

        entity_records = [
            {
                "canonical_id": "e1",
                "props": {
                    "canonical_id": "e1",
                    "name": "Acme Corp",
                    "entity_type": "organization",
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:00:00Z",
                    "mention_count": 3,
                },
            }
        ]
        edge_records = [
            {
                "source_id": "a1",
                "rel_type": "MENTIONS",
                "target_id": "e1",
                "props": {"confidence": 0.8, "chunk_ids": '["c1"]', "created_at": "2026-01-01T00:00:00Z"},
            }
        ]
        session.run.side_effect = [iter(entity_records), iter(edge_records)]

        result = export_entities(driver, str(tmp_path))

        assert result["entities"] == 1
        assert result["entity_edges"] == 1

        ent_file = tmp_path / NEO4J_SUBDIR / ENTITIES_JSONL
        row = json.loads(ent_file.read_text().strip())
        assert row["canonical_id"] == "e1"
        assert row["props"]["name"] == "Acme Corp"

        edge_file = tmp_path / NEO4J_SUBDIR / ENTITY_EDGES_JSONL
        edge_row = json.loads(edge_file.read_text().strip())
        assert edge_row["target_id"] == "e1"
        assert edge_row["props"]["confidence"] == 0.8

    def test_handles_neo4j_error(self, mock_neo4j, tmp_path):
        from app.sync.export import export_entities

        driver, session = mock_neo4j
        session.run.side_effect = RuntimeError("Neo4j down")

        result = export_entities(driver, str(tmp_path))
        assert "error" in result
        assert result["entities"] == 0


class TestImportEntities:
    def test_merges_entity_nodes_idempotently(self, mock_neo4j, tmp_path):
        from app.sync.import_ import import_entities

        driver, session = mock_neo4j
        neo4j_dir = tmp_path / NEO4J_SUBDIR
        neo4j_dir.mkdir(parents=True)
        row = {"canonical_id": "e1", "props": {"canonical_id": "e1", "name": "Acme"}}
        (neo4j_dir / ENTITIES_JSONL).write_text(json.dumps(row) + "\n")
        (neo4j_dir / ENTITY_EDGES_JSONL).write_text("")

        result = import_entities(driver, str(tmp_path))

        assert result["entities_merged"] == 1
        assert result["edges_merged"] == 0

        calls = [c.args[0] for c in session.run.call_args_list if c.args]
        assert any("MERGE (e:Entity" in c for c in calls)
        assert not any(c.strip().startswith("CREATE (e:Entity") for c in calls)

    def test_merges_mentions_edges(self, mock_neo4j, tmp_path):
        from app.sync.import_ import import_entities

        driver, session = mock_neo4j
        neo4j_dir = tmp_path / NEO4J_SUBDIR
        neo4j_dir.mkdir(parents=True)
        (neo4j_dir / ENTITIES_JSONL).write_text("")
        edge = {
            "source_id": "a1", "rel_type": "MENTIONS", "target_id": "e1",
            "props": {"confidence": 0.5},
        }
        (neo4j_dir / ENTITY_EDGES_JSONL).write_text(json.dumps(edge) + "\n")

        result = import_entities(driver, str(tmp_path))

        assert result["entities_merged"] == 0
        assert result["edges_merged"] == 1

    def test_missing_files_return_zero_counts(self, mock_neo4j, tmp_path):
        from app.sync.import_ import import_entities

        driver, _session = mock_neo4j
        result = import_entities(driver, str(tmp_path))
        assert result["entities_merged"] == 0
        assert result["edges_merged"] == 0


# ---------------------------------------------------------------------------
# Chained round trip: export's actual JSONL output feeds import (not two
# separately hand-authored fixtures). This is what proves "backup then
# restore returns the same data" for the two Neo4j surfaces, mirroring
# TestImportConversations.test_restores_conversations_present_in_sync_dir
# above.
# ---------------------------------------------------------------------------


class TestMemoryEntityChainedRoundTrip:
    def test_memory_round_trip_preserves_properties(self, mock_neo4j, tmp_path):
        from app.sync.export import export_memories
        from app.sync.import_ import import_memories

        driver, session = mock_neo4j

        seeded_props = {
            "id": "m1",
            "status": "active",
            "source": "extraction",
            "memory_type": "decision",
            "text": "the sky is blue over the northern ocean",
            "confidence": 0.87,
            "created_at": "2026-01-01T00:00:00Z",
            "last_accessed_at": "2026-01-03T00:00:00Z",
            "access_count": 5,
            "decay_anchor": "2026-01-03T00:00:00Z",
            "base_score": 1.0,
        }
        # Pass a defensive copy into the mock — a real driver returns a
        # fresh dict per query, never the same object across calls. Using
        # the same object here would let an in-place mutation in the code
        # under test silently corrupt the oracle too, masking a fidelity
        # regression (verified while sanity-checking this test).
        memory_records = [{"id": "m1", "props": dict(seeded_props)}]

        # --- Export phase: the mocked driver returns the seeded node ---
        session.run.side_effect = [iter(memory_records), iter([])]
        export_result = export_memories(driver, str(tmp_path))
        assert export_result["memories"] == 1
        assert export_result["memory_edges"] == 0

        # --- Import phase: feed the REAL file export_memories just wrote,
        # from the SAME tmp_path, into the REAL import_memories ---
        session.run.reset_mock(side_effect=True)
        session.run.side_effect = None
        session.run.return_value = MagicMock()

        import_result = import_memories(driver, str(tmp_path))
        assert import_result["memories_merged"] == 1
        assert import_result["edges_merged"] == 0

        merge_calls = [
            c for c in session.run.call_args_list
            if c.args and "MERGE (m:Memory" in c.args[0]
        ]
        assert len(merge_calls) == 1
        assert merge_calls[0].kwargs["id"] == "m1"
        # The exact prop dict export wrote to memories.jsonl must be the
        # exact prop dict import restores — this is the fidelity guarantee.
        assert merge_calls[0].kwargs["props"] == seeded_props

    def test_entity_round_trip_preserves_properties(self, mock_neo4j, tmp_path):
        from app.sync.export import export_entities
        from app.sync.import_ import import_entities

        driver, session = mock_neo4j

        seeded_props = {
            "canonical_id": "e1",
            "name": "Acme Corp",
            "entity_type": "organization",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-02T00:00:00Z",
            "mention_count": 7,
            "aliases": '["Acme", "Acme Corporation"]',
        }
        # Defensive copy — see comment in test_memory_round_trip_preserves_properties.
        entity_records = [{"canonical_id": "e1", "props": dict(seeded_props)}]

        session.run.side_effect = [iter(entity_records), iter([])]
        export_result = export_entities(driver, str(tmp_path))
        assert export_result["entities"] == 1
        assert export_result["entity_edges"] == 0

        session.run.reset_mock(side_effect=True)
        session.run.side_effect = None
        session.run.return_value = MagicMock()

        import_result = import_entities(driver, str(tmp_path))
        assert import_result["entities_merged"] == 1
        assert import_result["edges_merged"] == 0

        merge_calls = [
            c for c in session.run.call_args_list
            if c.args and "MERGE (e:Entity" in c.args[0]
        ]
        assert len(merge_calls) == 1
        assert merge_calls[0].kwargs["canonical_id"] == "e1"
        assert merge_calls[0].kwargs["props"] == seeded_props

    def test_memory_reimport_is_idempotent_merge_not_duplicate(self, mock_neo4j, tmp_path):
        """Re-importing the same exported memories.jsonl twice must issue
        MERGE (never CREATE) on both runs and must not grow the set of
        distinct node ids merged. Under the mocked driver we cannot observe
        actual Neo4j node counts, so this asserts the two proxies that are
        observable: both runs emit the MERGE form (not CREATE), and both
        runs merge the identical id set — i.e. a re-import looks like a
        no-op from the caller's side, not a duplication.
        """
        from app.sync.export import export_memories
        from app.sync.import_ import import_memories

        driver, session = mock_neo4j

        seeded_props = {"id": "m1", "text": "hello", "status": "active"}
        memory_records = [{"id": "m1", "props": dict(seeded_props)}]

        session.run.side_effect = [iter(memory_records), iter([])]
        export_memories(driver, str(tmp_path))

        session.run.reset_mock(side_effect=True)
        session.run.side_effect = None
        session.run.return_value = MagicMock()

        result_1 = import_memories(driver, str(tmp_path))
        first_merge_calls = [
            c for c in session.run.call_args_list
            if c.args and "MERGE (m:Memory" in c.args[0]
        ]
        first_ids = {c.kwargs["id"] for c in first_merge_calls}

        session.run.reset_mock()  # clears call history, keeps return_value

        result_2 = import_memories(driver, str(tmp_path))
        second_merge_calls = [
            c for c in session.run.call_args_list
            if c.args and "MERGE (m:Memory" in c.args[0]
        ]
        second_ids = {c.kwargs["id"] for c in second_merge_calls}

        assert result_1["memories_merged"] == result_2["memories_merged"] == 1
        assert first_ids == second_ids == {"m1"}
        assert not any(
            c.args[0].strip().startswith("CREATE (m:Memory")
            for c in first_merge_calls + second_merge_calls
        )


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------


class TestExportConversations:
    def test_counts_existing_conversations(self, tmp_path):
        from app.sync.export import export_conversations
        from app.sync.user_state import write_conversation

        write_conversation(str(tmp_path), {"id": "c1", "title": "hello"})
        write_conversation(str(tmp_path), {"id": "c2", "title": "world"})

        result = export_conversations(str(tmp_path))
        assert result["conversations"] == 2

    def test_empty_dir_returns_zero(self, tmp_path):
        from app.sync.export import export_conversations

        result = export_conversations(str(tmp_path))
        assert result["conversations"] == 0


class TestImportConversations:
    def test_restores_conversations_present_in_sync_dir(self, tmp_path):
        """Conversations live directly in the sync dir (no separate local
        store), so the realistic round trip is: a conversation already
        landed in {sync_dir}/user/conversations/ (e.g. via Dropbox sync
        from another machine) and import_conversations positively confirms
        + re-normalizes it as part of import_all, rather than silently
        ignoring it."""
        from app.sync.import_ import import_conversations
        from app.sync.user_state import read_conversation, write_conversation

        write_conversation(str(tmp_path), {"id": "c1", "title": "hello", "messages": [1, 2]})

        result = import_conversations(str(tmp_path))
        assert result["conversations"] == 1

        restored = read_conversation(str(tmp_path), "c1")
        assert restored["id"] == "c1"
        assert restored["title"] == "hello"
        assert restored["messages"] == [1, 2]

    def test_skips_conversation_missing_id(self, tmp_path):
        from app.sync.import_ import import_conversations

        conv_dir = tmp_path / "user" / "conversations"
        conv_dir.mkdir(parents=True)
        (conv_dir / "bad.json").write_text(json.dumps({"title": "no id here"}))

        result = import_conversations(str(tmp_path))
        assert result["conversations"] == 0
        assert result.get("skipped", 0) == 1

    def test_empty_dir_returns_zero(self, tmp_path):
        from app.sync.import_ import import_conversations

        result = import_conversations(str(tmp_path))
        assert result["conversations"] == 0


# ---------------------------------------------------------------------------
# export_all / import_all wiring
# ---------------------------------------------------------------------------


class TestExportAllWiring:
    def test_includes_new_surface_counts(self, tmp_path):
        import app.sync.export as export_mod

        driver = MagicMock()
        with patch.object(
            export_mod, "export_neo4j",
            return_value={"artifacts": 0, "domains": 0, "relationships": 0,
                          "artifact_ids": set(), "output_dir": str(tmp_path)},
        ), patch.object(
            export_mod, "export_chroma",
            return_value={"domains": {}, "total_chunks": 0, "output_dir": str(tmp_path)},
        ), patch.object(
            export_mod, "export_bm25",
            return_value={"files_copied": 0, "files_skipped": 0, "output_dir": str(tmp_path)},
        ), patch.object(
            export_mod, "export_memories", return_value={"memories": 3, "memory_edges": 1},
        ) as mock_mem, patch.object(
            export_mod, "export_entities", return_value={"entities": 2, "entity_edges": 1},
        ) as mock_ent, patch.object(
            export_mod, "export_conversations", return_value={"conversations": 4},
        ) as mock_conv, patch(
            "app.sync.tombstones.export_tombstones", return_value={"tombstones_exported": 0},
        ), patch(
            "app.sync.manifest.write_manifest", return_value={"machine_id": "x"},
        ):
            result = export_mod.export_all(driver, sync_dir=str(tmp_path))

        assert result["memories"] == {"memories": 3, "memory_edges": 1}
        assert result["entities"] == {"entities": 2, "entity_edges": 1}
        assert result["conversations"] == {"conversations": 4}
        mock_mem.assert_called_once()
        mock_ent.assert_called_once()
        mock_conv.assert_called_once()


class TestImportAllWiring:
    def test_includes_new_surface_counts(self, tmp_path):
        import app.sync.import_ as import_mod

        driver = MagicMock()
        with patch.object(
            import_mod, "import_neo4j",
            return_value={"domains_merged": 0, "artifacts_created": 0, "artifacts_updated": 0,
                          "artifacts_skipped": 0, "artifacts_conflict": 0, "conflicts": [],
                          "relationships_merged": 0},
        ), patch.object(
            import_mod, "import_chroma",
            return_value={"domains": {}, "total_added": 0, "total_skipped": 0},
        ), patch.object(
            import_mod, "import_bm25",
            return_value={"files_processed": 0, "chunks_added": 0, "chunks_skipped": 0},
        ), patch.object(
            import_mod, "import_memories", return_value={"memories_merged": 3, "edges_merged": 1},
        ) as mock_mem, patch.object(
            import_mod, "import_entities", return_value={"entities_merged": 2, "edges_merged": 1},
        ) as mock_ent, patch.object(
            import_mod, "import_conversations", return_value={"conversations": 4},
        ) as mock_conv, patch(
            "app.sync.tombstones.apply_tombstones", return_value={"deleted": 0},
        ), patch(
            "app.sync.manifest.read_manifest", side_effect=FileNotFoundError,
        ):
            result = import_mod.import_all(driver, sync_dir=str(tmp_path))

        assert result["memories"] == {"memories_merged": 3, "edges_merged": 1}
        assert result["entities"] == {"entities_merged": 2, "edges_merged": 1}
        assert result["conversations"] == {"conversations": 4}
        mock_mem.assert_called_once()
        mock_ent.assert_called_once()
        mock_conv.assert_called_once()


# ---------------------------------------------------------------------------
# Manifest + status integrity coverage for the new backup surfaces
# (Task 2.4b). Task 2.4a wired memories.jsonl / memory_edges.jsonl /
# entities.jsonl / entity_edges.jsonl into export/import but NOT into the
# manifest checksum gate or the status drift report — this closes that gap.
# The load-bearing assertion is test_truncated_memories_file_fails_checksum
# _verification below: it proves a corrupted backup file is DETECTED, not
# silently restored.
# ---------------------------------------------------------------------------


class TestManifestDetectsCorruption:
    def test_truncated_memories_file_fails_checksum_verification(self, tmp_path):
        """A truncated memories.jsonl (e.g. a Dropbox sync conflict cutting
        a write short) must produce a checksum AND line-count mismatch
        against the manifest recorded at backup time — this is the exact
        failure mode the backup feature exists to catch before a silent
        bad restore."""
        from app.sync._helpers import _count_jsonl_lines, _sha256_file
        from app.sync.manifest import write_manifest

        neo4j_dir = tmp_path / NEO4J_SUBDIR
        neo4j_dir.mkdir(parents=True)
        memories_file = neo4j_dir / MEMORIES_JSONL
        memories_file.write_text(
            '{"id": "m1", "text": "the sky is blue"}\n'
            '{"id": "m2", "text": "the grass is green"}\n'
            '{"id": "m3", "text": "water is wet"}\n'
        )

        # Manifest recorded at "backup time" — the trusted checksum + count.
        manifest = write_manifest(str(tmp_path))
        recorded = manifest["files"][f"{NEO4J_SUBDIR}/{MEMORIES_JSONL}"]
        assert recorded["exists"] is True
        assert recorded["count"] == 3

        # Corrupt the file after backup by truncating it mid-write.
        raw = memories_file.read_bytes()
        memories_file.write_bytes(raw[: len(raw) // 2])

        # Re-run the same primitives write_manifest uses (sha256 + line
        # count) against the now-corrupted file and compare against what
        # was recorded at backup time — the verification a restore
        # workflow must perform before trusting a backup file.
        current_checksum = _sha256_file(str(memories_file))
        current_count = _count_jsonl_lines(str(memories_file))

        assert current_checksum != recorded["sha256"], (
            "truncated file must be caught by checksum mismatch"
        )
        assert current_count != recorded["count"], (
            "truncation must also be visible as a line-count mismatch"
        )

    def test_single_byte_corruption_still_fails_checksum(self, tmp_path):
        """Even a corruption that preserves line count (a byte flipped
        inside a line) must be caught — sha256, not line count, is the
        primary integrity signal."""
        from app.sync._helpers import _sha256_file
        from app.sync.manifest import write_manifest

        neo4j_dir = tmp_path / NEO4J_SUBDIR
        neo4j_dir.mkdir(parents=True)
        entities_file = neo4j_dir / ENTITIES_JSONL
        entities_file.write_text('{"canonical_id": "e1", "name": "Acme Corp"}\n')

        manifest = write_manifest(str(tmp_path))
        recorded = manifest["files"][f"{NEO4J_SUBDIR}/{ENTITIES_JSONL}"]

        raw = bytearray(entities_file.read_bytes())
        raw[10] = (raw[10] + 1) % 256
        entities_file.write_bytes(bytes(raw))

        current_checksum = _sha256_file(str(entities_file))
        assert current_checksum != recorded["sha256"]

    def test_absent_edge_file_reports_exists_false_without_error(self, tmp_path):
        """A legitimately-absent edge file (no memory/entity edges
        extracted yet) must report exists: False, not raise."""
        from app.sync.manifest import write_manifest

        neo4j_dir = tmp_path / NEO4J_SUBDIR
        neo4j_dir.mkdir(parents=True)
        (neo4j_dir / MEMORIES_JSONL).write_text('{"id": "m1"}\n')
        (neo4j_dir / ENTITIES_JSONL).write_text('{"canonical_id": "e1"}\n')
        # memory_edges.jsonl / entity_edges.jsonl never written

        manifest = write_manifest(str(tmp_path))
        files = manifest["files"]

        assert files[f"{NEO4J_SUBDIR}/{MEMORY_EDGES_JSONL}"] == {
            "exists": False, "count": 0, "sha256": "",
        }
        assert files[f"{NEO4J_SUBDIR}/{ENTITY_EDGES_JSONL}"] == {
            "exists": False, "count": 0, "sha256": "",
        }


class TestCompareStatusNewSurfaces:
    @staticmethod
    def _single(value):
        m = MagicMock()
        m.single.return_value = {"n": value}
        return m

    def test_reports_memories_and_entities_counts(self, mock_neo4j, tmp_path):
        from app.sync.manifest import write_manifest
        from app.sync.status import compare_status

        driver, session = mock_neo4j
        # Query order in compare_status: artifacts, domains, relationships,
        # memories, entities.
        session.run.side_effect = [
            self._single(0), self._single(0), self._single(0),
            self._single(3), self._single(2),
        ]

        neo4j_dir = tmp_path / NEO4J_SUBDIR
        neo4j_dir.mkdir(parents=True)
        (neo4j_dir / MEMORIES_JSONL).write_text(
            "\n".join(json.dumps({"id": f"m{i}"}) for i in range(3)) + "\n"
        )
        (neo4j_dir / ENTITIES_JSONL).write_text(
            "\n".join(json.dumps({"canonical_id": f"e{i}"}) for i in range(2)) + "\n"
        )
        write_manifest(str(tmp_path))

        with patch("app.sync.status.httpx.get", side_effect=ConnectionError("no chroma")):
            result = compare_status(
                driver, chroma_url="http://chroma", redis_client=None, sync_dir=str(tmp_path)
            )

        assert result["local"]["neo4j_memories"] == 3
        assert result["local"]["neo4j_entities"] == 2
        assert result["sync"]["neo4j_memories"] == 3
        assert result["sync"]["neo4j_entities"] == 2
        assert result["diff"]["neo4j_memories"] == 0
        assert result["diff"]["neo4j_entities"] == 0

    def test_drift_when_backup_is_stale(self, mock_neo4j, tmp_path):
        """A memories.jsonl backup that lags the live graph (e.g. exported
        before new memories were extracted) must surface as a non-zero
        diff — the drift-visibility half of the integrity gap."""
        from app.sync.manifest import write_manifest
        from app.sync.status import compare_status

        driver, session = mock_neo4j
        session.run.side_effect = [
            self._single(0), self._single(0), self._single(0),
            self._single(5), self._single(2),
        ]

        neo4j_dir = tmp_path / NEO4J_SUBDIR
        neo4j_dir.mkdir(parents=True)
        # Backup only captured 2 of the 5 memories now live in the graph.
        (neo4j_dir / MEMORIES_JSONL).write_text(
            "\n".join(json.dumps({"id": f"m{i}"}) for i in range(2)) + "\n"
        )
        (neo4j_dir / ENTITIES_JSONL).write_text(
            "\n".join(json.dumps({"canonical_id": f"e{i}"}) for i in range(2)) + "\n"
        )
        write_manifest(str(tmp_path))

        with patch("app.sync.status.httpx.get", side_effect=ConnectionError("no chroma")):
            result = compare_status(
                driver, chroma_url="http://chroma", redis_client=None, sync_dir=str(tmp_path)
            )

        assert result["diff"]["neo4j_memories"] == 3
        assert result["diff"]["neo4j_entities"] == 0
