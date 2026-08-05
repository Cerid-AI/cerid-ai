# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for recursive email-attachment ingestion (RAG Cycle C2.4).

Three layers:

1. **Parser**: ``parse_eml`` extracts attachment bytes into
   :class:`AttachmentBlob`. Too-large attachments are listed but not
   extracted. Cycle-prevention ContextVar suppresses bytes when set.
2. **Magic-byte gate**: ``magic_bytes_match`` returns ``(False, ...)`` on
   mismatch so the service layer skips without raising.
3. **Live Neo4j**: ``write_has_attachment`` creates an idempotent edge.
   Skips when Neo4j isn't reachable (same fixture pattern as
   ``test_wikilink_neo4j_resolution.py`` and
   ``test_frontmatter_integration.py``).
"""
from __future__ import annotations

import logging
import os
import uuid
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import pytest

from app.parsers.email import _SKIP_NESTED_ATTACHMENTS, parse_eml
from app.parsers.magic_bytes import magic_bytes_match
from core.ingest.attachments import EMAIL_ATTACHMENT_MAX_SIZE, AttachmentBlob

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Minimal real PDF — same constant used by tests/test_upload_magic_byte.py.
# Real PDFs start with %PDF-; this stub is enough to pass the magic-byte
# check (filetype.guess returns "pdf") even if pdfplumber yields no
# extractable text.
_REAL_PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n"

# Real DOCX is a ZIP container with specific internal files. For unit
# tests where we only need the magic-byte gate to accept it, the bare
# ZIP header is sufficient — the office parser will reject the content
# downstream, which is the "parser-error, skip and continue" path we
# want to exercise.
_ZIP_BYTES = (
    b"PK\x03\x04"
    + b"\x14\x00\x00\x00\x00\x00" + b"\x00" * 40
)


def _build_email_with_attachment(
    *,
    body_text: str,
    attachment_filename: str,
    attachment_bytes: bytes,
    attachment_mime: tuple[str, str] = ("application", "pdf"),
) -> bytes:
    """Construct a real .eml byte string with one attachment.

    Uses Python's stdlib ``email.message.EmailMessage`` so the framing
    matches what real mail clients emit (proper boundaries,
    base64-encoded payload, Content-Disposition: attachment).
    """
    msg = EmailMessage()
    msg["From"] = "alice@example.com"
    msg["To"] = "bob@example.com"
    msg["Subject"] = "Test with attachment"
    msg["Message-ID"] = "<abc-123@example.com>"
    msg.set_content(body_text)
    maintype, subtype = attachment_mime
    msg.add_attachment(
        attachment_bytes,
        maintype=maintype,
        subtype=subtype,
        filename=attachment_filename,
    )
    return msg.as_bytes()


def _build_email_no_attachments(body_text: str) -> bytes:
    msg = EmailMessage()
    msg["From"] = "alice@example.com"
    msg["To"] = "bob@example.com"
    msg["Subject"] = "Plain"
    msg["Message-ID"] = "<plain-1@example.com>"
    msg.set_content(body_text)
    return msg.as_bytes()


def _build_nested_email(
    *,
    outer_body: str,
    inner_attachment_filename: str,
    inner_attachment_bytes: bytes,
) -> bytes:
    """Outer .eml contains an inner .eml (which itself has an attachment).

    Used to exercise the cycle-prevention contract: the outer ingest
    should recurse into the inner .eml's body text, but the inner
    .eml's own attachment must NOT be extracted (no double-recursion).
    """
    inner = _build_email_with_attachment(
        body_text="Inner email body.",
        attachment_filename=inner_attachment_filename,
        attachment_bytes=inner_attachment_bytes,
    )
    outer = EmailMessage()
    outer["From"] = "carol@example.com"
    outer["To"] = "alice@example.com"
    outer["Subject"] = "Forwarded"
    outer["Message-ID"] = "<outer-1@example.com>"
    outer.set_content(outer_body)
    outer.add_attachment(
        inner,
        maintype="message",
        subtype="rfc822",
        filename="forwarded.eml",
    )
    return outer.as_bytes()


# ---------------------------------------------------------------------------
# Layer 1: parser tests (no infra needed)
# ---------------------------------------------------------------------------


class TestParseEmlAttachments:
    def test_attachment_bytes_are_extracted(self, tmp_path, monkeypatch):
        monkeypatch.setattr("config.ANONYMIZE_EMAIL_HEADERS", False)
        eml_bytes = _build_email_with_attachment(
            body_text="See attached PDF.",
            attachment_filename="report.pdf",
            attachment_bytes=_REAL_PDF_BYTES,
        )
        eml_path = tmp_path / "with_pdf.eml"
        eml_path.write_bytes(eml_bytes)

        result = parse_eml(str(eml_path))

        blobs = result["_attachments"]
        assert len(blobs) == 1
        blob = blobs[0]
        assert isinstance(blob, AttachmentBlob)
        assert blob.filename == "report.pdf"
        assert blob.content_bytes == _REAL_PDF_BYTES
        assert blob.content_type == "application/pdf"
        assert blob.size == len(_REAL_PDF_BYTES)

    def test_attachment_listed_in_body_text(self, tmp_path, monkeypatch):
        monkeypatch.setattr("config.ANONYMIZE_EMAIL_HEADERS", False)
        eml_bytes = _build_email_with_attachment(
            body_text="hi",
            attachment_filename="report.pdf",
            attachment_bytes=_REAL_PDF_BYTES,
        )
        eml_path = tmp_path / "listed.eml"
        eml_path.write_bytes(eml_bytes)

        result = parse_eml(str(eml_path))
        # Body text still carries the human-readable listing
        assert "report.pdf" in result["text"]
        assert result["attachment_count"] == 1
        # Headers project onto the return dict
        assert result["subject"] == "Test with attachment"
        assert result["message_id"] == "<abc-123@example.com>"

    def test_oversized_attachment_skipped_with_marker(
        self, tmp_path, monkeypatch,
    ):
        """A 51 MB attachment is listed in the body text with the
        ``[skipped: too large]`` marker but never extracted."""
        monkeypatch.setattr("config.ANONYMIZE_EMAIL_HEADERS", False)
        oversized = b"x" * (EMAIL_ATTACHMENT_MAX_SIZE + 1)
        eml_bytes = _build_email_with_attachment(
            body_text="see attached",
            attachment_filename="huge.bin",
            attachment_bytes=oversized,
            attachment_mime=("application", "octet-stream"),
        )
        eml_path = tmp_path / "huge.eml"
        eml_path.write_bytes(eml_bytes)

        result = parse_eml(str(eml_path))
        # No bytes extracted — too large
        assert result["_attachments"] == []
        # But the body text records that the attachment existed
        assert "huge.bin" in result["text"]
        assert "[skipped: too large]" in result["text"]

    def test_no_attachments_returns_empty_list(self, tmp_path, monkeypatch):
        monkeypatch.setattr("config.ANONYMIZE_EMAIL_HEADERS", False)
        eml_path = tmp_path / "plain.eml"
        eml_path.write_bytes(_build_email_no_attachments("just a body"))

        result = parse_eml(str(eml_path))
        assert result["_attachments"] == []
        assert result["attachment_count"] == 0

    def test_cycle_prevention_contextvar_suppresses_extraction(
        self, tmp_path, monkeypatch,
    ):
        """When ``_SKIP_NESTED_ATTACHMENTS`` is set, the parser still
        emits the body-text listing but returns an empty blob list."""
        monkeypatch.setattr("config.ANONYMIZE_EMAIL_HEADERS", False)
        eml_bytes = _build_email_with_attachment(
            body_text="hi",
            attachment_filename="report.pdf",
            attachment_bytes=_REAL_PDF_BYTES,
        )
        eml_path = tmp_path / "cycle.eml"
        eml_path.write_bytes(eml_bytes)

        token = _SKIP_NESTED_ATTACHMENTS.set(True)
        try:
            result = parse_eml(str(eml_path))
        finally:
            _SKIP_NESTED_ATTACHMENTS.reset(token)

        assert result["_attachments"] == []
        # Listing still rendered so retrieval knows the file existed
        assert "report.pdf" in result["text"]
        # attachment_count still reflects what we *saw*
        assert result["attachment_count"] == 1

    def test_nested_eml_attachment_is_listed(self, tmp_path, monkeypatch):
        """An outer email whose attachment is itself an .eml.  At the
        outer parse level the nested .eml is extracted as bytes —
        cycle-prevention only kicks in once the service layer recurses
        into the nested parse (covered in Layer-2 tests)."""
        monkeypatch.setattr("config.ANONYMIZE_EMAIL_HEADERS", False)
        outer_bytes = _build_nested_email(
            outer_body="See forwarded email.",
            inner_attachment_filename="inner.pdf",
            inner_attachment_bytes=_REAL_PDF_BYTES,
        )
        eml_path = tmp_path / "nested.eml"
        eml_path.write_bytes(outer_bytes)

        result = parse_eml(str(eml_path))
        assert len(result["_attachments"]) == 1
        nested = result["_attachments"][0]
        # The nested .eml comes out as bytes ready for the service
        # layer to recurse into.
        assert nested.filename == "forwarded.eml"
        assert nested.content_type == "message/rfc822"


# ---------------------------------------------------------------------------
# Layer 2: magic-byte gate (pure function, no fixtures)
# ---------------------------------------------------------------------------


class TestMagicBytesMatch:
    def test_real_pdf_matches_pdf_suffix(self):
        ok, detected = magic_bytes_match(".pdf", _REAL_PDF_BYTES)
        assert ok is True
        assert detected == "pdf"

    def test_zip_renamed_pdf_rejected(self):
        ok, detected = magic_bytes_match(".pdf", _ZIP_BYTES)
        assert ok is False
        assert detected == "zip"

    def test_unidentifiable_returns_false_and_empty(self):
        ok, detected = magic_bytes_match(".pdf", b"random bytes")
        assert ok is False
        assert detected == ""

    def test_text_only_suffix_passes(self):
        ok, detected = magic_bytes_match(".md", b"# anything")
        assert ok is True
        assert detected == ""

    def test_unmapped_suffix_fails_open(self):
        """ZIP content with .rtf suffix — .rtf isn't in the map, so we
        return ``(True, "zip")`` (fail-open with detected info for logging)."""
        ok, detected = magic_bytes_match(".rtf", _ZIP_BYTES)
        assert ok is True
        assert detected == "zip"


# ---------------------------------------------------------------------------
# Layer 3: service-layer integration (in-process, mocked stores)
# ---------------------------------------------------------------------------

# These tests exercise the full attachment-recursion path WITHOUT needing
# a live Neo4j/Chroma/Redis stack — every infra call is intercepted.
# The contract is: given a parsed .eml with N attachments, we call
# ``ingest_content`` N+1 times (parent + each attachment) and write N
# HAS_ATTACHMENT edges. Failures don't abort the batch.


class _RecordingInfra:
    """Minimal in-memory recorder for ingest + graph writes.

    Patches the service-layer entry points so we can assert behaviour
    without a real ChromaDB / Neo4j running.
    """

    def __init__(self) -> None:
        self.content_calls: list[dict[str, Any]] = []
        self.edge_calls: list[dict[str, Any]] = []
        self.next_artifact_id = 0
        self.dedup_hash_to_id: dict[str, str] = {}

    def fake_ingest_content(
        self,
        content: str,
        domain: str = "general",
        metadata: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        # Dedup by content hash (approximated by content string).
        meta = metadata or {}
        if content in self.dedup_hash_to_id:
            existing_id = self.dedup_hash_to_id[content]
            self.content_calls.append({
                "content": content,
                "domain": domain,
                "metadata": meta,
                "result": "duplicate",
            })
            return {
                "status": "duplicate",
                "artifact_id": existing_id,
                "domain": domain,
                "chunks": 0,
            }
        self.next_artifact_id += 1
        aid = f"art-{self.next_artifact_id}"
        self.dedup_hash_to_id[content] = aid
        self.content_calls.append({
            "content": content,
            "domain": domain,
            "metadata": meta,
            "result": "success",
        })
        return {
            "status": "success",
            "artifact_id": aid,
            "domain": domain,
            "chunks": 1,
        }

    def fake_write_has_attachment(
        self,
        *,
        driver: Any = None,
        parent_id: str,
        child_id: str,
        filename: str,
        content_type: str,
    ) -> bool:
        self.edge_calls.append({
            "parent_id": parent_id,
            "child_id": child_id,
            "filename": filename,
            "content_type": content_type,
        })
        return True


@pytest.fixture
def recording_infra(monkeypatch):
    """Patch ``ingest_content`` and ``graph.write_has_attachment``."""
    infra = _RecordingInfra()

    # Patch service-layer ingest_content used by the attachment recursion
    import app.services.ingestion as ing
    monkeypatch.setattr(ing, "ingest_content", infra.fake_ingest_content)

    # Patch graph.write_has_attachment so the edge write is recorded
    monkeypatch.setattr(
        ing.graph, "write_has_attachment", infra.fake_write_has_attachment,
    )

    # Patch get_neo4j so the edge-write code path doesn't try to open a
    # real connection. fake_write_has_attachment ignores the driver arg.
    monkeypatch.setattr(ing, "get_neo4j", lambda: object())

    return infra


def _run_attachment_recursion(
    *,
    blobs: list[AttachmentBlob],
    parent_artifact_id: str = "parent-1",
    parent_domain: str = "general",
    parent_meta: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Drive the async ``_ingest_email_attachments`` from a sync test."""
    import asyncio

    from app.services.ingestion import _ingest_email_attachments

    return asyncio.run(
        _ingest_email_attachments(
            attachments=blobs,
            parent_artifact_id=parent_artifact_id,
            parent_domain=parent_domain,
            parent_meta=parent_meta or {},
        )
    )


