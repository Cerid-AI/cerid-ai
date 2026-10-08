# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Sync import never brings back what was forgotten, and old tombstones join the registry."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

import config
from core.forget.registry import Entry, Registry, Subject


def _forgotten(reg: Registry, kind: str, sid: str, state: str = "purged") -> None:
    reg.append([Entry("fg_1", Subject(kind, sid), state, "2026-10-07T10:00:00Z", "m2", "ui")])


@pytest.fixture
def reg(tmp_path: Path, monkeypatch) -> Registry:
    registry = Registry(tmp_path / "forget", "m1")
    monkeypatch.setattr("core.forget.registry.get_registry", lambda: registry)
    return registry


def test_import_conversations_skips_a_forgotten_conversation(tmp_path: Path, monkeypatch, reg):
    from app.sync import import_
    _forgotten(reg, "conversation", "gone")
    _forgotten(reg, "conversation", "binned", state="trashed")
    written: list[str] = []
    monkeypatch.setattr(import_, "write_conversation", lambda sd, conv: written.append(conv["id"]))
    monkeypatch.setattr(
        import_, "read_conversations", lambda sd: [{"id": "gone"}, {"id": "binned"}, {"id": "kept"}],
    )
    result = import_.import_conversations(sync_dir=str(tmp_path))
    assert written == ["kept"]
    assert result["conversations"] == 1
    assert result["skipped_forgotten"] == 2


def test_a_restored_conversation_is_imported_again(tmp_path: Path, monkeypatch, reg):
    from app.sync import import_
    reg.append([
        Entry("fg_1", Subject("conversation", "back"), "trashed", "2026-10-07T10:00:00Z", "m2", "ui"),
        Entry("fg_1", Subject("conversation", "back"), "restored", "2026-10-07T11:00:00Z", "m2", "ui"),
    ])
    written: list[str] = []
    monkeypatch.setattr(import_, "write_conversation", lambda sd, conv: written.append(conv["id"]))
    monkeypatch.setattr(import_, "read_conversations", lambda sd: [{"id": "back"}])
    import_.import_conversations(sync_dir=str(tmp_path))
    assert written == ["back"]


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_neo4j_import_skips_a_forgotten_artifact(tmp_path: Path, mock_neo4j, reg):
    from app.sync import import_
    from app.sync._helpers import ARTIFACTS_JSONL, NEO4J_SUBDIR
    _forgotten(reg, "artifact", "gone-art")
    _write_jsonl(tmp_path / NEO4J_SUBDIR / ARTIFACTS_JSONL, [
        {"id": "gone-art", "filename": "a.md", "domain": "general", "ingested_at": "2026-10-01"},
        {"id": "kept-art", "filename": "b.md", "domain": "general", "ingested_at": "2026-10-01"},
    ])
    driver, session = mock_neo4j
    session.run.return_value.single.return_value = None

    result = import_.import_neo4j(driver, sync_dir=str(tmp_path))

    touched = {c.kwargs.get("id") for c in session.run.call_args_list}
    assert "gone-art" not in touched
    assert "kept-art" in touched
    assert result["artifacts_created"] == 1
    assert result["artifacts_skipped_forgotten"] == 1


def test_chroma_import_skips_rows_of_a_forgotten_artifact(tmp_path: Path, monkeypatch, reg):
    from app.sync import import_
    from app.sync._helpers import CHROMA_SUBDIR
    from tests.test_sync_chroma_roundtrip import _FakeChromaServer
    _forgotten(reg, "artifact", "gone-art")
    monkeypatch.setattr(import_.config, "DOMAINS", ["general"], raising=False)
    _write_jsonl(tmp_path / CHROMA_SUBDIR / f"{config.collection_name('general')}.jsonl", [
        {"id": "gone-art_chunk_0", "document": "x", "metadata": {"artifact_id": "gone-art"}, "embedding": [0.1]},
        {"id": "kept-art_chunk_0", "document": "y", "metadata": {"artifact_id": "kept-art"}, "embedding": [0.2]},
    ])
    server = _FakeChromaServer(seed_chunks=0)
    monkeypatch.setattr(import_, "httpx", server)

    result = import_.import_chroma(chroma_url="http://chroma.test:8000", sync_dir=str(tmp_path))

    assert set(server.written) == {"kept-art_chunk_0"}
    assert result["total_added"] == 1


