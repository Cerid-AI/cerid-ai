# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for ``app.services.vault_write`` (RAG Cycle C3.3).

Covers the two-way vault writeback contract:

* Happy-path write + re-ingest, with ``source_type='cerid-synthesis'``
  reaching the ingestion metadata.
* Path safety: ``..`` escape, templates/ folder, attachments/ folder.
* Mode semantics: create rejects existing file, append preserves body,
  overwrite atomically replaces.
* Frontmatter stamping: ``source`` + ``cerid:created`` always present;
  ``cerid:reanalyze`` only when ``allow_synthesis_input=True``.
* Ingestion-failure path: file lands on disk, response says
  ``ingested=False`` with the error stringified.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.services.vault_write import (
    CERID_SYNTHESIS_SOURCE_TYPE,
    VaultWriteError,
    WriteNoteRequest,
    write_note,
)
from core.ingest.frontmatter import extract_frontmatter

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    """An on-disk vault root with the conventional sub-folders."""
    (tmp_path / "mocs").mkdir()
    (tmp_path / "daily").mkdir()
    (tmp_path / "templates").mkdir()
    (tmp_path / "attachments").mkdir()
    (tmp_path / "notes").mkdir()
    return tmp_path


@pytest.fixture
def fake_redis(vault_root: Path) -> MagicMock:
    """Mock Redis client returning a registered-vault record."""
    record = {
        "id": "v1",
        "path": str(vault_root),
        "label": "Test Vault",
        "is_vault": True,
        "vault_config": None,
        "domain_override": None,
    }
    redis = MagicMock()
    redis.get.return_value = json.dumps(record)
    return redis


@pytest.fixture
def fake_redis_non_vault(vault_root: Path) -> MagicMock:
    """Mock Redis client returning a registered folder that's NOT a vault."""
    record = {
        "id": "v1",
        "path": str(vault_root),
        "label": "Not A Vault",
        "is_vault": False,
        "vault_config": None,
    }
    redis = MagicMock()
    redis.get.return_value = json.dumps(record)
    return redis


@pytest.fixture
def fake_redis_unknown() -> MagicMock:
    """Mock Redis client that returns nothing — unknown vault_id."""
    redis = MagicMock()
    redis.get.return_value = None
    return redis