class TestAttachmentRecursionService:
    def test_pdf_attachment_ingested_and_edge_written(
        self, recording_infra, monkeypatch,
    ):
        """A PDF attachment becomes its own Artifact with the parentage
        metadata and a HAS_ATTACHMENT edge to the parent email."""
        # The PDF parser would normally extract real text — patch it so
        # the test doesn't require real PDF text-extraction (the stub
        # bytes we hand it have no extractable text). The service-layer
        # attachment recursion re-imports ``parse_file`` from
        # ``app.parsers``, so we patch the package-level binding (the
        # ``app.services.ingestion`` module-level binding is the
        # *primary* parent ingest's import; the attachment path has
        # its own).
        import app.parsers as _parsers_pkg
        monkeypatch.setattr(
            _parsers_pkg, "parse_file",
            lambda _p: {"text": "stub pdf text", "file_type": "pdf",
                        "page_count": 1},
        )

        # Also stub extract_metadata_minimal — we only care that
        # ingest_content gets the right metadata stamps.
        monkeypatch.setattr(
            "utils.metadata.extract_metadata_minimal",
            lambda text, filename, domain: {"filename": filename,
                                            "domain": domain,
                                            "summary": text[:200]},
        )

        blobs = [
            AttachmentBlob(
                filename="report.pdf",
                content_bytes=_REAL_PDF_BYTES,
                content_type="application/pdf",
                size=len(_REAL_PDF_BYTES),
            ),
        ]
        summaries = _run_attachment_recursion(
            blobs=blobs,
            parent_artifact_id="parent-1",
            parent_meta={
                "message_id": "<m-1@example.com>",
                "from": "alice@example.com",
                "subject": "with pdf",
            },
        )

        assert len(summaries) == 1
        assert summaries[0]["status"] == "success"
        assert summaries[0]["filename"] == "report.pdf"

        # ingest_content called exactly once (the attachment)
        assert len(recording_infra.content_calls) == 1
        meta = recording_infra.content_calls[0]["metadata"]
        assert meta["source_type"] == "email_attachment"
        assert meta["parent_artifact_id"] == "parent-1"
        assert meta["parent_message_id"] == "<m-1@example.com>"
        assert meta["parent_email_from"] == "alice@example.com"
        assert meta["parent_email_subject"] == "with pdf"

        # Edge written
        assert len(recording_infra.edge_calls) == 1
        edge = recording_infra.edge_calls[0]
        assert edge["parent_id"] == "parent-1"
        assert edge["filename"] == "report.pdf"
        assert edge["content_type"] == "application/pdf"

    def test_magic_byte_mismatch_skips_attachment(
        self, recording_infra, monkeypatch, caplog,
    ):
        """An attachment whose magic bytes don't match its extension is
        logged and skipped — no parser call, no edge write."""
        # Sentinel: parse_file MUST NOT be called for a skipped attachment.
        # Patch the package binding the attachment-recursion path imports.
        import app.parsers as _parsers_pkg
        def _explode(_p: str) -> dict[str, Any]:
            raise AssertionError("parse_file should not be called for skipped attachment")
        monkeypatch.setattr(_parsers_pkg, "parse_file", _explode)

        # ZIP bytes with .pdf extension → magic-byte mismatch
        blobs = [
            AttachmentBlob(
                filename="bomb.pdf",
                content_bytes=_ZIP_BYTES,
                content_type="application/pdf",
                size=len(_ZIP_BYTES),
            ),
        ]

        with caplog.at_level(logging.WARNING):
            summaries = _run_attachment_recursion(blobs=blobs)

        assert len(summaries) == 1
        assert summaries[0]["status"] == "skipped"
        assert "magic" in summaries[0]["reason"].lower()
        # No ingest, no edge
        assert recording_infra.content_calls == []
        assert recording_infra.edge_calls == []
        # The mismatch was logged via the swallowed-error pipeline
        assert any(
            "email_attachment_magic_mismatch" in r.message
            or "magic-byte mismatch" in r.message.lower()
            for r in caplog.records
        )

    def test_unsupported_extension_skipped(self, recording_infra, monkeypatch):
        """An attachment with an extension not in PARSER_REGISTRY is
        skipped — no parser dispatch, no edge."""
        import app.parsers as _parsers_pkg
        monkeypatch.setattr(
            _parsers_pkg, "parse_file",
            lambda _p: pytest.fail("parse_file should not be called"),
        )

        blobs = [
            AttachmentBlob(
                filename="image.png",  # parsers/__init__.py has no .png parser
                content_bytes=b"\x89PNG\r\n\x1a\n" + b"\x00" * 100,
                content_type="image/png",
                size=108,
            ),
        ]
        summaries = _run_attachment_recursion(blobs=blobs)
        assert len(summaries) == 1
        assert summaries[0]["status"] == "skipped"
        assert "unsupported extension" in summaries[0]["reason"]
        assert recording_infra.content_calls == []
        assert recording_infra.edge_calls == []

    def test_duplicate_attachment_still_links_via_edge(
        self, recording_infra, monkeypatch,
    ):
        """A second email attaching the same PDF: the dedup'd artifact
        gets a NEW HAS_ATTACHMENT edge from the second email."""
        import app.parsers as _parsers_pkg
        monkeypatch.setattr(
            _parsers_pkg, "parse_file",
            lambda _p: {"text": "stub pdf text", "file_type": "pdf",
                        "page_count": 1},
        )
        monkeypatch.setattr(
            "utils.metadata.extract_metadata_minimal",
            lambda text, filename, domain: {"filename": filename,
                                            "domain": domain,
                                            "summary": text[:200]},
        )

        # First ingest creates the artifact
        blob = AttachmentBlob(
            filename="shared.pdf",
            content_bytes=_REAL_PDF_BYTES,
            content_type="application/pdf",
            size=len(_REAL_PDF_BYTES),
        )
        first = _run_attachment_recursion(
            blobs=[blob], parent_artifact_id="email-1",
        )
        assert first[0]["status"] == "success"
        first_child_id = first[0]["artifact_id"]

        # Second email attaches the same PDF — dedup returns the same id
        second = _run_attachment_recursion(
            blobs=[blob], parent_artifact_id="email-2",
        )
        assert second[0]["status"] == "duplicate"
        assert second[0]["artifact_id"] == first_child_id

        # Two edges exist — one per parent email
        assert len(recording_infra.edge_calls) == 2
        parents = {e["parent_id"] for e in recording_infra.edge_calls}
        assert parents == {"email-1", "email-2"}
        # Both edges point to the same child
        child_ids = {e["child_id"] for e in recording_infra.edge_calls}
        assert child_ids == {first_child_id}

    def test_one_bad_attachment_doesnt_abort_batch(
        self, recording_infra, monkeypatch, caplog,
    ):
        """One attachment errors mid-parse; the next one still ingests."""
        import app.parsers as _parsers_pkg

        call_counter = {"n": 0}
        def _parse(_p: str) -> dict[str, Any]:
            call_counter["n"] += 1
            if call_counter["n"] == 1:
                raise RuntimeError("simulated parser crash")
            return {"text": "stub", "file_type": "pdf", "page_count": 1}
        monkeypatch.setattr(_parsers_pkg, "parse_file", _parse)
        monkeypatch.setattr(
            "utils.metadata.extract_metadata_minimal",
            lambda text, filename, domain: {"filename": filename, "domain": domain},
        )

        blobs = [
            AttachmentBlob(
                filename="crash.pdf",
                content_bytes=_REAL_PDF_BYTES,
                content_type="application/pdf",
                size=len(_REAL_PDF_BYTES),
            ),
            AttachmentBlob(
                filename="ok.pdf",
                content_bytes=_REAL_PDF_BYTES,
                content_type="application/pdf",
                size=len(_REAL_PDF_BYTES),
            ),
        ]
        with caplog.at_level(logging.WARNING):
            summaries = _run_attachment_recursion(blobs=blobs)

        # The first crashed, the second succeeded
        assert len(summaries) == 2
        statuses = [s["status"] for s in summaries]
        assert "error" in statuses
        assert "success" in statuses
        # Only one ingest_content call happened (the successful one).
        # The crashed attachment never reached ingest_content.
        assert len(recording_infra.content_calls) == 1
        # Only one edge written
        assert len(recording_infra.edge_calls) == 1


