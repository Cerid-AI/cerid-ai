# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""User state API — settings, conversations, and UI preferences via sync directory."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import config
from app.services.private_mode import private_blocks
from app.sync.user_state import (
    delete_conversation,
    read_conversation,
    read_conversations,
    read_preferences,
    read_settings,
    write_conversation,
    write_preferences_with_retry,
)


# --- Response models (generated: single-return dict-literal routes) ---
class RemoveConversationResponse(BaseModel):
    deleted: Any


class SavePreferencesResponse(BaseModel):
    ok: bool


class SaveConversationResponse(BaseModel):
    saved: Any


class SaveConversationsBulkResponse(BaseModel):
    saved: Any



router = APIRouter(prefix="/user-state", tags=["user-state"])
logger = logging.getLogger("ai-companion.user_state")


def _sync_dir() -> str:
    """Return the configured sync directory. Extracted for test patching."""
    return config.SYNC_DIR


@router.get("", response_model=dict[str, Any])
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


@router.get("/conversations")  # response-model-allowed: dynamic response (shape varies)
def list_conversations():
    """List all synced conversations."""
    sd = _sync_dir()
    if not sd:
        return []
    return read_conversations(sd)


@router.get("/conversations/{conv_id}")  # response-model-allowed: dynamic response (shape varies)
def get_conversation(conv_id: str):
    """Return a single conversation by ID."""
    sd = _sync_dir()
    if not sd:
        raise HTTPException(status_code=404, detail="Conversation not found")
    data = read_conversation(sd, conv_id)
    if not data:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return data


@router.post("/conversations", response_model=SaveConversationResponse)
def save_conversation(body: dict[str, Any]):
    """Save a single conversation. Body must contain an 'id' field."""
    sd = _sync_dir()
    if not sd:
        raise HTTPException(status_code=412, detail="Sync directory not configured")
    if "id" not in body:
        raise HTTPException(status_code=400, detail="Conversation must have an 'id' field")
    if private_blocks(1):
        # response_model=SaveConversationResponse only declares `saved`, so
        # any extra key here would be silently stripped on the wire — the
        # None value alone is the skip signal (mirrors the bulk endpoint).
        return {"saved": None}
    write_conversation(sd, body)
    return {"saved": body["id"]}


@router.post("/conversations/bulk", response_model=SaveConversationsBulkResponse)
def save_conversations_bulk(body: list[dict[str, Any]]):
    """Save multiple conversations. Each dict must contain an 'id' field."""
    sd = _sync_dir()
    if not sd:
        raise HTTPException(status_code=412, detail="Sync directory not configured")
    for conv in body:
        if "id" not in conv:
            raise HTTPException(status_code=400, detail="Each conversation must have an 'id' field")
    if private_blocks(1):
        return {"saved": []}
    for conv in body:
        write_conversation(sd, conv)
    return {"saved": len(body)}


@router.delete("/conversations/{conv_id}", response_model=RemoveConversationResponse)
def remove_conversation(conv_id: str):
    """Delete a conversation by ID."""
    sd = _sync_dir()
    if not sd:
        raise HTTPException(status_code=412, detail="Sync directory not configured")
    delete_conversation(sd, conv_id)
    return {"deleted": conv_id}


@router.patch("/preferences", response_model=SavePreferencesResponse)
async def save_preferences(body: dict[str, Any]):
    """Merge UI preferences into the stored state.

    Runs the write through :func:`write_preferences_with_retry` so macOS
    Dropbox lock collisions (EDEADLK) recover transparently. After the
    retry budget exhausts, returns 503 with a user-readable message
    rather than a generic 500.
    """
    sd = _sync_dir()
    if not sd:
        raise HTTPException(status_code=412, detail="Sync directory not configured")
    ok = await write_preferences_with_retry(sd, body)
    if not ok:
        raise HTTPException(
            status_code=503,
            detail=(
                "UI preferences were not saved to cloud sync — another "
                "process (likely Dropbox) held the file lock. Retry in a "
                "moment or pause Dropbox briefly."
            ),
        )
    return {"ok": True}