def test_record_tombstone_also_records_a_purged_artifact(tmp_path: Path, monkeypatch, reg):
    from app.sync import tombstones
    monkeypatch.setattr("config.TOMBSTONE_LOG_PATH", str(tmp_path / "tombstones.jsonl"))
    tombstones.record_tombstone("a1", ["a1_chunk_0"], domain="general", filename="f.md")
    assert reg.state_of("artifact", "a1") == "purged"
    assert (tmp_path / "tombstones.jsonl").exists()


def test_a_registry_failure_still_writes_the_tombstone(tmp_path: Path, monkeypatch):
    from app.sync import tombstones

    def _broken():
        raise OSError("sync dir unwritable")

    monkeypatch.setattr("core.forget.registry.get_registry", _broken)
    log = tmp_path / "tombstones.jsonl"
    monkeypatch.setattr("config.TOMBSTONE_LOG_PATH", str(log))
    tombstones.record_tombstone("a1", [], domain="general")
    assert json.loads(log.read_text(encoding="utf-8"))["artifact_id"] == "a1"


def test_migration_is_idempotent(tmp_path: Path, monkeypatch, reg):
    from app.sync import tombstones
    log = tmp_path / "tombstones.jsonl"
    log.write_text(json.dumps({"artifact_id": "old1", "deleted_at": "2026-08-01T00:00:00+00:00"}) + "\n")
    monkeypatch.setattr("config.TOMBSTONE_LOG_PATH", str(log))
    assert tombstones.migrate_tombstones_to_registry(sync_dir=str(tmp_path)) == 1
    assert tombstones.migrate_tombstones_to_registry(sync_dir=str(tmp_path)) == 0
    assert reg.state_of("artifact", "old1") == "purged"


def test_migration_reads_synced_tombstones_from_other_machines(tmp_path: Path, monkeypatch, reg):
    from app.sync import tombstones
    from app.sync._helpers import NEO4J_SUBDIR, TOMBSTONES_JSONL
    monkeypatch.setattr("config.TOMBSTONE_LOG_PATH", str(tmp_path / "absent.jsonl"))
    _write_jsonl(tmp_path / NEO4J_SUBDIR / TOMBSTONES_JSONL, [
        {"artifact_id": "remote1", "deleted_at": "2026-09-01T00:00:00+00:00", "machine_id": "m2"},
        {"artifact_id": "remote1", "deleted_at": "2026-09-01T00:00:00+00:00", "machine_id": "m2"},
        {"deleted_at": "2026-09-01T00:00:00+00:00"},
    ])
    assert tombstones.migrate_tombstones_to_registry(sync_dir=str(tmp_path)) == 1
    [entry] = reg.latest()
    assert (entry.subject, entry.state, entry.machine_id) == (Subject("artifact", "remote1"), "purged", "m2")
    assert entry.at == "2026-09-01T00:00:00+00:00"


@pytest.fixture
def maintenance(monkeypatch, tmp_path: Path):
    """The job's three steps, recorded instead of run."""
    from app import scheduler
    from app.services.forget import engine
    from app.sync import tombstones

    calls: list[Any] = []
    monkeypatch.setattr(config, "SYNC_DIR", str(tmp_path))
    monkeypatch.setattr(
        tombstones, "migrate_tombstones_to_registry", lambda sync_dir=None: calls.append(("migrate", sync_dir)) or 2,
    )
    monkeypatch.setattr(engine, "apply_remote", lambda: calls.append(("apply",)) or {"hidden": 1})
    monkeypatch.setattr(
        engine, "empty_trash", lambda older_than_days=None: calls.append(("empty", older_than_days)) or ["fg_x"],
    )
    logged = MagicMock()
    monkeypatch.setattr(scheduler, "_log_execution", logged)
    return calls, logged


@pytest.mark.asyncio
async def test_maintenance_migrates_applies_remote_forgets_and_empties_old_trash(maintenance, monkeypatch, tmp_path):
    from app import scheduler
    calls, logged = maintenance
    monkeypatch.setattr(config, "FORGET_TRASH_DAYS", 30)
    await scheduler._run_forget_maintenance()
    assert calls == [("migrate", str(tmp_path)), ("apply",), ("empty", 30)]
    assert logged.call_args.args[:2] == ("forget_maintenance", "success")


