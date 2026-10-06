# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""``POST /upload?quick=true`` acknowledges on persist and enriches later.

A two-line quick-capture note took 45 s on the Studio because the router
awaited ``ai_categorize`` inline while a wiki-refresh sweep held the chat
slot. In quick mode the router persists with a provisional domain, hands
categorisation + title enrichment to ``QuickCaptureEnrichJob``, and returns.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import config
from app.routers.upload import router as upload_router

_NOTE = ("note-2026-10-05T22-57-00.md", b"Buy a new drill\nThe old one died today.", "text/markdown")


def _make_client() -> TestClient:
    app = FastAPI()
    app.include_router(upload_router)
    return TestClient(app)


@pytest.fixture
def deps(monkeypatch):
    """Swap the handler's lazy imports for recording fakes.

    ``ai_categorize`` is a real coroutine function (not an AsyncMock) so the
    assertion is on whether the router *awaited* it, which is the defect.
    """
    import app.parsers as parsers_mod
    import app.parsers.magic_bytes as magic_mod
    import app.processor.jobs.quick_capture_enrich as job_mod
    import app.services.ingestion as ingestion_mod
    import utils.metadata as metadata_mod

    monkeypatch.setattr(config, "SUPPORTED_EXTENSIONS", {".md", ".txt"}, raising=False)
    monkeypatch.setattr(config, "STORAGE_MODE", "local", raising=False)
    monkeypatch.setattr(config, "CATEGORIZE_MODE", "smart", raising=False)

    calls = SimpleNamespace(categorize=[], ingest=[], enqueue=[], minimal=0, full=0)

    async def fake_categorize(text, filename, mode=None):
        calls.categorize.append((text, filename, mode))
        return {"suggested_domain": "finance"}

    def fake_ingest(text, domain, metadata, **kwargs):
        calls.ingest.append((text, domain, dict(metadata), kwargs))
        return {"status": "success", "artifact_id": "art-quick-1", "chunks": 1, "domain": domain}

    def fake_enqueue(artifact_id, **kwargs):
        calls.enqueue.append((artifact_id, kwargs))
        return "job-1"

    def fake_minimal(text, filename, domain):
        calls.minimal += 1
        return {"filename": filename, "domain": domain, "metadata_mode": "minimal"}

    def fake_full(text, filename, domain):
        calls.full += 1
        return {"filename": filename, "domain": domain}

    monkeypatch.setattr(parsers_mod, "parse_file", lambda path: {"text": "Buy a new drill\nThe old one died today.", "file_type": "md"})
    monkeypatch.setattr(magic_mod, "validate_magic_bytes", lambda *a, **k: None)
    monkeypatch.setattr(metadata_mod, "ai_categorize", fake_categorize)
    monkeypatch.setattr(metadata_mod, "extract_metadata_minimal", fake_minimal)
    monkeypatch.setattr(metadata_mod, "extract_metadata", fake_full)
    monkeypatch.setattr(ingestion_mod, "ingest_content", fake_ingest)
    monkeypatch.setattr(job_mod, "enqueue_quick_capture_enrichment", fake_enqueue)
    return calls


class TestQuickMode:
    def test_persists_without_awaiting_the_categoriser(self, deps):
        resp = _make_client().post("/upload", files={"file": _NOTE}, params={"quick": "true"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["artifact_id"] == "art-quick-1"
        assert body["enrichment"] == "queued"
        # The LLM call never ran inside the request.
        assert deps.categorize == []
        # Provisional domain, minimal metadata, neutral quality — the job enriches.
        _text, domain, metadata, kwargs = deps.ingest[0]
        assert domain == config.DEFAULT_DOMAIN
        assert metadata["quick_capture"] == "true"
        assert kwargs.get("skip_quality") is True
        assert deps.minimal == 1 and deps.full == 0

    def test_enqueues_enrichment_keyed_on_the_artifact(self, deps):
        _make_client().post("/upload", files={"file": _NOTE}, params={"quick": "true"})
        assert len(deps.enqueue) == 1
        artifact_id, kwargs = deps.enqueue[0]
        assert artifact_id == "art-quick-1"
        assert kwargs == {"domain_locked": False, "categorize_mode": "smart"}

    def test_explicit_domain_locks_the_domain_for_the_job(self, deps):
        resp = _make_client().post(
            "/upload", files={"file": _NOTE}, params={"quick": "true", "domain": "finance"},
        )
        assert resp.status_code == 200
        assert deps.ingest[0][1] == "finance"
        assert deps.enqueue[0][1]["domain_locked"] is True

    def test_enqueue_failure_still_acknowledges_the_persist(self, deps, monkeypatch):
        import app.processor.jobs.quick_capture_enrich as job_mod

        def boom(artifact_id, **kwargs):
            raise RuntimeError("redis down")

        monkeypatch.setattr(job_mod, "enqueue_quick_capture_enrichment", boom)
        resp = _make_client().post("/upload", files={"file": _NOTE}, params={"quick": "true"})
        assert resp.status_code == 200
        assert resp.json()["enrichment"] == "unavailable"

    def test_a_duplicate_is_acknowledged_but_never_re_enriched(self, deps, monkeypatch):
        """Ingest is content-addressed: the same text returns the EXISTING
        artifact's id with status duplicate. Enrichment on it would retitle
        the note and could move it to another domain, so nothing is queued."""
        import app.services.ingestion as ingestion_mod

        def duplicate(text, domain, metadata, **kwargs):
            return {"status": "duplicate", "artifact_id": "art-existing", "domain": "coding",
                    "duplicate_of": "drill.md"}

        monkeypatch.setattr(ingestion_mod, "ingest_content", duplicate)
        resp = _make_client().post("/upload", files={"file": _NOTE}, params={"quick": "true"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["artifact_id"] == "art-existing"
        assert body["enrichment"] == "duplicate"
        assert deps.enqueue == []

    def test_default_mode_still_categorises_inline(self, deps):
        resp = _make_client().post("/upload", files={"file": _NOTE})
        assert resp.status_code == 200
        assert len(deps.categorize) == 1
        assert deps.enqueue == []
        assert "enrichment" not in resp.json()
