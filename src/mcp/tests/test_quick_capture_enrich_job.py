# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""QuickCaptureEnrichJob — the deferred half of ``POST /upload?quick=true``.

Classifies the persisted note, moves it to the suggested domain, writes
sub-category + tags, derives a display title, marks the artifact enriched
and emits the agent event the quick-capture FAB listens for. A second run
on the same artifact is a no-op (no second classifier call).
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import config
from app.processor.jobs.quick_capture_enrich import (
    QuickCaptureEnrichJob,
    enqueue_quick_capture_enrichment,
)
from core.processor.job import BaseJob
from core.processor.priority import Priority

_AID = "art-quick-1"
_NOTE = "Buy a new drill\nThe old one died today."


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def single(self):
        return self._row


class _FakeSession:
    def __init__(self, row):
        self._row = row

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, query, **params):
        return _FakeResult(dict(self._row) if self._row is not None else None)


class _FakeDriver:
    def __init__(self, row):
        self.row = row

    def session(self):
        return _FakeSession(self.row)


class _FakeCollection:
    def __init__(self, ids, docs, metas):
        self.ids, self.docs, self.metas = ids, docs, metas
        self.updates: list[dict] = []

    def get(self, ids=None, include=None, **kwargs):
        return {"ids": list(self.ids), "documents": list(self.docs), "metadatas": [dict(m) for m in self.metas]}

    def update(self, ids, metadatas):
        self.updates.append({"ids": ids, "metadatas": metadatas})
        self.metas = metadatas


class _FakeChroma:
    def __init__(self, collections):
        self.collections = collections

    def get_or_create_collection(self, name):
        return self.collections.setdefault(name, _FakeCollection([], [], []))


@pytest.fixture
def world(monkeypatch):
    """A persisted quick note in ``general`` with minimal metadata."""
    import app.db.neo4j.artifacts as artifacts_mod
    import app.db.neo4j.taxonomy as taxonomy_mod
    import app.deps as deps_mod
    import app.routers.artifacts as artifacts_router
    import utils.agent_events as events_mod
    import utils.metadata as metadata_mod

    monkeypatch.setattr(config, "DOMAINS", ["general", "finance", "projects"], raising=False)
    monkeypatch.setattr(config, "DEFAULT_DOMAIN", "general", raising=False)
    monkeypatch.setattr(config, "CATEGORIZE_MODE", "smart", raising=False)

    row = {
        "domain": "general",
        "filename": "note-2026-10-05T22-57-00.md",
        "chunk_ids": json.dumps(["c1"]),
        "enriched_at": None,
    }
    driver = _FakeDriver(row)
    source = _FakeCollection(
        ["c1"], [_NOTE],
        [{"artifact_id": _AID, "filename": row["filename"], "domain": "general", "metadata_mode": "minimal"}],
    )
    chroma = _FakeChroma({config.collection_name("general"): source})
    calls = SimpleNamespace(categorize=[], recategorize=[], taxonomy=[], props=[], events=[])

    async def fake_categorize(text, filename, mode=None):
        calls.categorize.append((text, filename, mode))
        return {
            "suggested_domain": "projects",
            "sub_category": "home",
            "tags": ["tools", "shopping"],
            "summary": "Needs a new drill.",
        }

    def fake_recategorize(artifact_id, new_domain, sub_category="", tags=""):
        calls.recategorize.append((artifact_id, new_domain, sub_category, tags))
        # Mirror the real helper: chunks now live in the destination collection.
        dest = chroma.get_or_create_collection(config.collection_name(new_domain))
        dest.ids, dest.docs, dest.metas = source.ids, source.docs, [
            {**m, "domain": new_domain} for m in source.metas
        ]
        source.ids, source.docs, source.metas = [], [], []
        row["domain"] = new_domain
        return {"status": "success", "old_domain": "general", "new_domain": new_domain}

    def fake_taxonomy(driver_, artifact_id, sub_category=None, tags_json=None):
        calls.taxonomy.append((artifact_id, sub_category, tags_json))
        return {}

    def fake_props(driver_, artifact_id, properties):
        calls.props.append((artifact_id, dict(properties)))
        if "quick_capture_enriched_at" in properties:
            row["enriched_at"] = properties["quick_capture_enriched_at"]
        if "filename" in properties:
            row["filename"] = properties["filename"]
        return len(properties)

    def fake_emit(agent, message, level="info", metadata=None):
        calls.events.append((agent, message, level, metadata or {}))

    monkeypatch.setattr(deps_mod, "get_neo4j", lambda: driver)
    monkeypatch.setattr(deps_mod, "get_chroma", lambda: chroma)
    monkeypatch.setattr(metadata_mod, "ai_categorize", fake_categorize)
    monkeypatch.setattr(artifacts_router, "recategorize", fake_recategorize)
    monkeypatch.setattr(taxonomy_mod, "update_artifact_taxonomy", fake_taxonomy)
    monkeypatch.setattr(artifacts_mod, "set_artifact_properties", fake_props)
    monkeypatch.setattr(events_mod, "emit_agent_event", fake_emit)
    return SimpleNamespace(row=row, chroma=chroma, source=source, calls=calls)