@pytest.mark.asyncio
async def test_maintenance_never_auto_empties_when_trash_days_is_zero(maintenance, monkeypatch):
    from app import scheduler
    calls, _logged = maintenance
    monkeypatch.setattr(config, "FORGET_TRASH_DAYS", 0)
    await scheduler._run_forget_maintenance()
    assert [c[0] for c in calls] == ["migrate", "apply"]


@pytest.mark.asyncio
async def test_a_failed_migration_does_not_stop_the_other_steps(maintenance, monkeypatch):
    from app import scheduler
    from app.sync import tombstones
    calls, logged = maintenance
    monkeypatch.setattr(config, "FORGET_TRASH_DAYS", 30)

    def _boom(sync_dir=None):
        raise OSError("tombstone log unreadable")

    monkeypatch.setattr(tombstones, "migrate_tombstones_to_registry", _boom)
    await scheduler._run_forget_maintenance()
    assert [c[0] for c in calls] == ["apply", "empty"]
    assert logged.call_args.args[:2] == ("forget_maintenance", "error")


@pytest.mark.asyncio
async def test_maintenance_runs_soon_after_startup_and_then_on_a_short_interval(monkeypatch, tmp_path: Path):
    """A machine asleep at any fixed hour must still apply remote forgets and empty its trash."""
    from datetime import datetime, timedelta, timezone

    from apscheduler.triggers.interval import IntervalTrigger

    from app import scheduler
    scheduler.stop_scheduler()
    monkeypatch.setattr(config, "SYNC_DIR", str(tmp_path))
    try:
        job = scheduler.start_scheduler().get_job("forget_maintenance")
        trigger, next_run = job.trigger, job.next_run_time
    finally:
        scheduler.stop_scheduler()
    assert isinstance(trigger, IntervalTrigger)
    assert trigger.interval <= timedelta(hours=1)
    assert next_run - datetime.now(timezone.utc) <= timedelta(minutes=5)


def _conversation_rows(cid: str) -> list[dict[str, Any]]:
    return [
        {"id": "turn-1_chunk_0", "document": "q/a", "embedding": [0.1],
         "metadata": {"artifact_id": "turn-1", "conversation_id": cid, "filename": f"chat_{cid}_20261007"}},
        {"id": "mem-1_chunk_0", "document": "fact", "embedding": [0.2],
         "metadata": {"artifact_id": "mem-1", "conversation_id": cid, "filename": f"memory_fact_{cid}_0"}},
        {"id": "turn-2_chunk_0", "document": "q/a", "embedding": [0.3],
         "metadata": {"artifact_id": "turn-2", "conversation_id": "live", "filename": "chat_live_20261007"}},
    ]


def _seed_conversation_export(sync_dir: Path, cid: str) -> None:
    from app.sync._helpers import ARTIFACTS_JSONL, CHROMA_SUBDIR, NEO4J_SUBDIR
    _write_jsonl(sync_dir / CHROMA_SUBDIR / f"{config.collection_name('conversations')}.jsonl", _conversation_rows(cid))
    _write_jsonl(sync_dir / NEO4J_SUBDIR / ARTIFACTS_JSONL, [
        {"id": aid, "filename": "f", "domain": "conversations", "ingested_at": "2026-10-07T09:00:00+00:00"}
        for aid in ("turn-1", "mem-1", "turn-2")
    ])


def test_chroma_import_skips_the_transcripts_of_a_trashed_conversation(tmp_path: Path, monkeypatch, reg):
    from app.sync import import_
    from tests.test_sync_chroma_roundtrip import _FakeChromaServer
    _forgotten(reg, "conversation", "binned", state="trashed")
    monkeypatch.setattr(import_.config, "DOMAINS", ["conversations"], raising=False)
    _seed_conversation_export(tmp_path, "binned")
    server = _FakeChromaServer(seed_chunks=0)
    monkeypatch.setattr(import_, "httpx", server)

    import_.import_chroma(chroma_url="http://chroma.test:8000", sync_dir=str(tmp_path))

    assert set(server.written) == {"mem-1_chunk_0", "turn-2_chunk_0"}


def test_neo4j_import_skips_the_transcript_artifacts_of_a_trashed_conversation(tmp_path: Path, mock_neo4j, reg):
    from app.sync import import_
    _forgotten(reg, "conversation", "binned", state="trashed")
    _seed_conversation_export(tmp_path, "binned")
    _driver, session = mock_neo4j
    session.run.return_value.single.return_value = None

    result = import_.import_neo4j(_driver, sync_dir=str(tmp_path))

    touched = {c.kwargs.get("id") for c in session.run.call_args_list}
    assert "turn-1" not in touched
    assert {"mem-1", "turn-2"} <= touched
    assert result["artifacts_skipped_forgotten"] == 1


