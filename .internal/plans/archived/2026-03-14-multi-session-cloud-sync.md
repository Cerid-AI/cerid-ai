# Multi-Session Cloud Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Enable seamless multi-session, multi-computer state synchronization (conversations, settings, UI preferences) via Dropbox, with frameworks for future multi-user support.

**Architecture:** File-based sync extending the existing `cerid-sync` infrastructure. User state is persisted to JSON files in `~/Dropbox/cerid-sync/user/`, synced automatically by Dropbox, and hydrated on MCP startup. localStorage remains the frontend cache for fast reads and offline resilience.

**Tech Stack:** FastAPI (Python 3.11), React 19, Dropbox (file transport), JSON

---

### Task 1: User State File I/O Module

**Files:**
- Create: `src/mcp/sync/user_state.py`
- Test: `src/mcp/tests/test_sync_user_state.py`

**Context:** This module handles reading/writing user state files to the sync directory (`~/Dropbox/cerid-sync/user/`). It provides the persistence layer that all other tasks depend on. Uses the existing `sync/_helpers.py` patterns (`_ensure_dir`, `_write_jsonl`, `_iter_jsonl`).

**Important:** The sync dir mount in Docker is currently `:ro` (read-only). Task 5 changes this. For now, tests use a temp directory.

**Step 1: Write the failing tests**

File: `src/mcp/tests/test_sync_user_state.py`

```python
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for sync/user_state.py — user state file I/O."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest


@pytest.fixture()
def sync_dir(tmp_path: Path) -> str:
    return str(tmp_path / "cerid-sync")


class TestWriteSettings:
    def test_creates_settings_file(self, sync_dir: str):
        from sync.user_state import write_settings

        settings = {"enable_feedback_loop": True, "cost_sensitivity": "low"}
        write_settings(sync_dir, settings)

        path = os.path.join(sync_dir, "user", "settings.json")
        assert os.path.isfile(path)
        with open(path) as f:
            data = json.load(f)
        assert data["settings"] == settings
        assert "updated_at" in data
        assert "machine_id" in data

    def test_merges_with_existing(self, sync_dir: str):
        from sync.user_state import write_settings

        write_settings(sync_dir, {"enable_feedback_loop": True})
        write_settings(sync_dir, {"cost_sensitivity": "high"})

        path = os.path.join(sync_dir, "user", "settings.json")
        with open(path) as f:
            data = json.load(f)
        assert data["settings"]["enable_feedback_loop"] is True
        assert data["settings"]["cost_sensitivity"] == "high"


class TestReadSettings:
    def test_returns_empty_when_no_file(self, sync_dir: str):
        from sync.user_state import read_settings

        result = read_settings(sync_dir)
        assert result == {}

    def test_reads_existing_file(self, sync_dir: str):
        from sync.user_state import write_settings, read_settings

        write_settings(sync_dir, {"enable_feedback_loop": True})
        result = read_settings(sync_dir)
        assert result["enable_feedback_loop"] is True


class TestWriteConversation:
    def test_creates_conversation_file(self, sync_dir: str):
        from sync.user_state import write_conversation

        convo = {
            "id": "conv-123",
            "title": "Test",
            "messages": [{"id": "m1", "role": "user", "content": "hello"}],
            "model": "openrouter/openai/gpt-4o-mini",
            "createdAt": 1710000000000,
            "updatedAt": 1710000000000,
        }
        write_conversation(sync_dir, convo)

        path = os.path.join(sync_dir, "user", "conversations", "conv-123.json")
        assert os.path.isfile(path)
        with open(path) as f:
            data = json.load(f)
        assert data["id"] == "conv-123"
        assert data["_synced_at"] is not None
        assert data["_machine_id"] is not None


class TestReadConversations:
    def test_returns_empty_when_no_dir(self, sync_dir: str):
        from sync.user_state import read_conversations

        result = read_conversations(sync_dir)
        assert result == []

    def test_reads_all_conversations(self, sync_dir: str):
        from sync.user_state import write_conversation, read_conversations

        for i in range(3):
            write_conversation(sync_dir, {
                "id": f"conv-{i}", "title": f"Test {i}", "messages": [],
                "model": "openrouter/openai/gpt-4o-mini",
                "createdAt": 1710000000000 + i, "updatedAt": 1710000000000 + i,
            })
        result = read_conversations(sync_dir)
        assert len(result) == 3


class TestDeleteConversation:
    def test_removes_file(self, sync_dir: str):
        from sync.user_state import write_conversation, delete_conversation

        write_conversation(sync_dir, {
            "id": "conv-del", "title": "Delete me", "messages": [],
            "model": "openrouter/openai/gpt-4o-mini",
            "createdAt": 1710000000000, "updatedAt": 1710000000000,
        })
        delete_conversation(sync_dir, "conv-del")

        path = os.path.join(sync_dir, "user", "conversations", "conv-del.json")
        assert not os.path.exists(path)

    def test_noop_when_missing(self, sync_dir: str):
        from sync.user_state import delete_conversation

        # Should not raise
        delete_conversation(sync_dir, "nonexistent")


class TestWritePreferences:
    def test_creates_state_file(self, sync_dir: str):
        from sync.user_state import write_preferences

        prefs = {"ui_mode": "advanced", "onboarding_complete": True}
        write_preferences(sync_dir, prefs)

        path = os.path.join(sync_dir, "user", "state.json")
        assert os.path.isfile(path)
        with open(path) as f:
            data = json.load(f)
        assert data["preferences"] == prefs


class TestReadPreferences:
    def test_returns_empty_when_no_file(self, sync_dir: str):
        from sync.user_state import read_preferences

        result = read_preferences(sync_dir)
        assert result == {}

    def test_reads_existing(self, sync_dir: str):
        from sync.user_state import write_preferences, read_preferences

        write_preferences(sync_dir, {"ui_mode": "simple"})
        result = read_preferences(sync_dir)
        assert result["ui_mode"] == "simple"


class TestDropboxConflictHandling:
    def test_ignores_dropbox_conflict_copies(self, sync_dir: str):
        """Dropbox creates 'file (conflicted copy ...)' files — we skip them."""
        from sync.user_state import write_conversation, read_conversations

        write_conversation(sync_dir, {
            "id": "conv-1", "title": "Real", "messages": [],
            "model": "openrouter/openai/gpt-4o-mini",
            "createdAt": 1710000000000, "updatedAt": 1710000000000,
        })

        # Simulate Dropbox conflict copy
        conv_dir = os.path.join(sync_dir, "user", "conversations")
        conflict_path = os.path.join(conv_dir, "conv-1 (conflicted copy 2026-03-14).json")
        with open(conflict_path, "w") as f:
            json.dump({"id": "conv-1", "title": "Conflict"}, f)

        result = read_conversations(sync_dir)
        assert len(result) == 1
        assert result[0]["title"] == "Real"
```

