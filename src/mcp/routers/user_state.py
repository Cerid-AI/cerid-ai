# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""User state API — settings, conversations, and UI preferences via sync directory."""

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
    """Return the configured sync directory. Extracted for test patching."""
    return config.SYNC_DIR


@router.get("")
def get_user_state_summary():
    """Return a summary of user state: settings, preferences, conversation IDs."""
    sd = _sync_dir()
    if not sd:
        return {"settings": {}, "preferences": {}, "conversation_ids": []}
    settings = read_settings(sd)
    preferences = read_preferences(sd)
    conversations = read_conversations(sd)
    return {
        "settings": settings,
        "preferences": preferences,
        "conversation_ids": [c.get("id") for c in conversations if c.get("id")],
    }


@router.get("/conversations")
def list_conversations():
    """List all synced conversations."""
    sd = _sync_dir()
    if not sd:
        return []
    return read_conversations(sd)


@router.get("/conversations/{conv_id}")
def get_conversation(conv_id: str):
    """Return a single conversation by ID."""
    sd = _sync_dir()
    if not sd:
        raise HTTPException(status_code=404, detail="Conversation not found")
    data = read_conversation(sd, conv_id)
    if not data:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return data


@router.post("/conversations")
def save_conversation(body: dict[str, Any]):
    """Save a single conversation. Body must contain an 'id' field."""
    sd = _sync_dir()
    if not sd:
        raise HTTPException(status_code=503, detail="Sync directory not configured")
    if "id" not in body:
        raise HTTPException(status_code=400, detail="Conversation must have an 'id' field")
    write_conversation(sd, body)
    return {"saved": body["id"]}


@router.post("/conversations/bulk")
def save_conversations_bulk(body: list[dict[str, Any]]):
    """Save multiple conversations. Each dict must contain an 'id' field."""
    sd = _sync_dir()
    if not sd:
        raise HTTPException(status_code=503, detail="Sync directory not configured")
    for conv in body:
        if "id" not in conv:
            raise HTTPException(status_code=400, detail="Each conversation must have an 'id' field")
        write_conversation(sd, conv)
    return {"saved": len(body)}


@router.delete("/conversations/{conv_id}")
def remove_conversation(conv_id: str):
    """Delete a conversation by ID."""
    sd = _sync_dir()
    if not sd:
        raise HTTPException(status_code=503, detail="Sync directory not configured")
    delete_conversation(sd, conv_id)
    return {"deleted": conv_id}


@router.patch("/preferences")
def save_preferences(body: dict[str, Any]):
    """Merge UI preferences into the stored state."""
    sd = _sync_dir()
    if not sd:
        raise HTTPException(status_code=503, detail="Sync directory not configured")
    write_preferences(sd, body)
    return {"ok": True}