def _ingest_ok(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Stub ingest_content that pretends to create an Artifact."""
    return {
        "status": "success",
        "artifact_id": "artifact-xyz",
        "domain": "general",
        "chunks": 1,
    }


def _patch_ingest_ok():
    """Patch the lazy import inside vault_write._reingest."""
    from contextlib import ExitStack

    stack = ExitStack()
    fake_module = MagicMock()
    fake_module.ingest_content = MagicMock(side_effect=_ingest_ok)
    # Patch the module-level import the lazy import resolves to.
    stack.enter_context(
        patch.dict(
            "sys.modules",
            {"app.services.ingestion": fake_module},
        ),
    )
    # Patch set_artifact_properties + get_neo4j so the post-ingest stamp
    # doesn't try to talk to a real database.
    fake_artifacts = MagicMock()
    fake_artifacts.set_artifact_properties = MagicMock(return_value=2)
    stack.enter_context(
        patch.dict(
            "sys.modules",
            {"app.db.neo4j.artifacts": fake_artifacts},
        ),
    )
    fake_deps = MagicMock()
    fake_deps.get_neo4j = MagicMock(return_value=MagicMock())
    # Don't blow away the real app.deps module — patch just the get_neo4j
    # attribute it exports.
    stack.enter_context(patch("app.deps.get_neo4j", return_value=MagicMock()))
    return stack


def _patch_ingest_fails(exc: Exception):
    """Patch ingest_content to raise — exercises the file-on-disk path."""
    from contextlib import ExitStack

    stack = ExitStack()
    fake_module = MagicMock()
    fake_module.ingest_content = MagicMock(side_effect=exc)
    stack.enter_context(
        patch.dict("sys.modules", {"app.services.ingestion": fake_module}),
    )
    return stack


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestCreateMode:
    def test_creates_file_with_default_frontmatter(
        self, vault_root: Path, fake_redis: MagicMock,
    ):
        with _patch_ingest_ok():
            result = write_note(
                WriteNoteRequest(
                    vault_id="v1",
                    path="notes/synthesis.md",
                    content="# Synthesis\n\nFindings.\n",
                ),
                fake_redis,
            )

        file_path = Path(result.file_path)
        assert file_path.exists()
        assert file_path.parent == vault_root / "notes"
        assert file_path.name == "synthesis.md"

        # Frontmatter on disk includes the loop-breaker stamps.
        raw = file_path.read_text(encoding="utf-8")
        fm, body = extract_frontmatter(raw)
        assert fm.get("source") == CERID_SYNTHESIS_SOURCE_TYPE
        assert "cerid:created" in fm
        assert "# Synthesis" in body

        # Result echoes what landed in the file header.
        assert result.frontmatter_written["source"] == CERID_SYNTHESIS_SOURCE_TYPE
        assert "cerid:created" in result.frontmatter_written
        # No reanalyze flag by default.
        assert "cerid:reanalyze" not in result.frontmatter_written
        assert result.ingested is True
        assert result.artifact_id == "artifact-xyz"
        assert result.reingest_error is None
        assert result.mode == "create"

    def test_appends_md_extension_when_absent(
        self, vault_root: Path, fake_redis: MagicMock,
    ):
        with _patch_ingest_ok():
            result = write_note(
                WriteNoteRequest(
                    vault_id="v1", path="notes/no_ext", content="body",
                ),
                fake_redis,
            )
        assert result.file_path.endswith("no_ext.md")
        assert (vault_root / "notes" / "no_ext.md").exists()

    def test_create_rejects_existing_file(
        self, vault_root: Path, fake_redis: MagicMock,
    ):
        existing = vault_root / "notes" / "dup.md"
        existing.write_text("old content", encoding="utf-8")

        with _patch_ingest_ok(), pytest.raises(VaultWriteError, match="exists"):
            write_note(
                WriteNoteRequest(
                    vault_id="v1", path="notes/dup.md", content="new",
                ),
                fake_redis,
            )

    def test_creates_subdirectories(
        self, vault_root: Path, fake_redis: MagicMock,
    ):
        with _patch_ingest_ok():
            result = write_note(
                WriteNoteRequest(
                    vault_id="v1",
                    path="deep/nested/path/note.md",
                    content="x",
                ),
                fake_redis,
            )
        assert Path(result.file_path).exists()


class TestOverwriteMode:
    def test_overwrite_replaces_existing_file(
        self, vault_root: Path, fake_redis: MagicMock,
    ):
        target = vault_root / "notes" / "rewrite.md"
        target.write_text("old\n", encoding="utf-8")

        with _patch_ingest_ok():
            write_note(
                WriteNoteRequest(
                    vault_id="v1",
                    path="notes/rewrite.md",
                    content="new body",
                    mode="overwrite",
                ),
                fake_redis,
            )

        raw = target.read_text(encoding="utf-8")
        assert "old" not in raw
        assert "new body" in raw


class TestAppendMode:
    def test_append_preserves_existing_body(
        self, vault_root: Path, fake_redis: MagicMock,
    ):
        target = vault_root / "notes" / "ledger.md"
        target.write_text(
            "---\nsource: cerid-synthesis\n---\nfirst entry\n",
            encoding="utf-8",
        )

        with _patch_ingest_ok():
            write_note(
                WriteNoteRequest(
                    vault_id="v1",
                    path="notes/ledger.md",
                    content="second entry\n",
                    mode="append",
                ),
                fake_redis,
            )

        raw = target.read_text(encoding="utf-8")
        assert "first entry" in raw
        assert "second entry" in raw

    def test_append_on_nonexistent_file_creates_it(
        self, vault_root: Path, fake_redis: MagicMock,
    ):
        with _patch_ingest_ok():
            write_note(
                WriteNoteRequest(
                    vault_id="v1",
                    path="notes/new_append.md",
                    content="hello",
                    mode="append",
                ),
                fake_redis,
            )
        assert (vault_root / "notes" / "new_append.md").exists()


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------


class TestPathSafety:
    def test_dot_dot_escape_rejected(
        self, fake_redis: MagicMock,
    ):
        with _patch_ingest_ok(), pytest.raises(VaultWriteError, match="outside vault root"):
            write_note(
                WriteNoteRequest(
                    vault_id="v1", path="../escape.md", content="x",
                ),
                fake_redis,
            )

    def test_double_dot_deeper_escape_rejected(
        self, fake_redis: MagicMock,
    ):
        with _patch_ingest_ok(), pytest.raises(VaultWriteError, match="outside vault root"):
            write_note(
                WriteNoteRequest(
                    vault_id="v1",
                    path="notes/../../escape.md",
                    content="x",
                ),
                fake_redis,
            )

    def test_absolute_path_rejected(
        self, fake_redis: MagicMock,
    ):
        with _patch_ingest_ok(), pytest.raises(VaultWriteError, match="absolute"):
            write_note(
                WriteNoteRequest(
                    vault_id="v1", path="/etc/passwd", content="x",
                ),
                fake_redis,
            )

    def test_templates_folder_rejected(
        self, fake_redis: MagicMock,
    ):
        with _patch_ingest_ok(), pytest.raises(VaultWriteError, match="templates/skip"):
            write_note(
                WriteNoteRequest(
                    vault_id="v1",
                    path="templates/note.md",
                    content="x",
                ),
                fake_redis,
            )

    def test_attachments_folder_rejected(
        self, fake_redis: MagicMock,
    ):
        with _patch_ingest_ok(), pytest.raises(VaultWriteError, match="attachments"):
            write_note(
                WriteNoteRequest(
                    vault_id="v1",
                    path="attachments/foo.md",
                    content="x",
                ),
                fake_redis,
            )

    def test_empty_path_rejected(
        self, fake_redis: MagicMock,
    ):
        with _patch_ingest_ok(), pytest.raises(VaultWriteError, match="empty"):
            write_note(
                WriteNoteRequest(vault_id="v1", path="", content="x"),
                fake_redis,
            )


# ---------------------------------------------------------------------------
# Vault registry validation
# ---------------------------------------------------------------------------


class TestVaultRegistry:
    def test_unknown_vault_id_rejected(self, fake_redis_unknown: MagicMock):
        with pytest.raises(VaultWriteError, match="Unknown vault_id"):
            write_note(
                WriteNoteRequest(
                    vault_id="missing", path="notes/x.md", content="y",
                ),
                fake_redis_unknown,
            )

    def test_non_vault_folder_rejected(self, fake_redis_non_vault: MagicMock):
        with pytest.raises(VaultWriteError, match="not registered as a vault"):
            write_note(
                WriteNoteRequest(
                    vault_id="v1", path="notes/x.md", content="y",
                ),
                fake_redis_non_vault,
            )


# ---------------------------------------------------------------------------
# Frontmatter stamping + allowlist
# ---------------------------------------------------------------------------


class TestFrontmatterStamping:
    def test_caller_frontmatter_merged_in(
        self, vault_root: Path, fake_redis: MagicMock,
    ):
        with _patch_ingest_ok():
            result = write_note(
                WriteNoteRequest(
                    vault_id="v1",
                    path="notes/tagged.md",
                    content="body",
                    frontmatter={
                        "tags": ["cerid-synthesis", "weekly"],
                        "aliases": ["alt name"],
                        "cerid:topic": "rag-loop",
                    },
                ),
                fake_redis,
            )

        fm = result.frontmatter_written
        assert fm["tags"] == ["cerid-synthesis", "weekly"]
        assert fm["aliases"] == ["alt name"]
        assert fm["cerid:topic"] == "rag-loop"
        # Defaults still present.
        assert fm["source"] == CERID_SYNTHESIS_SOURCE_TYPE
        assert "cerid:created" in fm

    def test_non_allowlisted_keys_dropped(
        self, vault_root: Path, fake_redis: MagicMock,
    ):
        with _patch_ingest_ok():
            result = write_note(
                WriteNoteRequest(
                    vault_id="v1",
                    path="notes/filter.md",
                    content="body",
                    frontmatter={
                        "evil_key": "should not flow through",
                        "another_unknown": 42,
                        "tags": ["ok"],
                    },
                ),
                fake_redis,
            )

        fm = result.frontmatter_written
        assert "evil_key" not in fm
        assert "another_unknown" not in fm
        assert fm["tags"] == ["ok"]

    def test_allow_synthesis_input_stamps_reanalyze(
        self, vault_root: Path, fake_redis: MagicMock,
    ):
        with _patch_ingest_ok():
            result = write_note(
                WriteNoteRequest(
                    vault_id="v1",
                    path="notes/reanalyze.md",
                    content="body",
                    allow_synthesis_input=True,
                ),
                fake_redis,
            )

        assert result.frontmatter_written.get("cerid:reanalyze") is True

    def test_default_does_not_stamp_reanalyze(
        self, vault_root: Path, fake_redis: MagicMock,
    ):
        with _patch_ingest_ok():
            result = write_note(
                WriteNoteRequest(
                    vault_id="v1",
                    path="notes/default.md",
                    content="body",
                ),
                fake_redis,
            )

        assert "cerid:reanalyze" not in result.frontmatter_written


# ---------------------------------------------------------------------------
# Ingestion failure path
# ---------------------------------------------------------------------------


class TestIngestionFailure:
    def test_file_written_when_ingest_raises(
        self, vault_root: Path, fake_redis: MagicMock,
    ):
        with _patch_ingest_fails(RuntimeError("chroma down")):
            result = write_note(
                WriteNoteRequest(
                    vault_id="v1",
                    path="notes/orphan.md",
                    content="body that ingestion can't index",
                ),
                fake_redis,
            )

        # The file IS on disk.
        assert Path(result.file_path).exists()
        # But the response makes the failure visible.
        assert result.ingested is False
        assert result.artifact_id is None
        assert result.reingest_error and "chroma down" in result.reingest_error

    def test_file_written_when_ingest_returns_no_artifact_id(
        self, vault_root: Path, fake_redis: MagicMock,
    ):
        from contextlib import ExitStack

        with ExitStack() as stack:
            fake_module = MagicMock()
            fake_module.ingest_content = MagicMock(
                return_value={"status": "error", "error": "boom"},
            )
            stack.enter_context(
                patch.dict(
                    "sys.modules", {"app.services.ingestion": fake_module},
                ),
            )
            result = write_note(
                WriteNoteRequest(
                    vault_id="v1", path="notes/no_id.md", content="body",
                ),
                fake_redis,
            )

        assert result.ingested is False
        assert result.artifact_id is None
        assert result.reingest_error == "ingest_content returned no artifact_id"


# ---------------------------------------------------------------------------
# Ingestion metadata contract
# ---------------------------------------------------------------------------


class TestIngestionMetadata:
    def test_source_type_passed_to_ingest_content(
        self, vault_root: Path, fake_redis: MagicMock,
    ):
        captured: dict[str, Any] = {}

        def _capture(content: str, domain: str, metadata: dict, **kw):
            captured["content"] = content
            captured["domain"] = domain
            captured["metadata"] = metadata
            return {"status": "success", "artifact_id": "a1"}

        from contextlib import ExitStack

        with ExitStack() as stack:
            fake_module = MagicMock()
            fake_module.ingest_content = MagicMock(side_effect=_capture)
            stack.enter_context(
                patch.dict("sys.modules", {"app.services.ingestion": fake_module}),
            )
            stack.enter_context(patch("app.deps.get_neo4j", return_value=MagicMock()))
            fake_artifacts = MagicMock()
            stack.enter_context(
                patch.dict(
                    "sys.modules", {"app.db.neo4j.artifacts": fake_artifacts},
                ),
            )

            write_note(
                WriteNoteRequest(
                    vault_id="v1",
                    path="notes/passthrough.md",
                    content="body",
                ),
                fake_redis,
            )

        md = captured["metadata"]
        assert md["source_type"] == CERID_SYNTHESIS_SOURCE_TYPE
        assert md["client_source"] == "cerid_synthesis"
        assert md["cerid_reanalyze"] is False

    def test_reanalyze_flag_propagates_to_metadata(
        self, vault_root: Path, fake_redis: MagicMock,
    ):
        captured: dict[str, Any] = {}

        def _capture(content: str, domain: str, metadata: dict, **kw):
            captured["metadata"] = metadata
            return {"status": "success", "artifact_id": "a2"}

        from contextlib import ExitStack

        with ExitStack() as stack:
            fake_module = MagicMock()
            fake_module.ingest_content = MagicMock(side_effect=_capture)
            stack.enter_context(
                patch.dict("sys.modules", {"app.services.ingestion": fake_module}),
            )
            stack.enter_context(patch("app.deps.get_neo4j", return_value=MagicMock()))
            fake_artifacts = MagicMock()
            stack.enter_context(
                patch.dict(
                    "sys.modules", {"app.db.neo4j.artifacts": fake_artifacts},
                ),
            )

            write_note(
                WriteNoteRequest(
                    vault_id="v1",
                    path="notes/opt_in.md",
                    content="body",
                    allow_synthesis_input=True,
                ),
                fake_redis,
            )

        assert captured["metadata"]["cerid_reanalyze"] is True


# ---------------------------------------------------------------------------
# Mode validation
# ---------------------------------------------------------------------------


class TestModeValidation:
    def test_invalid_mode_rejected(self, fake_redis: MagicMock):
        # WriteNoteRequest takes a Literal type for static checking, but
        # at runtime nothing stops a hand-crafted value — the service
        # rejects it before any disk side-effects.
        req = WriteNoteRequest(
            vault_id="v1",
            path="notes/x.md",
            content="body",
            mode="upsert",  # type: ignore[arg-type]
        )
        with _patch_ingest_ok(), pytest.raises(VaultWriteError, match="unsupported mode"):
            write_note(req, fake_redis)
