# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Targeted 30-case contract suite for the recent upload + ingest changes.

Covers two surfaces that landed/changed most recently:

* The GUI file-upload path — ``app/routers/upload.py`` (validation, the
  skip_metadata/skip_quality wizard fast-paths, magic-byte propagation,
  error-code mapping, the supported-extensions + archive-listing helpers,
  and the typed ``models/upload.py`` responses).
* The content-addressed / idempotent ingest contracts that back the
  no-duplicate-chunk behaviour — ``_content_hash`` and ``_idempotency_key``
  in ``app/services/ingestion.py``.

The router tests mock the service layer (``ingest_content``), the parser,
the magic-byte validator, the metadata extractors, and the cache
invalidator — all of which the endpoint imports lazily inside the handler,
so they are patched at their canonical source modules.
"""
from __future__ import annotations

import asyncio
import io
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.testclient import TestClient

import config
from app.routers import upload as upload_module
from app.routers.upload import router as upload_router
from app.routers.upload import upload_file_endpoint

# Stable text payload used by the happy-path multipart uploads.
_FILE = ("doc.txt", b"hello world payload", "text/plain")


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(upload_router)
    return app


client = TestClient(_make_app())


@pytest.fixture
def mocks(monkeypatch):
    """Patch every heavy dependency the upload handler reaches lazily.

    Yields a namespace of the mocks so individual tests can assert on call
    args or override behaviour (raise, return alternate values).
    """
    monkeypatch.setattr(config, "SUPPORTED_EXTENSIONS", {".txt", ".pdf", ".md"}, raising=False)
    monkeypatch.setattr(config, "STORAGE_MODE", "local", raising=False)
    with ExitStack() as stack:
        m_parse = stack.enter_context(patch("app.parsers.parse_file"))
        m_ingest = stack.enter_context(patch("app.services.ingestion.ingest_content"))
        m_magic = stack.enter_context(patch("app.parsers.magic_bytes.validate_magic_bytes"))
        m_meta = stack.enter_context(patch("utils.metadata.extract_metadata"))
        m_meta_min = stack.enter_context(patch("utils.metadata.extract_metadata_minimal"))
        m_inval = stack.enter_context(
            patch("utils.query_cache.invalidate_cache_non_blocking", new_callable=AsyncMock)
        )

        m_parse.return_value = {"text": "extracted body text", "file_type": "txt"}
        m_ingest.return_value = {"status": "ok", "artifact_id": "a1", "chunks": 3, "domain": "general"}
        # Fresh dict per call — the handler mutates the returned metadata dict.
        m_meta.side_effect = lambda *a, **k: {"source_extractor": "full"}
        m_meta_min.side_effect = lambda *a, **k: {"source_extractor": "minimal"}

        yield SimpleNamespace(
            parse=m_parse,
            ingest=m_ingest,
            magic=m_magic,
            meta=m_meta,
            meta_min=m_meta_min,
            inval=m_inval,
        )


# ── Group A — POST /upload endpoint ───────────────────────────────────────────


class TestUploadEndpoint:
    def test_no_filename_returns_400(self, mocks):
        # An empty multipart filename is rejected by FastAPI as a missing file
        # (422) before the handler runs, so exercise the handler's own guard
        # directly with a filename-less UploadFile.
        uf = UploadFile(file=io.BytesIO(b"data"), filename="")
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(upload_file_endpoint(file=uf))
        assert exc_info.value.status_code == 400
        assert "filename" in exc_info.value.detail.lower()

    def test_unsupported_extension_returns_400(self, mocks):
        resp = client.post("/upload", files={"file": ("x.xyz", b"data", "application/octet-stream")})
        assert resp.status_code == 400
        assert "unsupported file type" in resp.json()["detail"].lower()

    def test_empty_file_returns_400(self, mocks):
        resp = client.post("/upload", files={"file": ("x.txt", b"", "text/plain")})
        assert resp.status_code == 400
        assert "empty" in resp.json()["detail"].lower()

    def test_oversize_returns_413(self, mocks, monkeypatch):
        monkeypatch.setattr(upload_module, "MAX_UPLOAD_BYTES", 8, raising=False)
        resp = client.post("/upload", files={"file": ("x.txt", b"way too many bytes", "text/plain")})
        assert resp.status_code == 413
        assert "too large" in resp.json()["detail"].lower()

    def test_happy_path_returns_200_and_result_shape(self, mocks):
        resp = client.post("/upload", files={"file": _FILE})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["artifact_id"] == "a1"
        assert body["chunks"] == 3
        assert body["domain"] == "general"
        assert body["filename"] == "doc.txt"
        assert body["categorize_mode"] == "smart"
        assert mocks.ingest.call_count == 1

    def test_no_extractable_text_returns_422(self, mocks):
        mocks.parse.return_value = {"text": "   \n\t ", "file_type": "pdf"}
        resp = client.post("/upload", files={"file": ("scan.pdf", b"%PDF-1.4 data", "application/pdf")})
        assert resp.status_code == 422
        assert "no extractable text" in resp.json()["detail"].lower()
        mocks.ingest.assert_not_called()

    def test_magic_byte_rejection_propagates_422(self, mocks):
        mocks.magic.side_effect = HTTPException(status_code=422, detail="Content appears to be zip")
        resp = client.post("/upload", files={"file": ("bomb.pdf", b"PK\x03\x04 zip", "application/pdf")})
        assert resp.status_code == 422
        assert "zip" in resp.json()["detail"].lower()
        mocks.ingest.assert_not_called()

    def test_original_filename_echoed_in_result(self, mocks):
        # ingest_content may report a different/normalized name; the endpoint
        # must override the result with the user's original upload filename.
        mocks.ingest.return_value = {"status": "ok", "artifact_id": "a1", "chunks": 1,
                                     "domain": "general", "filename": "internal-name.txt"}
        resp = client.post("/upload", files={"file": ("MyReport.txt", b"content here", "text/plain")})
        assert resp.status_code == 200
        assert resp.json()["filename"] == "MyReport.txt"

    def test_skip_metadata_uses_minimal_extractor(self, mocks):
        resp = client.post("/upload", files={"file": _FILE}, params={"skip_metadata": "true"})
        assert resp.status_code == 200
        assert mocks.meta_min.called
        assert not mocks.meta.called
        assert resp.json()["metadata"]["source_extractor"] == "minimal"

    def test_default_uses_full_extractor(self, mocks):
        resp = client.post("/upload", files={"file": _FILE})
        assert resp.status_code == 200
        assert mocks.meta.called
        assert not mocks.meta_min.called
        assert resp.json()["metadata"]["source_extractor"] == "full"

    def test_skip_quality_threaded_to_ingest(self, mocks):
        resp = client.post("/upload", files={"file": _FILE}, params={"skip_quality": "true"})
        assert resp.status_code == 200
        assert mocks.ingest.call_args.kwargs.get("skip_quality") is True

    def test_categorize_mode_defaults_smart_and_honors_explicit(self, mocks):
        default = client.post("/upload", files={"file": _FILE})
        assert default.json()["categorize_mode"] == "smart"
        explicit = client.post("/upload", files={"file": _FILE}, params={"categorize_mode": "pro"})
        assert explicit.json()["categorize_mode"] == "pro"

    def test_metadata_client_source_and_subcategory(self, mocks):
        resp = client.post("/upload", files={"file": _FILE}, params={"sub_category": "invoices"})
        assert resp.status_code == 200
        meta = resp.json()["metadata"]
        assert meta["client_source"] == "upload"
        assert meta["sub_category"] == "invoices"

    def test_parsed_optional_fields_merged_and_none_filtered(self, mocks):
        mocks.parse.return_value = {
            "text": "real text", "file_type": "pdf",
            "page_count": 3, "table_count": None, "form_field_count": None,
        }
        resp = client.post("/upload", files={"file": ("r.pdf", b"%PDF-1.4 data", "application/pdf")})
        assert resp.status_code == 200
        meta = resp.json()["metadata"]
        assert meta["page_count"] == 3
        assert "table_count" not in meta  # None values are filtered (ChromaDB rejects None)
        assert "form_field_count" not in meta

    def test_value_error_maps_to_400(self, mocks):
        mocks.ingest.side_effect = ValueError("path outside archive")
        resp = client.post("/upload", files={"file": _FILE})
        assert resp.status_code == 400
        assert "path outside archive" in resp.json()["detail"]

    def test_file_not_found_maps_to_404(self, mocks):
        mocks.parse.side_effect = FileNotFoundError("temp gone")
        resp = client.post("/upload", files={"file": _FILE})
        assert resp.status_code == 404
        assert "temp gone" in resp.json()["detail"]

    def test_generic_error_maps_to_500(self, mocks):
        mocks.ingest.side_effect = RuntimeError("unexpected boom")
        resp = client.post("/upload", files={"file": _FILE})
        assert resp.status_code == 500
        assert "unexpected boom" in resp.json()["detail"]


# ── Group B — supported-extensions + archive listing helpers ──────────────────


class TestUploadAuxEndpoints:
    def test_supported_extensions_endpoint(self, monkeypatch):
        monkeypatch.setattr(config, "SUPPORTED_EXTENSIONS", {".md", ".txt", ".pdf"}, raising=False)
        resp = client.get("/upload/supported")
        assert resp.status_code == 200
        body = resp.json()
        assert body["extensions"] == [".md", ".pdf", ".txt"]  # sorted
        assert body["count"] == 3

    def test_archive_files_empty_when_root_absent(self, monkeypatch, tmp_path):
        missing = tmp_path / "does-not-exist"
        monkeypatch.setattr(config, "ARCHIVE_PATH", str(missing), raising=False)
        monkeypatch.setattr(config, "STORAGE_MODE", "local", raising=False)
        resp = client.get("/archive/files")
        assert resp.status_code == 200
        body = resp.json()
        assert body["files"] == []
        assert body["total"] == 0
        assert body["storage_mode"] == "local"

    def test_archive_files_lists_grouped_by_domain(self, monkeypatch, tmp_path):
        (tmp_path / "finance").mkdir()
        (tmp_path / "finance" / "statement.pdf").write_bytes(b"%PDF-1.4 data")
        monkeypatch.setattr(config, "ARCHIVE_PATH", str(tmp_path), raising=False)
        resp = client.get("/archive/files")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        entry = body["files"][0]
        assert entry["filename"] == "statement.pdf"
        assert entry["domain"] == "finance"
        assert entry["size"] > 0


# ── Group C — typed response models (models/upload.py) ────────────────────────


class TestUploadResponseModels:
    def test_upload_response_defaults(self):
        from models.upload import UploadResponse

        r = UploadResponse()
        assert r.status == "ok"
        assert r.chunks == 0
        assert r.categorize_mode == "smart"
        assert r.metadata == {}

    def test_upload_response_allows_extra_fields(self):
        # extra="allow" → forward-compat for new ingest result keys (e.g.
        # "duplicate_of", "timestamp") without a model bump.
        from models.upload import UploadResponse

        # model_validate so the static type-checker doesn't flag the extra keys —
        # they are accepted at runtime via model_config extra="allow".
        r = UploadResponse.model_validate(
            {"artifact_id": "x", "duplicate_of": "old.txt", "timestamp": "2026-06-21T00:00:00Z"}
        )
        dumped = r.model_dump()
        assert dumped["duplicate_of"] == "old.txt"
        assert dumped["timestamp"] == "2026-06-21T00:00:00Z"

    def test_supported_and_archive_models(self):
        from models.upload import ArchiveFileItem, SupportedExtensionsResponse

        ext = SupportedExtensionsResponse(extensions=[".txt"], count=1)
        assert ext.count == 1
        item = ArchiveFileItem(filename="a.pdf", domain="finance", size=10, path="finance/a.pdf")
        assert item.domain == "finance"
        assert item.size == 10


# ── Group D — content-addressed / idempotent ingest contracts ─────────────────


class TestContentAddressedIngest:
    def test_content_hash_unicode_deterministic(self):
        from app.services.ingestion import _content_hash

        text = "café — naïve résumé — 日本語"
        assert _content_hash(text) == _content_hash(text)
        # 64 hex chars (sha256)
        assert len(_content_hash(text)) == 64

    def test_content_hash_empty_stable_and_distinct(self):
        from app.services.ingestion import _content_hash

        assert _content_hash("") == _content_hash("")
        assert _content_hash("") != _content_hash("x")


class TestIdempotencyKey:
    def test_idempotency_key_stable(self):
        from app.services.ingestion import _idempotency_key

        a = _idempotency_key("body", "imap://inbox/42", "default")
        b = _idempotency_key("body", "imap://inbox/42", "default")
        assert a == b
        assert len(a) == 64

    def test_idempotency_key_changes_with_content(self):
        from app.services.ingestion import _idempotency_key

        assert _idempotency_key("body", "uri", "t") != _idempotency_key("BODY", "uri", "t")

    def test_idempotency_key_changes_with_source_uri(self):
        from app.services.ingestion import _idempotency_key

        assert _idempotency_key("body", "uri-a", "t") != _idempotency_key("body", "uri-b", "t")

    def test_idempotency_key_changes_with_tenant(self):
        from app.services.ingestion import _idempotency_key

        assert _idempotency_key("body", "uri", "tenant-a") != _idempotency_key("body", "uri", "tenant-b")

    def test_idempotency_key_separator_prevents_collision(self):
        # NUL-joining the three fields means a field boundary can't be forged by
        # smuggling the separator into one field: ("a\x00b", "", "t") must not
        # collide with ("a", "b", "t").
        from app.services.ingestion import _idempotency_key

        assert _idempotency_key("a\x00b", "", "t") != _idempotency_key("a", "b", "t")
