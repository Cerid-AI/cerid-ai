# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for sync.user_state — user state file I/O."""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

# Stub the config module before importing the module under test.
_config_stub = type("config", (), {"MACHINE_ID": "test-machine"})()

with mock.patch.dict("sys.modules", {"config": _config_stub}):
    from sync.user_state import (
        delete_conversation,
        read_conversation,
        read_conversations,
        read_preferences,
        read_settings,
        write_conversation,
        write_preferences,
        write_settings,
    )


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class TestWriteSettings:
    def test_creates_file_with_correct_structure(self, tmp_path: Path) -> None:
        write_settings(str(tmp_path), {"theme": "dark"})
        path = tmp_path / "user" / "settings.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["theme"] == "dark"
        assert "updated_at" in data
        assert "machine_id" in data

    def test_merges_with_existing(self, tmp_path: Path) -> None:
        write_settings(str(tmp_path), {"theme": "dark", "lang": "en"})
        write_settings(str(tmp_path), {"theme": "light", "font_size": 14})
        data = json.loads((tmp_path / "user" / "settings.json").read_text())
        assert data["theme"] == "light"
        assert data["lang"] == "en"
        assert data["font_size"] == 14


class TestReadSettings:
    def test_returns_empty_when_no_file(self, tmp_path: Path) -> None:
        assert read_settings(str(tmp_path)) == {}

    def test_reads_existing_file(self, tmp_path: Path) -> None:
        write_settings(str(tmp_path), {"key": "value"})
        result = read_settings(str(tmp_path))
        assert result["key"] == "value"


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------


class TestWriteConversation:
    def test_creates_file_in_conversations_subdir(self, tmp_path: Path) -> None:
        conv = {"id": "conv-001", "title": "Hello", "messages": []}
        write_conversation(str(tmp_path), conv)
        path = tmp_path / "user" / "conversations" / "conv-001.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["id"] == "conv-001"
        assert data["title"] == "Hello"
        assert "_synced_at" in data
        assert "_machine_id" in data

    def test_raises_on_missing_id(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="must have an 'id'"):
            write_conversation(str(tmp_path), {"title": "No ID"})


class TestReadConversations:
    def test_returns_empty_when_no_dir(self, tmp_path: Path) -> None:
        assert read_conversations(str(tmp_path)) == []

    def test_reads_all_conversation_files(self, tmp_path: Path) -> None:
        write_conversation(str(tmp_path), {"id": "c1", "title": "First"})
        write_conversation(str(tmp_path), {"id": "c2", "title": "Second"})
        results = read_conversations(str(tmp_path))
        assert len(results) == 2
        ids = {r["id"] for r in results}
        assert ids == {"c1", "c2"}

    def test_skips_dropbox_conflict_copies(self, tmp_path: Path) -> None:
        write_conversation(str(tmp_path), {"id": "c1", "title": "Good"})
        # Create a Dropbox conflict copy manually
        conflict_path = (
            tmp_path
            / "user"
            / "conversations"
            / "c1 (conflicted copy 2026-03-14).json"
        )
        conflict_path.write_text(json.dumps({"id": "c1", "title": "Conflict"}))
        results = read_conversations(str(tmp_path))
        assert len(results) == 1
        assert results[0]["title"] == "Good"


class TestReadConversation:
    def test_reads_single_by_id(self, tmp_path: Path) -> None:
        write_conversation(str(tmp_path), {"id": "abc", "title": "Test"})
        result = read_conversation(str(tmp_path), "abc")
        assert result["id"] == "abc"
        assert result["title"] == "Test"

    def test_returns_empty_when_missing(self, tmp_path: Path) -> None:
        assert read_conversation(str(tmp_path), "nonexistent") == {}


class TestDeleteConversation:
    def test_removes_file(self, tmp_path: Path) -> None:
        write_conversation(str(tmp_path), {"id": "del-me", "title": "Bye"})
        path = tmp_path / "user" / "conversations" / "del-me.json"
        assert path.exists()
        delete_conversation(str(tmp_path), "del-me")
        assert not path.exists()

    def test_noop_when_missing(self, tmp_path: Path) -> None:
        # Should not raise
        delete_conversation(str(tmp_path), "does-not-exist")


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------


class TestWritePreferences:
    def test_creates_state_json(self, tmp_path: Path) -> None:
        write_preferences(str(tmp_path), {"sidebar_open": True})
        path = tmp_path / "user" / "state.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["sidebar_open"] is True
        assert "updated_at" in data
        assert "machine_id" in data

    def test_merges_with_existing(self, tmp_path: Path) -> None:
        write_preferences(str(tmp_path), {"sidebar_open": True})
        write_preferences(str(tmp_path), {"kb_pane_width": 400})
        data = json.loads((tmp_path / "user" / "state.json").read_text())
        assert data["sidebar_open"] is True
        assert data["kb_pane_width"] == 400


class TestReadPreferences:
    def test_returns_empty_when_no_file(self, tmp_path: Path) -> None:
        assert read_preferences(str(tmp_path)) == {}

    def test_reads_existing_file(self, tmp_path: Path) -> None:
        write_preferences(str(tmp_path), {"zoom": 1.5})
        result = read_preferences(str(tmp_path))
        assert result["zoom"] == 1.5
