# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for sync directory encryption."""
from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

# Stub the config module before importing the module under test.
_config_stub = type("config", (), {"MACHINE_ID": "test-machine", "ENCRYPT_SYNC": True})()

with mock.patch.dict("sys.modules", {"config": _config_stub}):
    import sync.user_state as _user_state_mod
    from sync.user_state import (
        read_conversation,
        read_conversations,
        read_preferences,
        read_settings,
        write_conversation,
        write_preferences,
        write_settings,
    )


def _mock_encrypt(v: str) -> str:
    return f"ENC:{v}"


def _mock_decrypt(v: str) -> str:
    return v[4:] if v.startswith("ENC:") else v


def test_encrypted_settings_roundtrip(tmp_path: Path) -> None:
    """Settings should encrypt on write and decrypt on read when key is set."""
    with mock.patch.object(_user_state_mod, "_encrypt_value", side_effect=_mock_encrypt):
        with mock.patch.object(_user_state_mod, "_decrypt_value", side_effect=_mock_decrypt):
            write_settings(str(tmp_path), {"theme": "dark"})
            raw = json.loads((tmp_path / "user" / "settings.json").read_text())
            assert raw["theme"] == "ENC:dark"
            # Metadata keys stay plaintext
            assert "machine_id" in raw
            assert "updated_at" in raw
            result = read_settings(str(tmp_path))
            assert result["theme"] == "dark"


def test_encrypted_conversation_roundtrip(tmp_path: Path) -> None:
    """Conversation messages should be encrypted on disk."""
    msgs = [{"role": "user", "content": "secret question"}]
    with mock.patch.object(_user_state_mod, "_encrypt_value", side_effect=_mock_encrypt):
        with mock.patch.object(_user_state_mod, "_decrypt_value", side_effect=_mock_decrypt):
            write_conversation(str(tmp_path), {"id": "c1", "title": "Test", "messages": msgs})
            raw = json.loads((tmp_path / "user" / "conversations" / "c1.json").read_text())
            assert raw["title"] == "ENC:Test"
            assert raw["messages"][0]["content"] == "ENC:secret question"
            # Metadata stays plaintext
            assert raw["id"] == "c1"
            assert raw["messages"][0]["role"] == "user"  # role is metadata
            assert "_synced_at" in raw
            # Read back and verify decryption
            convos = read_conversations(str(tmp_path))
            assert convos[0]["title"] == "Test"
            assert convos[0]["messages"][0]["content"] == "secret question"


def test_encrypted_single_conversation_read(tmp_path: Path) -> None:
    """read_conversation should also decrypt."""
    with mock.patch.object(_user_state_mod, "_encrypt_value", side_effect=_mock_encrypt):
        with mock.patch.object(_user_state_mod, "_decrypt_value", side_effect=_mock_decrypt):
            write_conversation(str(tmp_path), {"id": "c2", "title": "Private"})
            result = read_conversation(str(tmp_path), "c2")
            assert result["title"] == "Private"
            assert result["id"] == "c2"


def test_encrypted_preferences_roundtrip(tmp_path: Path) -> None:
    """Preferences should encrypt on write and decrypt on read."""
    with mock.patch.object(_user_state_mod, "_encrypt_value", side_effect=_mock_encrypt):
        with mock.patch.object(_user_state_mod, "_decrypt_value", side_effect=_mock_decrypt):
            write_preferences(str(tmp_path), {"api_endpoint": "https://secret.example.com"})
            raw = json.loads((tmp_path / "user" / "state.json").read_text())
            assert raw["api_endpoint"] == "ENC:https://secret.example.com"
            result = read_preferences(str(tmp_path))
            assert result["api_endpoint"] == "https://secret.example.com"


def test_no_encryption_without_key(tmp_path: Path) -> None:
    """Without encryption, values should be written in plaintext."""
    with mock.patch.object(_user_state_mod, "_encrypt_value", side_effect=lambda v: v):
        with mock.patch.object(_user_state_mod, "_decrypt_value", side_effect=lambda v: v):
            write_settings(str(tmp_path), {"theme": "dark"})
            raw = json.loads((tmp_path / "user" / "settings.json").read_text())
            assert raw["theme"] == "dark"


def test_settings_merge_decrypts_existing(tmp_path: Path) -> None:
    """When merging settings, existing encrypted values should be decrypted first."""
    with mock.patch.object(_user_state_mod, "_encrypt_value", side_effect=_mock_encrypt):
        with mock.patch.object(_user_state_mod, "_decrypt_value", side_effect=_mock_decrypt):
            write_settings(str(tmp_path), {"theme": "dark", "lang": "en"})
            # Write again -- should merge with decrypted existing values
            write_settings(str(tmp_path), {"theme": "light"})
            result = read_settings(str(tmp_path))
            assert result["theme"] == "light"
            assert result["lang"] == "en"


def test_nested_dict_encryption(tmp_path: Path) -> None:
    """Nested dicts should have their string values encrypted too."""
    with mock.patch.object(_user_state_mod, "_encrypt_value", side_effect=_mock_encrypt):
        with mock.patch.object(_user_state_mod, "_decrypt_value", side_effect=_mock_decrypt):
            write_settings(str(tmp_path), {"nested": {"private_note": "value", "count": 42}})
            raw = json.loads((tmp_path / "user" / "settings.json").read_text())
            assert raw["nested"]["private_note"] == "ENC:value"
            assert raw["nested"]["count"] == 42
            result = read_settings(str(tmp_path))
            assert result["nested"]["private_note"] == "value"
            assert result["nested"]["count"] == 42


def test_non_string_values_unchanged(tmp_path: Path) -> None:
    """Non-string values (int, bool, None) should pass through unchanged."""
    with mock.patch.object(_user_state_mod, "_encrypt_value", side_effect=_mock_encrypt):
        with mock.patch.object(_user_state_mod, "_decrypt_value", side_effect=_mock_decrypt):
            write_settings(str(tmp_path), {"count": 5, "enabled": True, "empty": None})
            raw = json.loads((tmp_path / "user" / "settings.json").read_text())
            assert raw["count"] == 5
            assert raw["enabled"] is True
            assert raw["empty"] is None