async def _progress(pct: float) -> None:
    return None


class TestContract:
    def test_registered_in_the_default_registry(self):
        from app.processor.worker import build_default_registry

        assert build_default_registry()["quick_capture_enrich"] is QuickCaptureEnrichJob
        assert issubclass(QuickCaptureEnrichJob, BaseJob)

    def test_user_is_waiting_so_priority_is_high(self):
        assert QuickCaptureEnrichJob(artifact_id=_AID).priority == Priority.HIGH

    def test_enqueue_is_keyed_on_the_artifact(self, monkeypatch):
        import app.db.redis.processor_queue as queue_mod

        seen = {}

        def fake_enqueue_if_absent(job, *, payload=None, dedupe_payload=None, redis_client=None):
            seen["job_type"] = job.job_type
            seen["payload"] = payload
            seen["dedupe"] = dedupe_payload
            return "job-1"

        monkeypatch.setattr(queue_mod, "enqueue_job_if_absent", fake_enqueue_if_absent)
        assert enqueue_quick_capture_enrichment(_AID, domain_locked=True, categorize_mode="pro") == "job-1"
        assert seen["job_type"] == "quick_capture_enrich"
        assert seen["payload"] == {
            "artifact_id": _AID, "tenant_id": "default", "domain_locked": True, "categorize_mode": "pro",
        }
        assert seen["dedupe"] == {"artifact_id": _AID}


class TestRun:
    @pytest.mark.asyncio
    async def test_moves_domain_writes_taxonomy_title_and_emits(self, world):
        job = QuickCaptureEnrichJob(artifact_id=_AID, categorize_mode="smart")
        result = await job.run(_progress)

        assert world.calls.categorize == [(_NOTE, "note-2026-10-05T22-57-00.md", "smart")]
        # Domain correction goes through the one helper that moves chunks.
        assert world.calls.recategorize == [(_AID, "projects", "home", json.dumps(["tools", "shopping"]))]
        assert world.calls.taxonomy == []  # recategorize already wrote the taxonomy
        # Display title derived from the note's first line, flagged as derived.
        props = {k: v for _, p in world.calls.props for k, v in p.items()}
        assert props["filename"] == "Buy a new drill"
        assert props["title_derived"] == "true"
        assert props["quick_capture_enriched_at"]
        # Chunk metadata in the destination collection carries the same fields.
        dest = world.chroma.collections[config.collection_name("projects")]
        assert dest.updates, "chunk metadata was not updated in the destination collection"
        meta = dest.updates[-1]["metadatas"][0]
        assert meta["filename"] == "Buy a new drill"
        assert meta["metadata_mode"] == "enriched"
        assert meta["sub_category"] == "home"
        # The FAB learns the final title from the agent event.
        assert len(world.calls.events) == 1
        agent, _msg, _level, meta_evt = world.calls.events[0]
        assert agent == "quick_capture"
        assert meta_evt == {
            "artifact_id": _AID, "title": "Buy a new drill", "domain": "projects",
            "sub_category": "home", "enriched": True,
        }
        assert result.metadata["domain"] == "projects"
        assert result.metadata["title"] == "Buy a new drill"

    @pytest.mark.asyncio
    async def test_rerun_is_a_no_op(self, world):
        job = QuickCaptureEnrichJob(artifact_id=_AID)
        await job.run(_progress)
        second = await job.run(_progress)
        assert len(world.calls.categorize) == 1
        assert len(world.calls.events) == 1
        assert second.metadata["skipped"] == "already_enriched"

    @pytest.mark.asyncio
    async def test_locked_domain_keeps_the_domain_but_still_enriches(self, world):
        job = QuickCaptureEnrichJob(artifact_id=_AID, domain_locked=True)
        result = await job.run(_progress)
        assert world.calls.recategorize == []
        assert world.calls.taxonomy == [(_AID, "home", json.dumps(["tools", "shopping"]))]
        assert world.source.updates[-1]["metadatas"][0]["sub_category"] == "home"
        assert result.metadata["domain"] == "general"

    @pytest.mark.asyncio
    async def test_classifier_failure_keeps_the_note_and_does_not_mark_enriched(self, world, monkeypatch):
        import utils.metadata as metadata_mod

        async def nothing(text, filename, mode=None):
            return {}

        monkeypatch.setattr(metadata_mod, "ai_categorize", nothing)
        result = await QuickCaptureEnrichJob(artifact_id=_AID).run(_progress)
        assert result.metadata["skipped"] == "classifier_unavailable"
        assert world.row["enriched_at"] is None
        assert world.calls.recategorize == []
        # The FAB still gets told so it does not wait on a title that will not come.
        assert world.calls.events[0][3]["enriched"] is False

    @pytest.mark.asyncio
    async def test_missing_artifact_is_skipped(self, world):
        world.row.clear()
        world.row.update({"domain": None, "filename": None, "chunk_ids": None, "enriched_at": None})
        import app.deps as deps_mod

        deps_mod.get_neo4j().row = None
        result = await QuickCaptureEnrichJob(artifact_id="missing").run(_progress)
        assert result.metadata["skipped"] == "artifact_not_found"