**Step 2: Run tests to verify they fail**

```bash
docker run --rm -v "$(pwd)/src/mcp:/work" -w /work python:3.11-slim bash -c \
  "pip install -q -r requirements.txt -r requirements-dev.txt && python -m pytest tests/test_sync_user_state.py -v"
```

Expected: FAIL with `ModuleNotFoundError: No module named 'sync.user_state'`

**Step 3: Implement the module**

File: `src/mcp/sync/user_state.py`

```python
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Read/write user state files (settings, conversations, preferences) to the sync directory.

Sync directory layout::

    cerid-sync/
    └── user/
        ├── settings.json          # Server + UI settings
        ├── state.json             # UI preferences (mode, onboarding)
        └── conversations/         # One file per conversation
            ├── {uuid}.json
            └── ...

Multi-user future: ``user/`` becomes ``users/{user_id}/``.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config

logger = logging.getLogger("ai-companion.sync.user_state")

# Dropbox conflict copies match: "file (conflicted copy YYYY-MM-DD).ext"
_CONFLICT_RE = re.compile(r"\(conflicted copy ", re.IGNORECASE)


def _user_dir(sync_dir: str, user_id: str = "default") -> Path:
    """Return the user state subdirectory, creating it if needed."""
    p = Path(sync_dir) / "user"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _machine_id() -> str:
    return getattr(config, "MACHINE_ID", os.uname().nodename.split(".")[0])


# ── Settings ─────────────────────────────────────────────────────────────────


def write_settings(sync_dir: str, settings: dict[str, Any]) -> None:
    """Merge *settings* into the settings file and write to disk."""
    udir = _user_dir(sync_dir)
    path = udir / "settings.json"

    existing: dict[str, Any] = {}
    if path.is_file():
        try:
            with open(path) as f:
                data = json.load(f)
            existing = data.get("settings", {})
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Cannot read existing settings.json: %s", exc)

    existing.update(settings)

    payload = {
        "settings": existing,
        "updated_at": _now_iso(),
        "machine_id": _machine_id(),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)

    logger.info("Settings persisted to %s (%d keys)", path, len(existing))


def read_settings(sync_dir: str) -> dict[str, Any]:
    """Read settings from the sync directory. Returns empty dict if not found."""
    path = Path(sync_dir) / "user" / "settings.json"
    if not path.is_file():
        return {}
    try:
        with open(path) as f:
            data = json.load(f)
        return data.get("settings", {})
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Cannot read settings.json: %s", exc)
        return {}


# ── Conversations ────────────────────────────────────────────────────────────


def write_conversation(sync_dir: str, conversation: dict[str, Any]) -> None:
    """Write a single conversation to its own JSON file."""
    conv_dir = _user_dir(sync_dir) / "conversations"
    conv_dir.mkdir(parents=True, exist_ok=True)

    conv_id = conversation.get("id")
    if not conv_id:
        logger.warning("Conversation missing 'id' field — skipping write")
        return

    # Add sync metadata (preserved alongside conversation data)
    conversation["_synced_at"] = _now_iso()
    conversation["_machine_id"] = _machine_id()

    path = conv_dir / f"{conv_id}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(conversation, f, indent=2, default=str)

    logger.debug("Conversation %s persisted to %s", conv_id, path)


def read_conversations(sync_dir: str) -> list[dict[str, Any]]:
    """Read all conversations from the sync directory.

    Skips Dropbox conflict copies (files with '(conflicted copy ...)' in name).
    """
    conv_dir = Path(sync_dir) / "user" / "conversations"
    if not conv_dir.is_dir():
        return []

    conversations: list[dict[str, Any]] = []
    for filepath in conv_dir.glob("*.json"):
        if _CONFLICT_RE.search(filepath.name):
            logger.debug("Skipping Dropbox conflict copy: %s", filepath.name)
            continue
        try:
            with open(filepath) as f:
                data = json.load(f)
            conversations.append(data)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Cannot read conversation %s: %s", filepath, exc)

    return conversations


def read_conversation(sync_dir: str, conv_id: str) -> dict[str, Any] | None:
    """Read a single conversation by ID. Returns None if not found."""
    path = Path(sync_dir) / "user" / "conversations" / f"{conv_id}.json"
    if not path.is_file():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Cannot read conversation %s: %s", conv_id, exc)
        return None


def delete_conversation(sync_dir: str, conv_id: str) -> None:
    """Delete a conversation file from the sync directory."""
    path = Path(sync_dir) / "user" / "conversations" / f"{conv_id}.json"
    try:
        path.unlink(missing_ok=True)
        logger.info("Conversation %s deleted from sync", conv_id)
    except OSError as exc:
        logger.warning("Cannot delete conversation %s: %s", conv_id, exc)


# ── UI Preferences ───────────────────────────────────────────────────────────


def write_preferences(sync_dir: str, preferences: dict[str, Any]) -> None:
    """Write UI preferences (mode, onboarding, theme) to state.json."""
    udir = _user_dir(sync_dir)
    path = udir / "state.json"

    existing: dict[str, Any] = {}
    if path.is_file():
        try:
            with open(path) as f:
                data = json.load(f)
            existing = data.get("preferences", {})
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Cannot read existing state.json: %s", exc)

    existing.update(preferences)

    payload = {
        "preferences": existing,
        "updated_at": _now_iso(),
        "machine_id": _machine_id(),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)

    logger.info("Preferences persisted to %s", path)


def read_preferences(sync_dir: str) -> dict[str, Any]:
    """Read UI preferences from the sync directory. Returns empty dict if not found."""
    path = Path(sync_dir) / "user" / "state.json"
    if not path.is_file():
        return {}
    try:
        with open(path) as f:
            data = json.load(f)
        return data.get("preferences", {})
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Cannot read state.json: %s", exc)
        return {}
```

**Step 4: Run tests to verify they pass**

```bash
docker run --rm -v "$(pwd)/src/mcp:/work" -w /work python:3.11-slim bash -c \
  "pip install -q -r requirements.txt -r requirements-dev.txt && python -m pytest tests/test_sync_user_state.py -v"
```

Expected: All 10 tests PASS

**Step 5: Commit**

```bash
git add src/mcp/sync/user_state.py src/mcp/tests/test_sync_user_state.py
git commit -m "feat: add user state file I/O module for cloud sync"
```

---

### Task 2: User State API Router