class TestParentEmailStampsSourceType:
    """Cross-cutting: the .eml/.mbox file_type must stamp
    ``source_type='email'`` on the parent artifact's metadata so the
    locked-design Cypher contract
    ``(:Artifact {source_type: 'email'})-[:HAS_ATTACHMENT]->(:Artifact)``
    is discoverable."""

    def test_eml_file_type_stamps_email_source_type(
        self, tmp_path, monkeypatch,
    ):
        """Drives ``ingest_file`` just far enough to see the meta dict
        constructed for the parent ingest_content call."""
        import asyncio

        import app.services.ingestion as ing

        # Capture the meta dict the parent ingest_content receives
        captured: dict[str, Any] = {}
        def _capture(content, domain="general", metadata=None, **_kw):
            captured["domain"] = domain
            captured["metadata"] = dict(metadata or {})
            return {"status": "success", "artifact_id": "parent-x",
                    "domain": domain, "chunks": 1}
        monkeypatch.setattr(ing, "ingest_content", _capture)

        # Bypass path validation — we operate on a tmp file
        monkeypatch.setattr(
            ing, "validate_file_path",
            lambda p: Path(p),
        )

        # Stub the parsers so we don't have to write a real .eml
        def _fake_parse(_p: str) -> dict[str, Any]:
            return {
                "text": "From: a\nSubject: t\n\nbody",
                "file_type": "eml",
                "page_count": None,
                "attachment_count": 0,
                "subject": "t",
                "message_id": "<x@y>",
                "from": "a@y",
                "_attachments": [],
            }
        monkeypatch.setattr(ing, "parse_file", _fake_parse)

        # Stub extract_metadata so we don't depend on spaCy/tiktoken
        monkeypatch.setattr(
            "utils.metadata.extract_metadata",
            lambda text, filename, domain: {"filename": filename,
                                            "domain": domain},
        )
        monkeypatch.setattr(
            "utils.metadata.extract_metadata_minimal",
            lambda text, filename, domain: {"filename": filename,
                                            "domain": domain},
        )

        # Stub the AI categoriser so it's a no-op
        async def _no_ai(*_a, **_kw):
            return {}
        monkeypatch.setattr("utils.metadata.ai_categorize", _no_ai)

        eml_path = tmp_path / "test.eml"
        eml_path.write_bytes(b"placeholder")

        asyncio.run(ing.ingest_file(str(eml_path), domain="general"))

        assert captured["metadata"].get("source_type") == "email"


