# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Read/write user state files (settings, conversations, preferences) to the sync directory."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config

logger = logging.getLogger("ai-companion.sync.user_state")

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _user_dir(sync_dir: str) -> Path:
    """Return the ``{sync_dir}/user`` directory, creating it if needed."""
    p = Path(sync_dir) / "user"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _conversations_dir(sync_dir: str) -> Path:
    """Return the ``{sync_dir}/user/conversations`` directory, creating it if needed."""
    p = _user_dir(sync_dir) / "conversations"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    """Read a JSON file, returning empty dict on missing/invalid files."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("Cannot read %s: %s", path, exc)
        return {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    """Write data as JSON, creating parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")


def _is_conflict_copy(name: str) -> bool:
    """Return True if filename looks like a Dropbox conflict copy."""
    return "(conflicted copy" in name


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def write_settings(sync_dir: str, settings: dict[str, Any]) -> None:
    """Merge *settings* into ``user/settings.json``."""
    path = _user_dir(sync_dir) / "settings.json"
    existing = _read_json(path)
    existing.update(settings)
    existing["updated_at"] = _now_iso()
    existing["machine_id"] = config.MACHINE_ID
    _write_json(path, existing)
    logger.info("Wrote settings to %s", path)


def read_settings(sync_dir: str) -> dict[str, Any]:
    """Read settings from ``user/settings.json``. Returns empty dict if missing."""
    return _read_json(_user_dir(sync_dir) / "settings.json")


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------


def write_conversation(sync_dir: str, conversation: dict[str, Any]) -> None:
    """Write a single conversation to ``user/conversations/{id}.json``."""
    conv_id = conversation.get("id")
    if not conv_id:
        raise ValueError("Conversation must have an 'id' field")
    conv = dict(conversation)
    conv["_synced_at"] = _now_iso()
    conv["_machine_id"] = config.MACHINE_ID
    path = _conversations_dir(sync_dir) / f"{conv_id}.json"
    _write_json(path, conv)
    logger.info("Wrote conversation %s to %s", conv_id, path)


def read_conversations(sync_dir: str) -> list[dict[str, Any]]:
    """Read all conversations from ``user/conversations/``.

    Skips Dropbox conflict copies (files with ``(conflicted copy`` in the name).
    Returns empty list if directory is missing.
    """
    conv_dir = Path(sync_dir) / "user" / "conversations"
    if not conv_dir.is_dir():
        return []
    results: list[dict[str, Any]] = []
    for fp in sorted(conv_dir.glob("*.json")):
        if _is_conflict_copy(fp.name):
            logger.debug("Skipping conflict copy: %s", fp.name)
            continue
        data = _read_json(fp)
        if data:
            results.append(data)
    return results


def read_conversation(sync_dir: str, conv_id: str) -> dict[str, Any]:
    """Read a single conversation by ID. Returns empty dict if missing."""
    path = Path(sync_dir) / "user" / "conversations" / f"{conv_id}.json"
    return _read_json(path)


def delete_conversation(sync_dir: str, conv_id: str) -> None:
    """Delete a conversation file. No-op if the file does not exist."""
    path = Path(sync_dir) / "user" / "conversations" / f"{conv_id}.json"
    try:
        path.unlink()
        logger.info("Deleted conversation %s", conv_id)
    except FileNotFoundError:
        logger.debug("Conversation %s not found, nothing to delete", conv_id)


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------


def write_preferences(sync_dir: str, preferences: dict[str, Any]) -> None:
    """Merge *preferences* into ``user/state.json``."""
    path = _user_dir(sync_dir) / "state.json"
    existing = _read_json(path)
    existing.update(preferences)
    existing["updated_at"] = _now_iso()
    existing["machine_id"] = config.MACHINE_ID
    _write_json(path, existing)
    logger.info("Wrote preferences to %s", path)


def read_preferences(sync_dir: str) -> dict[str, Any]:
    """Read UI preferences from ``user/state.json``. Returns empty dict if missing."""
    return _read_json(_user_dir(sync_dir) / "state.json")