**Files:**
- Create: `src/mcp/routers/user_state.py`
- Modify: `src/mcp/main.py` (add router import + registration)
- Test: `src/mcp/tests/test_router_user_state.py`

**Context:** This router exposes CRUD endpoints for user state. The frontend calls these to sync conversations, settings, and preferences to the server, which persists them to the sync directory. The router uses `sync.user_state` from Task 1.

**Step 1: Write the failing tests**

File: `src/mcp/tests/test_router_user_state.py`

```python
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for routers/user_state.py — user state CRUD endpoints."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture()
def sync_dir(tmp_path):
    return str(tmp_path / "cerid-sync")


@pytest.fixture()
def app(sync_dir: str):
    with patch("routers.user_state._sync_dir", return_value=sync_dir):
        from routers.user_state import router
        app = FastAPI()
        app.include_router(router)
        yield app


@pytest.fixture()
def client(app):
    return TestClient(app)


class TestGetUserState:
    def test_returns_empty_state(self, client):
        resp = client.get("/user-state")
        assert resp.status_code == 200
        data = resp.json()
        assert data["settings"] == {}
        assert data["preferences"] == {}
        assert data["conversation_ids"] == []


class TestConversations:
    def test_save_and_list(self, client):
        convo = {
            "id": "c1", "title": "Hello", "messages": [],
            "model": "openrouter/openai/gpt-4o-mini",
            "createdAt": 1710000000000, "updatedAt": 1710000000000,
        }
        resp = client.post("/user-state/conversations", json=convo)
        assert resp.status_code == 200

        resp = client.get("/user-state/conversations")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["conversations"]) == 1
        assert data["conversations"][0]["id"] == "c1"

    def test_get_single(self, client):
        convo = {
            "id": "c2", "title": "Single", "messages": [{"id": "m1", "role": "user", "content": "hi"}],
            "model": "openrouter/openai/gpt-4o-mini",
            "createdAt": 1710000000000, "updatedAt": 1710000000000,
        }
        client.post("/user-state/conversations", json=convo)

        resp = client.get("/user-state/conversations/c2")
        assert resp.status_code == 200
        assert resp.json()["id"] == "c2"
        assert len(resp.json()["messages"]) == 1

    def test_get_missing_returns_404(self, client):
        resp = client.get("/user-state/conversations/nonexistent")
        assert resp.status_code == 404

    def test_delete(self, client):
        convo = {
            "id": "c3", "title": "Delete me", "messages": [],
            "model": "openrouter/openai/gpt-4o-mini",
            "createdAt": 1710000000000, "updatedAt": 1710000000000,
        }
        client.post("/user-state/conversations", json=convo)
        resp = client.delete("/user-state/conversations/c3")
        assert resp.status_code == 200

        resp = client.get("/user-state/conversations/c3")
        assert resp.status_code == 404

    def test_bulk_save(self, client):
        convos = [
            {"id": f"b{i}", "title": f"Bulk {i}", "messages": [],
             "model": "openrouter/openai/gpt-4o-mini",
             "createdAt": 1710000000000, "updatedAt": 1710000000000}
            for i in range(3)
        ]
        resp = client.post("/user-state/conversations/bulk", json=convos)
        assert resp.status_code == 200
        assert resp.json()["saved"] == 3

        resp = client.get("/user-state/conversations")
        assert len(resp.json()["conversations"]) == 3


class TestPreferences:
    def test_save_and_read(self, client):
        resp = client.patch("/user-state/preferences", json={
            "ui_mode": "advanced",
            "onboarding_complete": True,
            "routing_mode": "auto",
        })
        assert resp.status_code == 200

        resp = client.get("/user-state")
        prefs = resp.json()["preferences"]
        assert prefs["ui_mode"] == "advanced"
        assert prefs["onboarding_complete"] is True
        assert prefs["routing_mode"] == "auto"
```

**Step 2: Run tests to verify they fail**

```bash
docker run --rm -v "$(pwd)/src/mcp:/work" -w /work python:3.11-slim bash -c \
  "pip install -q -r requirements.txt -r requirements-dev.txt && python -m pytest tests/test_router_user_state.py -v"
```

Expected: FAIL with `ModuleNotFoundError: No module named 'routers.user_state'`

**Step 3: Implement the router**

File: `src/mcp/routers/user_state.py`

```python
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""User state endpoints — sync conversations, settings, and preferences to the cloud.

These endpoints persist user state to the Dropbox sync directory so it survives
MCP restarts and syncs between machines. The frontend continues to use
localStorage as the immediate cache; these endpoints are the durable backing store.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

import config
from sync.user_state import (
    delete_conversation,
    read_conversation,
    read_conversations,
    read_preferences,
    read_settings,
    write_conversation,
    write_preferences,
)

router = APIRouter(prefix="/user-state", tags=["user-state"])
logger = logging.getLogger("ai-companion.user_state")


def _sync_dir() -> str:
    """Return the sync directory path. Extracted for test patching."""
    return getattr(config, "SYNC_DIR", "")


# ── Overview ─────────────────────────────────────────────────────────────────


@router.get("")
async def get_user_state():
    """Return a summary of persisted user state."""
    sd = _sync_dir()
    if not sd:
        return {"settings": {}, "preferences": {}, "conversation_ids": []}

    convos = read_conversations(sd)
    return {
        "settings": read_settings(sd),
        "preferences": read_preferences(sd),
        "conversation_ids": [c.get("id") for c in convos if c.get("id")],
    }


# ── Conversations ────────────────────────────────────────────────────────────


@router.get("/conversations")
async def list_conversations():
    """List all synced conversations (summaries — no full message bodies for listing)."""
    sd = _sync_dir()
    if not sd:
        return {"conversations": []}

    convos = read_conversations(sd)
    return {"conversations": convos}


@router.get("/conversations/{conv_id}")
async def get_conversation_endpoint(conv_id: str):
    """Get a single conversation by ID with full messages."""
    sd = _sync_dir()
    if not sd:
        raise HTTPException(status_code=404, detail="Sync directory not configured")

    convo = read_conversation(sd, conv_id)
    if convo is None:
        raise HTTPException(status_code=404, detail=f"Conversation {conv_id} not found")
    return convo


@router.post("/conversations")
async def save_conversation(conversation: dict[str, Any]):
    """Save or update a single conversation."""
    sd = _sync_dir()
    if not sd:
        raise HTTPException(status_code=503, detail="Sync directory not configured")

    if not conversation.get("id"):
        raise HTTPException(status_code=400, detail="Conversation must have an 'id' field")

    write_conversation(sd, conversation)
    return {"status": "saved", "id": conversation["id"]}


@router.post("/conversations/bulk")
async def save_conversations_bulk(conversations: list[dict[str, Any]]):
    """Save multiple conversations at once (used for initial sync)."""
    sd = _sync_dir()
    if not sd:
        raise HTTPException(status_code=503, detail="Sync directory not configured")

    saved = 0
    for convo in conversations:
        if convo.get("id"):
            write_conversation(sd, convo)
            saved += 1

    return {"status": "saved", "saved": saved}


@router.delete("/conversations/{conv_id}")
async def delete_conversation_endpoint(conv_id: str):
    """Delete a conversation from the sync directory."""
    sd = _sync_dir()
    if not sd:
        raise HTTPException(status_code=503, detail="Sync directory not configured")

    delete_conversation(sd, conv_id)
    return {"status": "deleted", "id": conv_id}


# ── Preferences ──────────────────────────────────────────────────────────────


@router.patch("/preferences")
async def update_preferences(preferences: dict[str, Any]):
    """Save UI preferences (mode, onboarding, routing mode, etc.)."""
    sd = _sync_dir()
    if not sd:
        raise HTTPException(status_code=503, detail="Sync directory not configured")

    write_preferences(sd, preferences)
    return {"status": "saved", "updated": list(preferences.keys())}
```