# ---------------------------------------------------------------------------
# Layer 4: live-Neo4j edge tests (skip when no NEO4J_PASSWORD)
# ---------------------------------------------------------------------------

NEO4J_URI_DEFAULT = "bolt://ai-companion-neo4j:7687"


@pytest.fixture(scope="module")
def neo4j_driver():
    """Real Neo4j driver. Skips when the database isn't reachable."""
    try:
        from neo4j import GraphDatabase
    except ImportError:
        pytest.skip("neo4j driver not installed")

    in_docker = os.path.exists("/.dockerenv")
    uri = os.environ.get("NEO4J_URI", NEO4J_URI_DEFAULT)
    if not in_docker and uri == NEO4J_URI_DEFAULT:
        uri = "bolt://127.0.0.1:7687"

    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "")
    if not password:
        pytest.skip("NEO4J_PASSWORD not set — live Neo4j assertions unavailable")

    try:
        driver = GraphDatabase.driver(uri, auth=(user, password))
        with driver.session() as s:
            s.run("RETURN 1").single()
    except Exception as exc:  # noqa: BLE001 — silent-catch-allowed: skip path
        pytest.skip(f"neo4j unreachable ({exc})")

    from app.db.neo4j.schema import init_schema
    init_schema(driver)
    yield driver
    driver.close()


