# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for ``app.processor.jobs.knowledge_pack_install``.

The install service is monkeypatched — these tests exercise the job
wrapper contract (registry re-read at run time, metadata shape, failure
propagation), not the download/extract/ingest pipeline, which
``test_services_knowledge_packs.py`` already covers.
"""
from __future__ import annotations

import json

import pytest

from app.processor.jobs.knowledge_pack_install import KnowledgePackInstallJob
from core.knowledge.packs import InstalledPack, PackError
from core.processor.priority import Priority

VALID_PACK = {
    "id": "job-test",
    "name": "Job test pack",
    "version": "1.0.0",
    "description": "Fixture for job tests",
    "domain": "general",
    "sub_category": "reference",
    "tags": ["fixture"],
    "license": "CC0-1.0",
    "size_bytes": 1234,
    "artifact_count": 1,
    "download_url": "https://example.org/pack.tar.gz",
    "sha256": "a" * 64,
    "provenance": {"source": "test"},
}


@pytest.fixture
def registry_path(tmp_path, monkeypatch):
    p = tmp_path / "registry.json"
    p.write_text(json.dumps({"schema_version": 1, "packs": [VALID_PACK]}))
    monkeypatch.setattr(
        "app.services.knowledge_packs.default_registry_path", lambda: p,
    )
    return p


def test_job_registered_in_default_registry():
    from app.processor.worker import build_default_registry

    registry = build_default_registry()
    assert registry.get("knowledge_pack_install") is KnowledgePackInstallJob


def test_job_is_high_priority_and_free():
    job = KnowledgePackInstallJob(pack_id="job-test")
    assert job.priority is Priority.HIGH
    estimate = job.estimate_cost()
    assert estimate.estimated_tokens_in == 0
    assert float(estimate.estimated_usd) == 0.0


def test_record_payload_round_trips_for_worker_dispatch():
    """Worker re-instantiates as job_class(**record.payload)."""
    job = KnowledgePackInstallJob(pack_id="job-test")
    record = job.new_record(payload={"pack_id": "job-test"})
    clone = KnowledgePackInstallJob(**record.payload)
    assert clone._pack_id == "job-test"


@pytest.mark.asyncio
async def test_run_installs_pack_from_registry(registry_path, monkeypatch):
    captured: dict = {}

    async def fake_install(pack, *, keep_staging: bool = False):
        captured["pack_id"] = pack.id
        return InstalledPack(
            pack_id=pack.id, version=pack.version,
            installed_at="2026-07-12T00:00:00+00:00",
            domain=pack.domain, sha256=pack.sha256,
            artifact_ids=("a", "b"),
        )

    monkeypatch.setattr(
        "app.services.knowledge_packs.install_pack_default", fake_install,
    )

    async def _noop_progress(_pct: float) -> None:
        return None

    job = KnowledgePackInstallJob(pack_id="job-test")
    result = await job.run(progress_cb=_noop_progress)

    assert captured["pack_id"] == "job-test"
    assert result.metadata["pack_id"] == "job-test"
    assert result.metadata["artifact_count"] == 2


@pytest.mark.asyncio
async def test_run_raises_for_unknown_pack(registry_path):
    async def _noop_progress(_pct: float) -> None:
        return None

    job = KnowledgePackInstallJob(pack_id="not-in-registry")
    with pytest.raises(PackError):
        await job.run(progress_cb=_noop_progress)