**Step 4: Register the router in main.py**

File: `src/mcp/main.py` — Add import and registration:

After the existing router imports (line ~37), add:
```python
from routers import user_state
```

In the `_api_routers` list (line ~195), add:
```python
user_state.router,
```

**Step 5: Run tests to verify they pass**

```bash
docker run --rm -v "$(pwd)/src/mcp:/work" -w /work python:3.11-slim bash -c \
  "pip install -q -r requirements.txt -r requirements-dev.txt && python -m pytest tests/test_router_user_state.py -v"
```

Expected: All 7 tests PASS

**Step 6: Commit**

```bash
git add src/mcp/routers/user_state.py src/mcp/tests/test_router_user_state.py src/mcp/main.py
git commit -m "feat: add user state API router for cloud sync"
```

---

### Task 3: Persist Settings to Sync Directory on PATCH

**Files:**
- Modify: `src/mcp/routers/settings.py` (add write_settings call at end of PATCH handler)
- Test: `src/mcp/tests/test_settings_persistence.py`

**Context:** Currently `PATCH /settings` updates in-memory config but nothing persists across restarts. After this task, every settings update also writes to `user/settings.json` in the sync directory, making settings durable.

**Step 1: Write the failing test**

File: `src/mcp/tests/test_settings_persistence.py`

```python
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for settings persistence to sync directory."""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture()
def sync_dir(tmp_path: Path) -> str:
    return str(tmp_path / "cerid-sync")


@pytest.fixture()
def client(sync_dir: str):
    with patch("config.SYNC_DIR", sync_dir):
        from routers.settings import router
        app = FastAPI()
        app.include_router(router)
        yield TestClient(app)


class TestSettingsPersistence:
    def test_patch_writes_to_sync_dir(self, client, sync_dir: str):
        resp = client.patch("/settings", json={"enable_feedback_loop": True})
        assert resp.status_code == 200

        path = os.path.join(sync_dir, "user", "settings.json")
        assert os.path.isfile(path)
        with open(path) as f:
            data = json.load(f)
        assert data["settings"]["enable_feedback_loop"] is True

    def test_multiple_patches_merge(self, client, sync_dir: str):
        client.patch("/settings", json={"enable_feedback_loop": True})
        client.patch("/settings", json={"cost_sensitivity": "high"})

        path = os.path.join(sync_dir, "user", "settings.json")
        with open(path) as f:
            data = json.load(f)
        assert data["settings"]["enable_feedback_loop"] is True
        assert data["settings"]["cost_sensitivity"] == "high"

    def test_skips_when_no_sync_dir(self, client):
        """Should not crash when SYNC_DIR is empty."""
        with patch("config.SYNC_DIR", ""):
            resp = client.patch("/settings", json={"enable_feedback_loop": True})
            assert resp.status_code == 200
```

**Step 2: Run tests to verify they fail**

```bash
docker run --rm -v "$(pwd)/src/mcp:/work" -w /work python:3.11-slim bash -c \
  "pip install -q -r requirements.txt -r requirements-dev.txt && python -m pytest tests/test_settings_persistence.py -v"
```

Expected: `test_patch_writes_to_sync_dir` FAIL — settings.json not created

**Step 3: Add persistence to the settings PATCH handler**

File: `src/mcp/routers/settings.py`

At the end of `update_settings_endpoint()`, before the final `return`, add:

```python
    # Persist to sync directory for cross-machine/restart durability
    try:
        if config.SYNC_DIR:
            from sync.user_state import write_settings
            write_settings(config.SYNC_DIR, updated)
    except Exception as exc:
        logger.warning("Failed to persist settings to sync dir: %s", exc)
```

This goes right before the `logger.info(f"Settings updated: {updated}")` line.

**Step 4: Run tests to verify they pass**

```bash
docker run --rm -v "$(pwd)/src/mcp:/work" -w /work python:3.11-slim bash -c \
  "pip install -q -r requirements.txt -r requirements-dev.txt && python -m pytest tests/test_settings_persistence.py -v"
```

Expected: All 3 tests PASS

**Step 5: Commit**

```bash
git add src/mcp/routers/settings.py src/mcp/tests/test_settings_persistence.py
git commit -m "feat: persist settings to sync directory on PATCH"
```

---

### Task 4: Hydrate Settings from Sync Directory on Startup

**Files:**
- Modify: `src/mcp/main.py` (add hydration step in lifespan)
- Create: `src/mcp/tests/test_startup_hydration.py`

**Context:** On MCP server startup, read `user/settings.json` from the sync directory and apply values to the config module. This makes settings survive container restarts and syncs settings set on another machine.

**Step 1: Write the failing test**

File: `src/mcp/tests/test_startup_hydration.py`