def test_a_restored_conversations_transcripts_are_imported_again(tmp_path: Path, monkeypatch, reg):
    from app.sync import import_
    from tests.test_sync_chroma_roundtrip import _FakeChromaServer
    reg.append([
        Entry("fg_1", Subject("conversation", "back"), "trashed", "2026-10-07T10:00:00Z", "m2", "ui"),
        Entry("fg_1", Subject("conversation", "back"), "restored", "2026-10-07T11:00:00Z", "m2", "ui"),
    ])
    monkeypatch.setattr(import_.config, "DOMAINS", ["conversations"], raising=False)
    _seed_conversation_export(tmp_path, "back")
    server = _FakeChromaServer(seed_chunks=0)
    monkeypatch.setattr(import_, "httpx", server)

    import_.import_chroma(chroma_url="http://chroma.test:8000", sync_dir=str(tmp_path))

    assert "turn-1_chunk_0" in server.written


# Artifact ids are content hashes: deleting content and adding it again brings
# back the same id. Whether the copy imports is decided by the registry alone (a
# re-add records ``readded`` against the forget it cancels), never by comparing
# timestamps from two machines. Each case's ingest time is chosen to mislead.
_DELETED_AT = "2026-10-07T10:00:00+00:00"


def _deleted(sync_dir: Path, reg: Registry, *, readded: bool, ingested_at: str) -> None:
    from app.sync._helpers import ARTIFACTS_JSONL, CHROMA_SUBDIR, NEO4J_SUBDIR
    entries = [Entry("fg_1", Subject("artifact", "same-hash"), "purged", _DELETED_AT, "m2", "content_lifecycle")]
    if readded:
        entries.append(Entry("fg_1", Subject("artifact", "same-hash"), "readded", "2026-10-07T08:00:00+00:00", "m3", "ingest"))
    reg.append(entries)
    _write_jsonl(sync_dir / NEO4J_SUBDIR / ARTIFACTS_JSONL, [
        {"id": "same-hash", "filename": "a.md", "domain": "general", "ingested_at": ingested_at},
    ])
    _write_jsonl(sync_dir / CHROMA_SUBDIR / f"{config.collection_name('general')}.jsonl", [
        {"id": "same-hash_chunk_0", "document": "x", "embedding": [0.1],
         "metadata": {"artifact_id": "same-hash", "ingested_at": ingested_at}},
    ])


_CASES = [
    # re-added, though its copy claims an ingest time before the delete
    (True, "2026-10-07T09:00:00+00:00", True),
    # never re-added, though a skewed clock stamped its copy after the delete
    (False, "2026-10-07T11:00:00+00:00", False),
]


@pytest.mark.parametrize("readded, ingested_at, imported", _CASES)
def test_neo4j_import_follows_the_registry_not_the_clocks(tmp_path, mock_neo4j, reg, readded, ingested_at, imported):
    from app.sync import import_
    _deleted(tmp_path, reg, readded=readded, ingested_at=ingested_at)
    driver, session = mock_neo4j
    session.run.return_value.single.return_value = None

    result = import_.import_neo4j(driver, sync_dir=str(tmp_path))

    assert result["artifacts_created"] == int(imported)
    assert result["artifacts_skipped_forgotten"] == int(not imported)


@pytest.mark.parametrize("readded, ingested_at, imported", _CASES)
def test_chroma_import_follows_the_registry_not_the_clocks(tmp_path, monkeypatch, reg, readded, ingested_at, imported):
    from app.sync import import_
    from tests.test_sync_chroma_roundtrip import _FakeChromaServer
    monkeypatch.setattr(import_.config, "DOMAINS", ["general"], raising=False)
    _deleted(tmp_path, reg, readded=readded, ingested_at=ingested_at)
    server = _FakeChromaServer(seed_chunks=0)
    monkeypatch.setattr(import_, "httpx", server)

    import_.import_chroma(chroma_url="http://chroma.test:8000", sync_dir=str(tmp_path))

    assert ("same-hash_chunk_0" in server.written) is imported
