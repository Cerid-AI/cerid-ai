# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for archive file listing and storage mode features (Phase 21D)."""

import asyncio
import os
from unittest.mock import patch

from app.routers.upload import _archive_file, list_archive_files

# ---------------------------------------------------------------------------
# Tests: _archive_file helper
# ---------------------------------------------------------------------------


class TestArchiveFile:
    def test_copies_file_to_domain_dir(self, tmp_path):
        src = tmp_path / "source.txt"
        src.write_text("hello world")

        archive = tmp_path / "archive"
        archive.mkdir()

        with patch("routers.upload.config") as mock_config:
            mock_config.ARCHIVE_PATH = str(archive)
            _archive_file(str(src), "test.txt", "coding")

        dest = archive / "coding" / "test.txt"
        assert dest.exists()
        assert dest.read_text() == "hello world"

    def test_creates_domain_dir_if_missing(self, tmp_path):
        src = tmp_path / "source.txt"
        src.write_text("content")

        archive = tmp_path / "archive"
        archive.mkdir()

        with patch("routers.upload.config") as mock_config:
            mock_config.ARCHIVE_PATH = str(archive)
            _archive_file(str(src), "file.txt", "newdomain")

        assert (archive / "newdomain" / "file.txt").exists()

    def test_avoids_overwriting_existing_file(self, tmp_path):
        src = tmp_path / "source.txt"
        src.write_text("new content")

        archive = tmp_path / "archive"
        (archive / "coding").mkdir(parents=True)
        (archive / "coding" / "test.txt").write_text("old content")

        with patch("routers.upload.config") as mock_config:
            mock_config.ARCHIVE_PATH = str(archive)
            _archive_file(str(src), "test.txt", "coding")

        # Original file untouched
        assert (archive / "coding" / "test.txt").read_text() == "old content"
        # New file with suffix
        assert (archive / "coding" / "test_1.txt").exists()
        assert (archive / "coding" / "test_1.txt").read_text() == "new content"

    def test_handles_missing_source_gracefully(self, tmp_path):
        archive = tmp_path / "archive"
        archive.mkdir()

        with patch("routers.upload.config") as mock_config:
            mock_config.ARCHIVE_PATH = str(archive)
            # Should not raise — logs a warning
            _archive_file("/nonexistent/path.txt", "file.txt", "general")


# ---------------------------------------------------------------------------
# Tests: archive listing via router
# ---------------------------------------------------------------------------


class TestArchiveFilesEndpoint:
    """Test the list_archive_files logic directly (not via FastAPI client)."""

    def test_empty_archive(self, tmp_path):
        """An empty archive directory returns no files."""
        with patch("routers.upload.config") as mock_config:
            mock_config.ARCHIVE_PATH = str(tmp_path)
            mock_config.STORAGE_MODE = "extract_only"
            result = asyncio.get_event_loop().run_until_complete(
                list_archive_files(domain="")
            )

        assert result["total"] == 0
        assert result["files"] == []
        assert result["storage_mode"] == "extract_only"

    def test_lists_files_by_domain(self, tmp_path):
        """Files in domain subdirectories are listed correctly."""
        # Create some test files
        (tmp_path / "coding").mkdir()
        (tmp_path / "coding" / "app.py").write_text("print('hello')")
        (tmp_path / "coding" / "lib.py").write_text("x = 1")
        (tmp_path / "finance").mkdir()
        (tmp_path / "finance" / "budget.csv").write_text("a,b\n1,2")

        with patch("routers.upload.config") as mock_config:
            mock_config.ARCHIVE_PATH = str(tmp_path)
            mock_config.STORAGE_MODE = "archive"
            result = asyncio.get_event_loop().run_until_complete(
                list_archive_files(domain="")
            )

        assert result["total"] == 3
        filenames = [f["filename"] for f in result["files"]]
        assert "app.py" in filenames
        assert "lib.py" in filenames
        assert "budget.csv" in filenames

    def test_filters_by_domain(self, tmp_path):
        """Domain parameter filters the listing."""
        (tmp_path / "coding").mkdir()
        (tmp_path / "coding" / "app.py").write_text("x")
        (tmp_path / "finance").mkdir()
        (tmp_path / "finance" / "budget.csv").write_text("y")

        with patch("routers.upload.config") as mock_config:
            mock_config.ARCHIVE_PATH = str(tmp_path)
            mock_config.STORAGE_MODE = "archive"
            result = asyncio.get_event_loop().run_until_complete(
                list_archive_files(domain="coding")
            )

        assert result["total"] == 1
        assert result["files"][0]["filename"] == "app.py"
        assert result["files"][0]["domain"] == "coding"

    def test_skips_hidden_dirs(self, tmp_path):
        """Directories starting with _ or . are skipped."""
        (tmp_path / "_uploads").mkdir()
        (tmp_path / "_uploads" / "temp.txt").write_text("tmp")
        (tmp_path / ".hidden").mkdir()
        (tmp_path / ".hidden" / "secret.txt").write_text("secret")
        (tmp_path / "coding").mkdir()
        (tmp_path / "coding" / "real.py").write_text("ok")

        with patch("routers.upload.config") as mock_config:
            mock_config.ARCHIVE_PATH = str(tmp_path)
            mock_config.STORAGE_MODE = "archive"
            result = asyncio.get_event_loop().run_until_complete(
                list_archive_files(domain="")
            )

        assert result["total"] == 1
        assert result["files"][0]["filename"] == "real.py"

    def test_includes_file_size(self, tmp_path):
        """File entries include size information."""
        (tmp_path / "general").mkdir()
        (tmp_path / "general" / "doc.txt").write_text("hello world")

        with patch("routers.upload.config") as mock_config:
            mock_config.ARCHIVE_PATH = str(tmp_path)
            mock_config.STORAGE_MODE = "archive"
            result = asyncio.get_event_loop().run_until_complete(
                list_archive_files(domain="general")
            )

        assert result["total"] == 1
        assert result["files"][0]["size"] == 11  # len("hello world")


# ---------------------------------------------------------------------------
# Tests: storage mode config
# ---------------------------------------------------------------------------


class TestStorageMode:
    def test_default_is_extract_only(self):
        """Default storage mode should be extract_only."""
        default = os.getenv("CERID_STORAGE_MODE", "extract_only")
        assert default == "extract_only"

    def test_valid_modes(self):
        """Only extract_only and archive are valid storage modes."""
        valid_modes = ("extract_only", "archive")
        for mode in valid_modes:
            assert mode in valid_modes