```python
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for startup settings hydration from sync directory."""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture()
def sync_dir(tmp_path: Path) -> str:
    sd = str(tmp_path / "cerid-sync")
    user_dir = os.path.join(sd, "user")
    os.makedirs(user_dir, exist_ok=True)
    return sd


class TestHydrateSettingsFromSync:
    def test_hydrates_boolean_toggles(self, sync_dir: str):
        # Write settings file
        path = os.path.join(sync_dir, "user", "settings.json")
        with open(path, "w") as f:
            json.dump({"settings": {
                "enable_feedback_loop": True,
                "enable_auto_inject": False,
            }, "updated_at": "2026-03-14T00:00:00Z", "machine_id": "test"}, f)

        from main import _hydrate_settings_from_sync
        with patch("config.SYNC_DIR", sync_dir):
            _hydrate_settings_from_sync()

        import config
        assert config.ENABLE_FEEDBACK_LOOP is True

    def test_hydrates_numeric_params(self, sync_dir: str):
        path = os.path.join(sync_dir, "user", "settings.json")
        with open(path, "w") as f:
            json.dump({"settings": {
                "cost_sensitivity": "high",
                "hallucination_threshold": 0.9,
            }, "updated_at": "2026-03-14T00:00:00Z", "machine_id": "test"}, f)

        from main import _hydrate_settings_from_sync
        with patch("config.SYNC_DIR", sync_dir):
            _hydrate_settings_from_sync()

        import config
        assert config.COST_SENSITIVITY == "high"
        assert config.HALLUCINATION_THRESHOLD == 0.9

    def test_noop_when_no_sync_dir(self):
        from main import _hydrate_settings_from_sync
        with patch("config.SYNC_DIR", ""):
            _hydrate_settings_from_sync()  # should not raise

    def test_noop_when_no_settings_file(self, sync_dir: str):
        from main import _hydrate_settings_from_sync
        with patch("config.SYNC_DIR", sync_dir):
            _hydrate_settings_from_sync()  # should not raise
```

**Step 2: Run tests to verify they fail**

```bash
docker run --rm -v "$(pwd)/src/mcp:/work" -w /work python:3.11-slim bash -c \
  "pip install -q -r requirements.txt -r requirements-dev.txt && python -m pytest tests/test_startup_hydration.py -v"
```

Expected: FAIL with `ImportError: cannot import name '_hydrate_settings_from_sync' from 'main'`

**Step 3: Implement hydration function and add to lifespan**

File: `src/mcp/main.py`

Add this function before the `lifespan()` function:

```python
def _hydrate_settings_from_sync() -> None:
    """Load persisted settings from sync directory on startup.

    Applies saved values to the config module so settings survive restarts
    and sync across machines. Only hydrates known, safe-to-apply settings.
    """
    import config as cfg
    try:
        if not getattr(cfg, "SYNC_DIR", ""):
            return
        from sync.user_state import read_settings
        saved = read_settings(cfg.SYNC_DIR)
        if not saved:
            return

        from utils.features import set_toggle

        # Boolean toggles (via feature toggle registry)
        _toggle_map = {
            "enable_feedback_loop": None,
            "enable_hallucination_check": None,
            "enable_memory_extraction": None,
            "enable_auto_inject": None,
            "enable_model_router": None,
            "enable_self_rag": None,
            "enable_contextual_chunks": None,
            "enable_adaptive_retrieval": None,
            "enable_query_decomposition": None,
            "enable_mmr_diversity": None,
            "enable_intelligent_assembly": None,
            "enable_late_interaction": None,
            "enable_semantic_cache": None,
        }
        for key in _toggle_map:
            if key in saved and isinstance(saved[key], bool):
                set_toggle(key, saved[key])

        # Categorical settings
        if saved.get("categorize_mode") in ("manual", "smart", "pro"):
            cfg.CATEGORIZE_MODE = saved["categorize_mode"]
        if saved.get("cost_sensitivity") in ("low", "medium", "high"):
            cfg.COST_SENSITIVITY = saved["cost_sensitivity"]
        if saved.get("storage_mode") in ("extract_only", "archive"):
            cfg.STORAGE_MODE = saved["storage_mode"]

        # Numeric settings (with range validation)
        _float_map = {
            "hallucination_threshold": (0.0, 1.0),
            "auto_inject_threshold": (0.5, 1.0),
            "hybrid_vector_weight": (0.0, 1.0),
            "hybrid_keyword_weight": (0.0, 1.0),
            "rerank_llm_weight": (0.0, 1.0),
            "rerank_original_weight": (0.0, 1.0),
        }
        for key, (lo, hi) in _float_map.items():
            if key in saved:
                val = saved[key]
                if isinstance(val, (int, float)) and lo <= val <= hi:
                    setattr(cfg, key.upper(), val)

        logger.info("Hydrated %d settings from sync directory", len(saved))
    except Exception as exc:
        logger.warning("Settings hydration from sync failed (using defaults): %s", exc)
```

In the `lifespan()` function, add this block right after the auto-import check (around line 102, after the `auto_import_if_empty()` try/except):

```python
    # Hydrate settings from sync directory (restores settings across restarts)
    try:
        _hydrate_settings_from_sync()
    except Exception as e:
        logger.warning(f"Settings hydration failed: {e}")
```

**Step 4: Run tests to verify they pass**

```bash
docker run --rm -v "$(pwd)/src/mcp:/work" -w /work python:3.11-slim bash -c \
  "pip install -q -r requirements.txt -r requirements-dev.txt && python -m pytest tests/test_startup_hydration.py -v"
```

Expected: All 4 tests PASS

**Step 5: Commit**

```bash
git add src/mcp/main.py src/mcp/tests/test_startup_hydration.py
git commit -m "feat: hydrate settings from sync directory on MCP startup"
```

---

### Task 5: Docker Volume Mount + Sync Directory Validation

**Files:**
- Modify: `src/mcp/docker-compose.yml` (change `/sync:ro` to `/sync`)
- Modify: `scripts/validate-env.sh` (add sync dir check)

**Context:** The sync directory is currently mounted read-only in Docker. Since the MCP server now writes user state files, we need read-write access. Also add a validation check to `validate-env.sh`.

**Step 1: Change Docker volume mount to read-write**

File: `src/mcp/docker-compose.yml`

Change line 12:
```yaml
      - ${CERID_SYNC_DIR:-~/Dropbox/cerid-sync}:/sync:ro
```
to:
```yaml
      - ${CERID_SYNC_DIR:-~/Dropbox/cerid-sync}:/sync
```

**Step 2: Add sync dir check to validate-env.sh**

File: `scripts/validate-env.sh`

In the `!QUICK` block (before the final `fi` on the line that says `# end !QUICK for checks 7–9`), add a new check:

```bash
    # ── Check 10: Sync directory writable ────────────────────────────────────
    SYNC_TEST_DIR="${CERID_SYNC_DIR:-$HOME/Dropbox/cerid-sync}"
    if [ -d "$SYNC_TEST_DIR" ]; then
        if [ -w "$SYNC_TEST_DIR" ]; then
            pass "Sync directory is writable: $SYNC_TEST_DIR"
        else
            fail "Sync directory exists but is not writable: $SYNC_TEST_DIR"
        fi
    fi
```

**Step 3: Commit**

```bash
git add src/mcp/docker-compose.yml scripts/validate-env.sh
git commit -m "feat: make sync dir writable in Docker for user state persistence"
```