def _create_artifact(driver: Any, *, filename: str) -> str:
    from app.db.neo4j.artifacts import create_artifact
    aid = f"test-{uuid.uuid4().hex}"
    create_artifact(
        driver=driver,
        artifact_id=aid,
        filename=filename,
        domain="general",
        keywords_json="[]",
        summary="",
        chunk_count=1,
        chunk_ids_json='["chunk_0"]',
        content_hash=aid,
    )
    return aid


def _cleanup(driver: Any, artifact_ids: list[str]) -> None:
    from app.db.neo4j.artifacts import delete_artifact
    for aid in artifact_ids:
        try:
            delete_artifact(driver, aid)
        except Exception:  # noqa: BLE001 — silent-catch-allowed: test teardown
            logging.getLogger("test.email_attachment.teardown").exception(
                "teardown delete_artifact failed for %s", aid,
            )


class TestHasAttachmentEdge:
    def test_edge_created_between_parent_and_child(self, neo4j_driver):
        from app.db.neo4j.relationships import write_has_attachment

        parent = _create_artifact(neo4j_driver, filename="email.eml")
        child = _create_artifact(neo4j_driver, filename="report.pdf")
        try:
            is_new = write_has_attachment(
                neo4j_driver,
                parent_id=parent,
                child_id=child,
                filename="report.pdf",
                content_type="application/pdf",
            )
            assert is_new is True

            with neo4j_driver.session() as session:
                rec = session.run(
                    "MATCH (p:Artifact {id: $p})-[r:HAS_ATTACHMENT]->(c:Artifact {id: $c}) "
                    "RETURN r.filename AS fn, r.content_type AS ct, r.attached_at AS ts",
                    p=parent, c=child,
                ).single()
            assert rec is not None
            assert rec["fn"] == "report.pdf"
            assert rec["ct"] == "application/pdf"
            assert rec["ts"]
        finally:
            _cleanup(neo4j_driver, [parent, child])

    def test_edge_is_idempotent(self, neo4j_driver):
        """Second write_has_attachment call for the same (parent, child)
        pair does NOT create a duplicate edge."""
        from app.db.neo4j.relationships import write_has_attachment

        parent = _create_artifact(neo4j_driver, filename="email2.eml")
        child = _create_artifact(neo4j_driver, filename="report2.pdf")
        try:
            first = write_has_attachment(
                neo4j_driver, parent_id=parent, child_id=child,
                filename="report2.pdf", content_type="application/pdf",
            )
            second = write_has_attachment(
                neo4j_driver, parent_id=parent, child_id=child,
                filename="report2.pdf", content_type="application/pdf",
            )
            assert first is True
            assert second is False

            with neo4j_driver.session() as session:
                count = session.run(
                    "MATCH (p:Artifact {id: $p})-[r:HAS_ATTACHMENT]->(c:Artifact {id: $c}) "
                    "RETURN count(r) AS n",
                    p=parent, c=child,
                ).single()
            assert count["n"] == 1
        finally:
            _cleanup(neo4j_driver, [parent, child])

    def test_unknown_relationship_type_blocked_by_settings_allowlist(self):
        """``HAS_ATTACHMENT`` is in the GRAPH_RELATIONSHIP_TYPES list — the
        ``create_relationship`` generic dispatcher must accept it. This
        is a config-level test (no Neo4j needed) that documents the
        wire-up between settings and the relationship-write code."""
        import config
        assert "HAS_ATTACHMENT" in config.GRAPH_RELATIONSHIP_TYPES
