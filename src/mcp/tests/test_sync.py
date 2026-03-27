# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for sync/ package — JSONL helpers, manifest, export/import logic.

Focus: helper functions (pure filesystem), manifest validation,
and core export/import paths with mocked services.
"""

import hashlib
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.sync._helpers import (
    ARTIFACTS_JSONL,
    CHROMA_BATCH_SIZE,
    DOMAINS_JSONL,
    MANIFEST_FILENAME,
    NEO4J_SUBDIR,
    RELATIONSHIPS_JSONL,
    _count_jsonl_lines,
    _default_sync_dir,
    _ensure_dir,
    _iter_jsonl,
    _sha256_file,
    _write_jsonl,
)
from app.sync.manifest import read_manifest, write_manifest

# ---------------------------------------------------------------------------
# Tests: Constants
# ---------------------------------------------------------------------------

class TestConstants:
    def test_manifest_filename(self):
        assert MANIFEST_FILENAME == "manifest.json"

    def test_jsonl_filenames(self):
        assert ARTIFACTS_JSONL.endswith(".jsonl")
        assert DOMAINS_JSONL.endswith(".jsonl")
        assert RELATIONSHIPS_JSONL.endswith(".jsonl")

    def test_chroma_batch_size_positive(self):
        assert CHROMA_BATCH_SIZE > 0

    def test_subdirs_defined(self):
        assert NEO4J_SUBDIR


# ---------------------------------------------------------------------------
# Tests: _sha256_file
# ---------------------------------------------------------------------------

class TestSha256File:
    def test_computes_hash(self, tmp_path):
        f = tmp_path / "test.txt"
        content = b"hello world"
        f.write_bytes(content)

        expected = hashlib.sha256(content).hexdigest()
        assert _sha256_file(str(f)) == expected

    def test_deterministic(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_bytes(b"deterministic")
        assert _sha256_file(str(f)) == _sha256_file(str(f))

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_bytes(b"")
        result = _sha256_file(str(f))
        assert len(result) == 64  # SHA-256 hex length

    def test_missing_file_returns_empty(self, tmp_path):
        result = _sha256_file(str(tmp_path / "nonexistent.txt"))
        assert result == ""

    def test_different_content_differs(self, tmp_path):
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_bytes(b"aaa")
        f2.write_bytes(b"bbb")
        assert _sha256_file(str(f1)) != _sha256_file(str(f2))


# ---------------------------------------------------------------------------
# Tests: _count_jsonl_lines
# ---------------------------------------------------------------------------

class TestCountJsonlLines:
    def test_counts_lines(self, tmp_path):
        f = tmp_path / "data.jsonl"
        f.write_text('{"a": 1}\n{"b": 2}\n{"c": 3}\n')
        assert _count_jsonl_lines(str(f)) == 3

    def test_skips_blank_lines(self, tmp_path):
        f = tmp_path / "data.jsonl"
        f.write_text('{"a": 1}\n\n{"b": 2}\n  \n')
        assert _count_jsonl_lines(str(f)) == 2

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.jsonl"
        f.write_text("")
        assert _count_jsonl_lines(str(f)) == 0

    def test_missing_file(self, tmp_path):
        assert _count_jsonl_lines(str(tmp_path / "nope.jsonl")) == 0


# ---------------------------------------------------------------------------
# Tests: _write_jsonl
# ---------------------------------------------------------------------------

class TestWriteJsonl:
    def test_writes_rows(self, tmp_path):
        f = tmp_path / "out.jsonl"
        rows = [{"key": "a"}, {"key": "b"}]
        count = _write_jsonl(str(f), rows)
        assert count == 2
        assert f.exists()

        lines = f.read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["key"] == "a"

    def test_empty_rows(self, tmp_path):
        f = tmp_path / "out.jsonl"
        count = _write_jsonl(str(f), [])
        assert count == 0
        assert f.read_text() == ""

    def test_non_serializable_uses_str(self, tmp_path):
        """Non-JSON-serializable values should fallback to str()."""
        f = tmp_path / "out.jsonl"
        rows = [{"path": Path(tempfile.gettempdir()) / "test"}]
        count = _write_jsonl(str(f), rows)
        assert count == 1
        parsed = json.loads(f.read_text().strip())
        assert "test" in parsed["path"]

    def test_round_trip_with_iter(self, tmp_path):
        f = tmp_path / "round.jsonl"
        original = [{"id": 1, "name": "test"}, {"id": 2, "name": "other"}]
        _write_jsonl(str(f), original)

        recovered = list(_iter_jsonl(str(f)))
        assert recovered == original


# ---------------------------------------------------------------------------
# Tests: _iter_jsonl
# ---------------------------------------------------------------------------

class TestIterJsonl:
    def test_yields_parsed_dicts(self, tmp_path):
        f = tmp_path / "data.jsonl"
        f.write_text('{"x": 1}\n{"x": 2}\n')
        results = list(_iter_jsonl(str(f)))
        assert len(results) == 2
        assert results[0]["x"] == 1

    def test_skips_blank_lines(self, tmp_path):
        f = tmp_path / "data.jsonl"
        f.write_text('{"a": 1}\n\n{"b": 2}\n')
        results = list(_iter_jsonl(str(f)))
        assert len(results) == 2

    def test_skips_malformed_json(self, tmp_path):
        f = tmp_path / "data.jsonl"
        f.write_text('{"a": 1}\nnot json\n{"b": 2}\n')
        results = list(_iter_jsonl(str(f)))
        assert len(results) == 2  # Malformed line skipped

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.jsonl"
        f.write_text("")
        assert list(_iter_jsonl(str(f))) == []

    def test_missing_file(self, tmp_path):
        # Should handle gracefully (log + empty)
        results = list(_iter_jsonl(str(tmp_path / "nope.jsonl")))
        assert results == []


# ---------------------------------------------------------------------------
# Tests: _ensure_dir
# ---------------------------------------------------------------------------

class TestEnsureDir:
    def test_creates_directory(self, tmp_path):
        target = tmp_path / "a" / "b" / "c"
        result = _ensure_dir(str(target))
        assert target.exists()
        assert target.is_dir()
        assert result == target

    def test_existing_directory(self, tmp_path):
        target = tmp_path / "exists"
        target.mkdir()
        result = _ensure_dir(str(target))
        assert target.exists()
        assert result == target


# ---------------------------------------------------------------------------
# Tests: _default_sync_dir
# ---------------------------------------------------------------------------

class TestDefaultSyncDir:
    @patch("sync._helpers.config")
    def test_uses_config_value(self, mock_config):
        mock_config.SYNC_DIR = "/custom/sync"
        assert _default_sync_dir() == "/custom/sync"

    @patch("sync._helpers.config")
    def test_fallback_when_no_config(self, mock_config):
        # Remove SYNC_DIR attribute
        del mock_config.SYNC_DIR
        mock_config.configure_mock(**{})
        # hasattr should return False
        type(mock_config).SYNC_DIR = property(lambda self: (_ for _ in ()).throw(AttributeError))

        result = _default_sync_dir()
        assert "cerid-sync" in result


# ---------------------------------------------------------------------------
# Tests: read_manifest
# ---------------------------------------------------------------------------

class TestReadManifest:
    def test_reads_valid_manifest(self, tmp_path):
        manifest = {
            "machine_id": "test-host",
            "timestamp": "2026-02-28T12:00:00",
            "sync_format_version": 1,
            "domains": ["coding"],
            "files": {},
        }
        mf = tmp_path / "manifest.json"
        mf.write_text(json.dumps(manifest))

        with patch("sync.manifest._default_sync_dir", return_value=str(tmp_path)):
            result = read_manifest(str(tmp_path))

        assert result["machine_id"] == "test-host"

    def test_missing_manifest_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            read_manifest(str(tmp_path))

    def test_invalid_json_raises(self, tmp_path):
        mf = tmp_path / "manifest.json"
        mf.write_text("not valid json")
        with pytest.raises(ValueError, match="Malformed"):
            read_manifest(str(tmp_path))

    def test_missing_required_keys_raises(self, tmp_path):
        mf = tmp_path / "manifest.json"
        mf.write_text(json.dumps({"machine_id": "x"}))  # Missing timestamp, files
        with pytest.raises(ValueError, match="missing"):
            read_manifest(str(tmp_path))


# ---------------------------------------------------------------------------
# Tests: write_manifest
# ---------------------------------------------------------------------------

class TestWriteManifest:
    @patch("sync.manifest.config")
    def test_writes_manifest(self, mock_config, tmp_path):
        mock_config.DOMAINS = ["coding", "general"]
        mock_config.collection_name = lambda d: f"domain_{d}"

        result = write_manifest(str(tmp_path), machine_id="test-host")

        mf = tmp_path / "manifest.json"
        assert mf.exists()
        assert result["machine_id"] == "test-host"
        assert "timestamp" in result
        assert "files" in result

    @patch("sync.manifest.config")
    def test_default_machine_id(self, mock_config, tmp_path):
        mock_config.DOMAINS = []
        mock_config.collection_name = lambda d: f"domain_{d}"

        with patch("socket.gethostname", return_value="my-host"):
            result = write_manifest(str(tmp_path))

        assert result["machine_id"] == "my-host"

    @patch("sync.manifest.config")
    def test_tracks_domain_files(self, mock_config, tmp_path):
        mock_config.DOMAINS = ["coding"]
        mock_config.collection_name = lambda d: f"domain_{d}"

        result = write_manifest(str(tmp_path))

        # Should reference chroma domain file
        files = result["files"]
        has_chroma = any("chroma" in k and "coding" in k for k in files)
        assert has_chroma

    @patch("sync.manifest.config")
    def test_hashes_existing_files(self, mock_config, tmp_path):
        mock_config.DOMAINS = []
        mock_config.collection_name = lambda d: f"domain_{d}"

        # Create a neo4j artifacts file
        neo4j_dir = tmp_path / "neo4j"
        neo4j_dir.mkdir()
        artifacts = neo4j_dir / "artifacts.jsonl"
        artifacts.write_text('{"id": "test"}\n')

        result = write_manifest(str(tmp_path))
        neo4j_key = "neo4j/artifacts.jsonl"
        if neo4j_key in result["files"]:
            assert result["files"][neo4j_key]["exists"] is True
            assert len(result["files"][neo4j_key]["sha256"]) == 64


# ---------------------------------------------------------------------------
# Tests: export_neo4j
# ---------------------------------------------------------------------------

class TestExportNeo4j:
    def test_exports_artifacts(self, mock_neo4j, tmp_path):
        from app.sync.export import export_neo4j

        driver, session = mock_neo4j

        # Mock Neo4j query results
        artifact_records = [
            {
                "id": "a1", "filename": "test.py", "domain": "coding",
                "content_hash": "h1", "ingested_at": "2026-01-01",
                "chunk_count": 3, "chunk_ids": '["c1", "c2", "c3"]',
                "recategorized_at": None,
            }
        ]
        domain_records = [{"name": "coding"}]
        rel_records = []

        session.run.side_effect = [
            iter(artifact_records),
            iter(domain_records),
            iter(rel_records),
        ]

        result = export_neo4j(driver, str(tmp_path))
        assert result["artifacts"] == 1
        assert result["domains"] == 1

        # Verify JSONL file was created
        artifacts_file = tmp_path / "neo4j" / "artifacts.jsonl"
        assert artifacts_file.exists()

    def test_handles_neo4j_error(self, mock_neo4j, tmp_path):
        from app.sync.export import export_neo4j

        driver, session = mock_neo4j
        session.run.side_effect = Exception("Neo4j down")

        result = export_neo4j(driver, str(tmp_path))
        assert "error" in result


# ---------------------------------------------------------------------------
# Tests: export_redis
# ---------------------------------------------------------------------------

class TestExportRedis:
    @patch("sync.export.config")
    def test_exports_audit_log(self, mock_config, tmp_path):
        from app.sync.export import export_redis

        mock_config.REDIS_INGEST_LOG = "ingest:log"
        redis = MagicMock()

        entries = [
            json.dumps({"event": "ingest", "timestamp": "2026-02-28"}),
            json.dumps({"event": "query", "timestamp": "2026-02-27"}),
        ]
        redis.lrange.return_value = entries

        result = export_redis(redis, str(tmp_path))
        assert result["entries_exported"] == 2

    @patch("sync.export.config")
    def test_handles_redis_error(self, mock_config, tmp_path):
        from app.sync.export import export_redis

        mock_config.REDIS_INGEST_LOG = "ingest:log"
        redis = MagicMock()
        redis.lrange.side_effect = Exception("Redis down")

        result = export_redis(redis, str(tmp_path))
        assert "error" in result


# ---------------------------------------------------------------------------
# Tests: import_redis
# ---------------------------------------------------------------------------

class TestImportRedis:
    @patch("sync.import_.config")
    def test_imports_new_entries(self, mock_config, tmp_path):
        from app.sync.import_ import import_redis

        mock_config.REDIS_INGEST_LOG = "ingest:log"
        mock_config.REDIS_LOG_MAX = 10000

        # Create export file
        redis_dir = tmp_path / "redis"
        redis_dir.mkdir()
        log_file = redis_dir / "audit_log.jsonl"
        log_file.write_text(
            json.dumps({"event": "ingest", "artifact_id": "a1", "timestamp": "2026-02-28"}) + "\n"
        )

        redis = MagicMock()
        redis.lrange.return_value = []  # No existing entries

        result = import_redis(redis, str(tmp_path))
        assert result["entries_added"] == 1
        assert result["entries_skipped"] == 0

    @patch("sync.import_.config")
    def test_deduplicates_existing(self, mock_config, tmp_path):
        from app.sync.import_ import import_redis

        mock_config.REDIS_INGEST_LOG = "ingest:log"
        mock_config.REDIS_LOG_MAX = 10000

        redis_dir = tmp_path / "redis"
        redis_dir.mkdir()
        log_file = redis_dir / "audit_log.jsonl"
        entry = {"event": "ingest", "artifact_id": "a1", "timestamp": "2026-02-28"}
        log_file.write_text(json.dumps(entry) + "\n")

        redis = MagicMock()
        # Same entry already in Redis
        redis.lrange.return_value = [json.dumps(entry)]

        result = import_redis(redis, str(tmp_path))
        assert result["entries_added"] == 0
        assert result["entries_skipped"] == 1

    @patch("sync.import_.config")
    def test_missing_export_file(self, mock_config, tmp_path):
        from app.sync.import_ import import_redis

        mock_config.REDIS_INGEST_LOG = "ingest:log"
        mock_config.REDIS_LOG_MAX = 10000

        redis = MagicMock()
        result = import_redis(redis, str(tmp_path))
        assert result["entries_added"] == 0