---

### Task 6: Frontend API Client Additions

**Files:**
- Modify: `src/web/src/lib/api.ts` (add user-state API functions)
- Test: `src/web/src/__tests__/api-user-state.test.ts`

**Context:** Add API client functions that the frontend hooks will use to sync conversations, preferences, and load state from the server. These are fire-and-forget like the existing `updateSettings()` — failures are silent (localStorage is the fallback).

**Step 1: Write the failing tests**

File: `src/web/src/__tests__/api-user-state.test.ts`

```typescript
// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"

// Mock fetch globally
const mockFetch = vi.fn()
vi.stubGlobal("fetch", mockFetch)

// Must import after mocking fetch
const api = await import("@/lib/api")

describe("User State API", () => {
  beforeEach(() => {
    mockFetch.mockReset()
  })

  describe("fetchUserState", () => {
    it("returns parsed user state", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({
          settings: { enable_feedback_loop: true },
          preferences: { ui_mode: "advanced" },
          conversation_ids: ["c1", "c2"],
        }),
      })

      const result = await api.fetchUserState()
      expect(result.settings.enable_feedback_loop).toBe(true)
      expect(result.preferences.ui_mode).toBe("advanced")
      expect(result.conversation_ids).toHaveLength(2)
    })
  })

  describe("syncConversation", () => {
    it("sends POST with conversation data", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ status: "saved", id: "c1" }),
      })

      await api.syncConversation({
        id: "c1", title: "Test", messages: [],
        model: "openrouter/openai/gpt-4o-mini",
        createdAt: 123, updatedAt: 456,
      })

      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining("/user-state/conversations"),
        expect.objectContaining({ method: "POST" }),
      )
    })
  })

  describe("syncConversationsBulk", () => {
    it("sends POST with array of conversations", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ status: "saved", saved: 2 }),
      })

      await api.syncConversationsBulk([
        { id: "c1", title: "A", messages: [], model: "m", createdAt: 1, updatedAt: 1 },
        { id: "c2", title: "B", messages: [], model: "m", createdAt: 2, updatedAt: 2 },
      ])

      const body = JSON.parse(mockFetch.mock.calls[0][1].body)
      expect(body).toHaveLength(2)
    })
  })

  describe("deleteConversationSync", () => {
    it("sends DELETE request", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ status: "deleted" }),
      })

      await api.deleteConversationSync("c1")

      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining("/user-state/conversations/c1"),
        expect.objectContaining({ method: "DELETE" }),
      )
    })
  })

  describe("fetchSyncedConversations", () => {
    it("returns conversations array", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({
          conversations: [{ id: "c1", title: "Test" }],
        }),
      })

      const result = await api.fetchSyncedConversations()
      expect(result).toHaveLength(1)
    })
  })

  describe("syncPreferences", () => {
    it("sends PATCH with preferences", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ status: "saved" }),
      })

      await api.syncPreferences({ ui_mode: "advanced" })

      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining("/user-state/preferences"),
        expect.objectContaining({ method: "PATCH" }),
      )
    })
  })
})
```

**Step 2: Run tests to verify they fail**

```bash
cd src/web && npx vitest run src/__tests__/api-user-state.test.ts
```

Expected: FAIL — functions not exported from api.ts

**Step 3: Add API functions**

File: `src/web/src/lib/api.ts` — Add these functions at the end of the file (before the closing, after the last export):

```typescript
// ── User State Sync ─────────────────────────────────────────────────────────

export async function fetchUserState(): Promise<{
  settings: Record<string, unknown>
  preferences: Record<string, unknown>
  conversation_ids: string[]
}> {
  const res = await fetch(`${MCP_BASE}/user-state`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, "Failed to fetch user state"))
  return res.json()
}

export async function fetchSyncedConversations(): Promise<Conversation[]> {
  const res = await fetch(`${MCP_BASE}/user-state/conversations`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, "Failed to fetch conversations"))
  const data = await res.json()
  return data.conversations ?? []
}

export async function syncConversation(conversation: Conversation): Promise<void> {
  await fetch(`${MCP_BASE}/user-state/conversations`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(conversation),
  })
}

export async function syncConversationsBulk(conversations: Conversation[]): Promise<void> {
  await fetch(`${MCP_BASE}/user-state/conversations/bulk`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(conversations),
  })
}

export async function deleteConversationSync(convId: string): Promise<void> {
  await fetch(`${MCP_BASE}/user-state/conversations/${convId}`, {
    method: "DELETE",
    headers: mcpHeaders(),
  })
}

export async function syncPreferences(prefs: Record<string, unknown>): Promise<void> {
  await fetch(`${MCP_BASE}/user-state/preferences`, {
    method: "PATCH",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(prefs),
  })
}
```

Note: `Conversation` type is already imported in `api.ts` via the types import block at the top. Verify it's in the import — if not, add it to the existing import.

**Step 4: Run tests to verify they pass**

```bash
cd src/web && npx vitest run src/__tests__/api-user-state.test.ts
```

Expected: All 6 tests PASS

**Step 5: Commit**

```bash
git add src/web/src/lib/api.ts src/web/src/__tests__/api-user-state.test.ts
git commit -m "feat: add user state sync API client functions"
```

---

### Task 7: Conversation Cloud Sync in useConversations Hook

**Files:**
- Modify: `src/web/src/hooks/use-conversations.ts` (add server sync + startup hydration)
- Test: `src/web/src/__tests__/use-conversations-sync.test.ts`

**Context:** This is the core frontend change. The `useConversations` hook already saves to localStorage. After this task, it also:
1. Syncs conversation saves to the server (fire-and-forget, debounced)
2. Syncs conversation deletes to the server
3. On mount, fetches conversations from the server and merges with localStorage (server data fills gaps, localStorage takes priority for conflicts by `updatedAt`)

**Step 1: Write the failing tests**

File: `src/web/src/__tests__/use-conversations-sync.test.ts`

