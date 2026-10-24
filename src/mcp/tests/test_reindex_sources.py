# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""``POST /admin/kb/reindex`` finds a source for knowledge-pack artifacts and
uploads (F127).

Pack staging is deleted after install and an upload keeps no file under its own
name, so the archive-by-filename lookup skipped every one of them. Packs are now
re-staged through the real ``install_pack`` and uploads record their archived
copy as ``source_path``. Fakes stand only at the boundaries: the pack download,
``ingest_file`` (Chroma, BM25 and Neo4j writes), and the Neo4j reads.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import config
from core.knowledge.packs import serialise_registry
from tests.test_kb_admin import _reset_rate_limiter
from tests.test_services_knowledge_packs import _build_pack_archive
from tests.test_upload_quick_capture import _NOTE, _make_client, deps  # noqa: F401 — the fixture

PACK_ID = "starter-test"
PACK_VERSION = "1.0.0"
PACK_FILES = {
    "intro.md": b"# Hello\n\nA quick reference doc.\n",
    "subdir/deep.md": b"## Deep\n\nNested file.\n",
    # In the pack, but deleted from the knowledge base since install.
    "removed.md": b"# Removed\n\nThe operator deleted this one.\n",
}


@pytest.fixture()
def client():
    from app.main import app

    _reset_rate_limiter(app)
    with (
        patch("app.routers.kb_admin.get_neo4j", return_value=MagicMock()),
        patch("app.routers.kb_admin.get_chroma", return_value=MagicMock()),
        patch("app.routers.kb_admin.get_redis", return_value=MagicMock()),
        patch("app.routers.kb_admin._invalidate_scoped_safe"),
        # install_pack's post-install recompute enqueue (Redis).
        patch("app.db.redis.processor_queue.enqueue_job"),
    ):
        yield TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def archive(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "archive"
    root.mkdir()
    monkeypatch.setattr(config, "ARCHIVE_PATH", str(root))
    return root


def _publish_pack(tmp_path: Path, monkeypatch, *, domain: str = "general") -> list[str]:
    """Publish a pack in a registry of its own and serve its download; returns
    the list of URLs downloaded."""
    built = tmp_path / "built"
    built.mkdir()
    tarball, _, manifest = _build_pack_archive(
        built, pack_id=PACK_ID, version=PACK_VERSION, domain=domain, files=PACK_FILES,
    )
    registry = tmp_path / "registry.json"
    registry.write_text(serialise_registry([manifest]))
    monkeypatch.setenv("CERID_KNOWLEDGE_PACKS_REGISTRY", str(registry))

    downloads: list[str] = []

    async def _download(url: str, dest: Path) -> None:
        downloads.append(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(tarball.read_bytes())

    monkeypatch.setattr("app.services.knowledge_packs._default_download", _download)
    return downloads


@pytest.fixture()
def pack(tmp_path, monkeypatch):
    return _publish_pack(tmp_path, monkeypatch)


def _pack_artifact(
    aid: str, filename: str, *, version: str = PACK_VERSION, domain: str = "general",
) -> dict:
    return {
        "id": aid,
        "filename": filename,
        "domain": domain,
        "sub_category": "reference",
        "client_source": "knowledge-pack",
        "tags": json.dumps(["fixture", f"pack:{PACK_ID}", f"pack-version:{version}"]),
    }


def _reindex(client: TestClient, artifacts: list[dict], source_paths: dict[str, str] | None = None):
    ingest = AsyncMock(return_value={"status": "updated"})
    with (
        patch("app.routers.kb_admin.list_artifacts", return_value=artifacts),
        patch("app.routers.kb_admin.get_source_paths", return_value=source_paths or {}),
        patch("app.services.ingestion.ingest_file", ingest),
    ):
        res = client.post("/admin/kb/reindex", json={"limit": 25})
    assert res.status_code == 200, res.text
    return res.json(), ingest


class TestKnowledgePackArtifacts:
    def test_a_pack_is_restaged_once_and_only_its_kb_documents_reingested(self, client, archive, pack):
        artifacts = [_pack_artifact("p1", "intro.md"), _pack_artifact("p2", "deep.md")]

        with patch("app.db.redis.processor_queue.enqueue_job") as enqueue:
            data, ingest = _reindex(client, artifacts)

        assert data["reindexed"] == 2
        assert data["skipped"] == 0
        assert data["errors"] == 0
        assert len(pack) == 1  # one download for the pack, not one per artifact
        staged = archive.resolve() / ".knowledge-packs" / f"{PACK_ID}-{PACK_VERSION}" / "extracted"
        # The staging path is the one the pack was first installed from, so the
        # re-ingest matches each artifact by its recorded source_path.
        assert sorted(c.kwargs["file_path"] for c in ingest.call_args_list) == [
            str(staged / "content" / "intro.md"),
            str(staged / "content" / "subdir" / "deep.md"),
        ]
        for call in ingest.call_args_list:
            assert call.kwargs["force_reindex"] is True
            assert call.kwargs["on_forgotten"] == "skip"
            assert call.kwargs["client_source"] == "knowledge-pack"
            assert f"pack:{PACK_ID}" in json.loads(call.kwargs["tags"])
        # A document deleted since install never comes back.
        assert not any(c.kwargs["file_path"].endswith("removed.md") for c in ingest.call_args_list)
        # Staging is removed after the re-stage.
        assert not (archive / ".knowledge-packs" / f"{PACK_ID}-{PACK_VERSION}").exists()
        # Re-staging adds no artifacts, so the layout and trust jobs an install
        # enqueues are not queued once per page.
        enqueue.assert_not_called()

    def test_a_pack_in_an_operational_domain_is_matched_in_that_domain(
        self, client, archive, tmp_path, monkeypatch,
    ):
        # ingest_file keeps a domain the ontology knows even when it is not in
        # config.DOMAINS, so the artifact was recorded under "trading".
        assert "trading" not in config.DOMAINS
        _publish_pack(tmp_path, monkeypatch, domain="trading")

        data, ingest = _reindex(client, [_pack_artifact("p1", "intro.md", domain="trading")])

        assert data["reindexed"] == 1, data
        assert ingest.call_args.kwargs["domain"] == "trading"

    def test_pack_artifacts_that_cannot_be_restaged_are_skipped_by_reason(self, client, archive, pack):
        artifacts = [
            _pack_artifact("p1", "intro.md", version="0.9.0"),  # an older release
            {**_pack_artifact("p2", "deep.md"), "tags": "[]"},  # no pack tag
            _pack_artifact("p3", "not-in-pack.md"),
        ]

        data, ingest = _reindex(client, artifacts)

        assert data["reindexed"] == 0
        assert data["skipped_reasons"] == {
            "pack_version_changed": 1,
            "pack_tag_missing": 1,
            "pack_file_missing": 1,
        }
        ingest.assert_not_called()

    def test_a_pack_that_fails_to_restage_counts_errors(self, client, archive, pack, monkeypatch):
        async def _unreachable(url: str, dest: Path) -> None:
            raise OSError("registry host unreachable")

        monkeypatch.setattr("app.services.knowledge_packs._default_download", _unreachable)

        data, ingest = _reindex(client, [_pack_artifact("p1", "intro.md")])

        assert data["errors"] == 1
        ingest.assert_not_called()


class TestUploadArtifacts:
    def test_an_upload_is_reindexed_from_its_recorded_source_path(self, client, archive):
        # Archived under a suffixed name, so a lookup by filename cannot find it.
        copy = archive / "finance" / "statement_1.pdf"
        copy.parent.mkdir()
        copy.write_bytes(b"%PDF-1.4 statement")
        artifact = {"id": "u1", "filename": "statement.pdf", "domain": "finance",
                    "sub_category": "", "client_source": "upload", "tags": "[]"}

        data, ingest = _reindex(client, [artifact], source_paths={"u1": str(copy)})

        assert data["reindexed"] == 1
        ingest.assert_awaited_once()
        assert ingest.call_args.kwargs["file_path"] == str(copy)
        assert ingest.call_args.kwargs["force_reindex"] is True

    def test_an_upload_with_no_retained_copy_is_skipped_with_its_reason(self, client, archive):
        artifact = {"id": "u1", "filename": "note.md", "domain": "general",
                    "sub_category": "", "client_source": "upload", "tags": "[]"}

        data, ingest = _reindex(client, [artifact])

        assert data["skipped_reasons"] == {"upload_without_source_path": 1}
        assert "upload_without_source_path" in data["message"]
        ingest.assert_not_called()

    def test_an_upload_in_the_trash_stays_there(self, client, archive):
        copy = archive / "general" / "note.md"
        copy.parent.mkdir()
        copy.write_text("# Note\n")
        artifact = {"id": "u1", "filename": "note.md", "domain": "general",
                    "sub_category": "", "client_source": "upload", "tags": "[]"}
        forgotten = AsyncMock(return_value={"status": "skipped", "reason": "forgotten"})

        with (
            patch("app.routers.kb_admin.list_artifacts", return_value=[artifact]),
            patch("app.routers.kb_admin.get_source_paths", return_value={"u1": str(copy)}),
            patch("app.services.ingestion.ingest_file", forgotten),
        ):
            res = client.post("/admin/kb/reindex", json={"limit": 25})

        assert res.status_code == 200, res.text
        # "readd" would cancel the forget and bring the upload back.
        assert forgotten.call_args.kwargs["on_forgotten"] == "skip"
        assert res.json()["skipped_reasons"] == {"forgotten": 1}

    def test_a_recorded_path_outside_the_archive_is_not_read(self, client, archive, tmp_path):
        outside = tmp_path / "elsewhere.md"
        outside.write_text("not under the archive root")
        artifact = {"id": "f1", "filename": "elsewhere.md", "domain": "general",
                    "sub_category": "", "client_source": "", "tags": "[]"}

        data, ingest = _reindex(client, [artifact], source_paths={"f1": str(outside)})

        assert data["skipped_reasons"] == {"source_file_not_found": 1}
        ingest.assert_not_called()


class TestUploadRecordsItsArchivedCopy:
    def test_an_archived_upload_records_the_copy_as_source_path(self, deps, archive, monkeypatch):  # noqa: F811
        monkeypatch.setattr(config, "STORAGE_MODE", "archive")
        with (
            patch("app.db.neo4j.artifacts.set_artifact_properties") as set_props,
            patch("app.deps.get_neo4j", return_value="driver"),
        ):
            resp = _make_client().post("/upload", files={"file": _NOTE}, params={"domain": "general"})

        assert resp.status_code == 200, resp.text
        copy = archive / "general" / _NOTE[0]
        assert copy.read_bytes() == _NOTE[1]
        set_props.assert_called_once_with("driver", "art-quick-1", {"source_path": str(copy)})

    def test_an_upload_that_is_not_archived_records_nothing(self, deps, archive):  # noqa: F811
        with patch("app.db.neo4j.artifacts.set_artifact_properties") as set_props:
            resp = _make_client().post("/upload", files={"file": _NOTE}, params={"domain": "general"})

        assert resp.status_code == 200
        set_props.assert_not_called()
        assert not (archive / "general").exists()


def test_get_source_paths_reads_only_artifacts_that_record_one():
    from app.db.neo4j.artifacts import get_source_paths

    session = MagicMock()
    session.run.return_value = [{"id": "a1", "source_path": "/archive/finance/x.pdf"}]
    driver = MagicMock()
    driver.session.return_value.__enter__.return_value = session

    assert get_source_paths(driver, ["a1", "a2"]) == {"a1": "/archive/finance/x.pdf"}
    cypher = session.run.call_args.args[0]
    assert "coalesce(a.source_path, '') <> ''" in cypher
    assert session.run.call_args.kwargs["ids"] == ["a1", "a2"]
    assert get_source_paths(driver, []) == {}