```typescript
// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"

// Mock the API module
vi.mock("@/lib/api", () => ({
  syncConversation: vi.fn().mockResolvedValue(undefined),
  syncConversationsBulk: vi.fn().mockResolvedValue(undefined),
  deleteConversationSync: vi.fn().mockResolvedValue(undefined),
  fetchSyncedConversations: vi.fn().mockResolvedValue([]),
}))

import { renderHook, act } from "@testing-library/react"
import { useConversations } from "@/hooks/use-conversations"
import * as api from "@/lib/api"

describe("useConversations cloud sync", () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
  })

  it("syncs conversation to server on create", async () => {
    const { result } = renderHook(() => useConversations())

    act(() => {
      result.current.create("openrouter/openai/gpt-4o-mini")
    })

    // Wait for async fire-and-forget
    await vi.waitFor(() => {
      expect(api.syncConversation).toHaveBeenCalledTimes(1)
    })
  })

  it("syncs to server on delete", async () => {
    const { result } = renderHook(() => useConversations())

    let convoId: string
    act(() => {
      convoId = result.current.create("openrouter/openai/gpt-4o-mini")
    })

    act(() => {
      result.current.remove(convoId!)
    })

    await vi.waitFor(() => {
      expect(api.deleteConversationSync).toHaveBeenCalledWith(convoId!)
    })
  })

  it("fetches server conversations on mount", async () => {
    const { result } = renderHook(() => useConversations())

    await vi.waitFor(() => {
      expect(api.fetchSyncedConversations).toHaveBeenCalledTimes(1)
    })
  })

  it("merges server conversations not in localStorage", async () => {
    const serverConvo = {
      id: "server-only",
      title: "From server",
      messages: [],
      model: "openrouter/openai/gpt-4o-mini",
      createdAt: 1710000000000,
      updatedAt: 1710000000000,
    }
    vi.mocked(api.fetchSyncedConversations).mockResolvedValueOnce([serverConvo])

    const { result } = renderHook(() => useConversations())

    await vi.waitFor(() => {
      expect(result.current.conversations.some(c => c.id === "server-only")).toBe(true)
    })
  })
})
```

**Step 2: Run tests to verify they fail**

```bash
cd src/web && npx vitest run src/__tests__/use-conversations-sync.test.ts
```

Expected: FAIL — syncConversation never called (not wired up yet)

**Step 3: Add cloud sync to useConversations**

File: `src/web/src/hooks/use-conversations.ts`

Add imports at the top (after existing imports):
```typescript
import {
  syncConversation,
  syncConversationsBulk,
  deleteConversationSync,
  fetchSyncedConversations,
} from "@/lib/api"
```

Add a debounced server sync helper inside the `useConversations` function, after the `debouncedSave` callback:

```typescript
  // Cloud sync: fire-and-forget server persistence (debounced separately from localStorage)
  const serverSyncTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const pendingServerSyncRef = useRef<Conversation | null>(null)

  const syncToServer = useCallback((convo: Conversation) => {
    pendingServerSyncRef.current = convo
    if (!serverSyncTimerRef.current) {
      serverSyncTimerRef.current = setTimeout(() => {
        serverSyncTimerRef.current = null
        const pending = pendingServerSyncRef.current
        if (pending) {
          pendingServerSyncRef.current = null
          syncConversation(pending).catch(() => { /* fire-and-forget */ })
        }
      }, 2000) // 2s debounce for server sync (heavier than localStorage)
    }
  }, [])
```

Modify the `create` callback to also sync to server — after `saveConversations(next)`, add:
```typescript
      syncConversation(convo).catch(() => { /* fire-and-forget */ })
```

Modify `addMessage` — after `saveConversations(next)`, add:
```typescript
      const updated = next.find((c) => c.id === convoId)
      if (updated) syncToServer(updated)
```

Modify `updateLastMessage` — after `debouncedSave(next)`, add:
```typescript
      const updated = next.find((c) => c.id === convoId)
      if (updated) syncToServer(updated)
```

Modify `remove` — after `saveConversations(next)`, add:
```typescript
      deleteConversationSync(convoId).catch(() => { /* fire-and-forget */ })
```

Modify `saveVerification` — after `saveConversations(next)`, add:
```typescript
      const updated = next.find((c) => c.id === convoId)
      if (updated) syncToServer(updated)
```

Add server hydration effect (after the unmount cleanup effect):

```typescript
  // Hydrate from server on mount — merge server conversations with localStorage
  const serverHydratedRef = useRef(false)
  useEffect(() => {
    if (serverHydratedRef.current) return
    serverHydratedRef.current = true

    fetchSyncedConversations()
      .then((serverConvos) => {
        if (!serverConvos.length) return
        setConversations((local) => {
          const localIds = new Set(local.map((c) => c.id))
          // Add server conversations not present locally
          const newFromServer = serverConvos.filter((sc) => !localIds.has(sc.id))
          if (newFromServer.length === 0) return local

          const merged = [...local, ...newFromServer].slice(0, MAX_CONVERSATIONS)
          saveConversations(merged)
          return merged
        })
      })
      .catch(() => { /* Server unavailable — use localStorage */ })
  }, [])
```

Add cleanup for server sync timer in the unmount effect:
```typescript
  // Flush any pending save on unmount
  useEffect(() => {
    return () => {
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current)
      if (pendingRef.current) saveConversations(pendingRef.current)
      if (serverSyncTimerRef.current) clearTimeout(serverSyncTimerRef.current)
      if (pendingServerSyncRef.current) {
        syncConversation(pendingServerSyncRef.current).catch(() => {})
      }
    }
  }, [])
```

**Step 4: Run tests to verify they pass**

```bash
cd src/web && npx vitest run src/__tests__/use-conversations-sync.test.ts
```

Expected: All 4 tests PASS

**Step 5: Run full frontend test suite**

```bash
cd src/web && npx vitest run
```

Expected: All 440+ tests PASS (no regressions)

**Step 6: Commit**

```bash
git add src/web/src/hooks/use-conversations.ts src/web/src/__tests__/use-conversations-sync.test.ts
git commit -m "feat: add cloud sync to useConversations hook"
```

---

### Task 8: UI Mode + Preferences Cloud Sync

**Files:**
- Modify: `src/web/src/contexts/ui-mode-context.tsx` (sync mode to server)
- Modify: `src/web/src/hooks/use-settings.ts` (sync routing mode + preferences to server)
- Test: `src/web/src/__tests__/ui-mode-sync.test.ts`

**Context:** This task syncs UI preferences (Simple/Advanced mode, onboarding state, routing mode) to the server so they persist across machines. Uses the `syncPreferences()` API from Task 6.

**Step 1: Write the failing tests**

File: `src/web/src/__tests__/ui-mode-sync.test.ts`

```typescript
// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import React from "react"

vi.mock("@/lib/api", () => ({
  syncPreferences: vi.fn().mockResolvedValue(undefined),
  fetchUserState: vi.fn().mockResolvedValue({
    settings: {},
    preferences: {},
    conversation_ids: [],
  }),
  fetchSettings: vi.fn().mockResolvedValue({}),
  updateSettings: vi.fn().mockResolvedValue({}),
}))

import { renderHook, act } from "@testing-library/react"
import { UIModeProvider, useUIMode } from "@/contexts/ui-mode-context"
import * as api from "@/lib/api"

describe("UI Mode cloud sync", () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
  })

  it("syncs mode change to server", async () => {
    const wrapper = ({ children }: { children: React.ReactNode }) =>
      React.createElement(UIModeProvider, null, children)

    const { result } = renderHook(() => useUIMode(), { wrapper })

    act(() => {
      result.current.setMode("advanced")
    })

    await vi.waitFor(() => {
      expect(api.syncPreferences).toHaveBeenCalledWith(
        expect.objectContaining({ ui_mode: "advanced" })
      )
    })
  })

  it("syncs toggle to server", async () => {
    const wrapper = ({ children }: { children: React.ReactNode }) =>
      React.createElement(UIModeProvider, null, children)

    const { result } = renderHook(() => useUIMode(), { wrapper })

    act(() => {
      result.current.toggle()
    })

    await vi.waitFor(() => {
      expect(api.syncPreferences).toHaveBeenCalledTimes(1)
    })
  })
})
```

**Step 2: Run tests to verify they fail**

```bash
cd src/web && npx vitest run src/__tests__/ui-mode-sync.test.ts
```

Expected: FAIL — syncPreferences never called

**Step 3: Add cloud sync to UIModeProvider**

File: `src/web/src/contexts/ui-mode-context.tsx`

Add import at the top:
```typescript
import { syncPreferences } from "@/lib/api"
```

In `setMode`, after the localStorage write, add:
```typescript
    syncPreferences({ ui_mode: m }).catch(() => { /* fire-and-forget */ })
```

In `toggle`, after the localStorage write in the setState callback, add server sync. The updated `toggle` should be:
```typescript
  const toggle = useCallback(() => {
    setModeState((prev) => {
      const next = prev === "simple" ? "advanced" : "simple"
      try { localStorage.setItem("cerid-ui-mode", next) } catch { /* noop */ }
      syncPreferences({ ui_mode: next }).catch(() => { /* fire-and-forget */ })
      return next
    })
  }, [])
```

**Step 4: Add routing mode sync to useSettings**

File: `src/web/src/hooks/use-settings.ts`

Add import at the top:
```typescript
import { syncPreferences } from "@/lib/api"
```

In `setRoutingMode`, after the existing `updateSettings` call, add:
```typescript
    syncPreferences({ routing_mode: mode }).catch(() => { /* fire-and-forget */ })
```

In `cycleRoutingMode`, after the existing `updateSettings` call, add:
```typescript
      syncPreferences({ routing_mode: next }).catch(() => { /* fire-and-forget */ })
```

In `toggleExpertVerification`, add after persist:
```typescript
      syncPreferences({ expert_verification: next }).catch(() => { /* fire-and-forget */ })
```

In `toggleInlineMarkups`, add after persist:
```typescript
      syncPreferences({ inline_markups: next }).catch(() => { /* fire-and-forget */ })
```

Add server hydration for preferences in the existing `useEffect` that hydrates from server. After the `fetchSettings()` block, add:

```typescript
    // Hydrate UI preferences from sync
    import("@/lib/api").then(({ fetchUserState }) => {
      fetchUserState()
        .then((state) => {
          if (!state.preferences) return
          const p = state.preferences as Record<string, unknown>
          // Only hydrate if localStorage has no value
          if (p.routing_mode && !localStorage.getItem("cerid-routing-mode")) {
            const m = p.routing_mode as RoutingMode
            if (m === "manual" || m === "recommend" || m === "auto") {
              setRoutingModeState(m)
              persist("cerid-routing-mode", m)
            }
          }
          if (p.expert_verification !== undefined && !localStorage.getItem("cerid-expert-verification")) {
            const v = Boolean(p.expert_verification)
            setExpertVerificationState(v)
            persist("cerid-expert-verification", String(v))
          }
          if (p.inline_markups !== undefined && !localStorage.getItem("cerid-inline-markups")) {
            const v = Boolean(p.inline_markups)
            setInlineMarkupsState(v)
            persist("cerid-inline-markups", String(v))
          }
        })
        .catch(() => { /* noop */ })
    })
```

**Step 5: Run tests to verify they pass**

```bash
cd src/web && npx vitest run src/__tests__/ui-mode-sync.test.ts
```

Expected: All 2 tests PASS

**Step 6: Run full frontend test suite**

```bash
cd src/web && npx vitest run
```

Expected: All tests PASS

**Step 7: Commit**

```bash
git add src/web/src/contexts/ui-mode-context.tsx src/web/src/hooks/use-settings.ts src/web/src/__tests__/ui-mode-sync.test.ts
git commit -m "feat: sync UI mode and settings preferences to cloud"
```

---

### Task 9: Documentation + Tracking Updates

**Files:**
- Modify: `docs/ISSUES.md` (add resolved issue)
- Modify: `tasks/todo.md` (log completion)
- Modify: `tasks/lessons.md` (add pattern)

**Step 1: Update docs/ISSUES.md**

Add to resolved issues:
```markdown
| 74 | Multi-session cloud sync — conversations, settings, and UI preferences not synced between machines | medium | resolved | 2026-03-14 |
```

**Step 2: Update tasks/todo.md**

Log the sync feature completion.

**Step 3: Update tasks/lessons.md**

Add pattern:
```markdown
- **Use file-based sync for user state, not database**: For self-hosted single-user apps, JSON files in a synced directory (Dropbox) are simpler and more portable than SQLite/Postgres. One file per entity (conversation) avoids merge conflicts. Merge strategy: server fills gaps in localStorage, localStorage wins on conflict.
```

**Step 4: Commit**

```bash
git add docs/ISSUES.md tasks/todo.md tasks/lessons.md
git commit -m "docs: update tracking for multi-session cloud sync feature"
```

---

### Post-Implementation Verification

After all tasks are complete:

1. **Run full Python test suite:**
```bash
docker run --rm -v "$(pwd)/src/mcp:/work" -w /work python:3.11-slim bash -c \
  "pip install -q -r requirements.txt -r requirements-dev.txt && python -m pytest tests/ -v"
```

2. **Run full frontend test suite:**
```bash
cd src/web && npx vitest run
```

3. **Rebuild and smoke test:**
```bash
./scripts/start-cerid.sh --build
./scripts/validate-env.sh
curl http://localhost:8888/user-state
```

4. **Manual verification:**
   - Open GUI → create a conversation → verify `~/Dropbox/cerid-sync/user/conversations/` has a JSON file
   - Change a setting → verify `~/Dropbox/cerid-sync/user/settings.json` updated
   - Restart MCP container → verify settings persist
   - Toggle UI mode → verify `~/Dropbox/cerid-sync/user/state.json` updated
