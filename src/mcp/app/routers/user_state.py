# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""User state API — settings, conversations, and UI preferences via sync directory."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import config
from app.services.forget import engine as forget_engine
from app.services.private_mode import private_blocks
from app.sync.user_state import (
    list_conversation_ids,
    read_conversation,
    read_conversations,
    read_preferences,
    read_settings,
    validate_conversation_id,
    write_conversation,
    write_preferences_with_retry,
)
from core.forget.registry import Subject, get_registry


# --- Response models (generated: single-return dict-literal routes) ---
class RemoveConversationResponse(BaseModel):
    deleted: Any
    forget_id: str | None = None
    state: str | None = None


class SavePreferencesResponse(BaseModel):
    ok: bool


class SaveConversationResponse(BaseModel):
    saved: Any


class SaveConversationsBulkResponse(BaseModel):
    saved: Any
    gone: list[str] = []


class ForgottenItem(BaseModel):
    kind: str
    id: str
    state: str
    at: str
    forget_id: str


class ForgottenResponse(BaseModel):
    items: list[ForgottenItem]
    cursor: str | None = None


router = APIRouter(prefix="/user-state", tags=["user-state"])
logger = logging.getLogger("ai-companion.user_state")


def _sync_dir() -> str:
    """Return the configured sync directory. Extracted for test patching."""
    return config.SYNC_DIR


def _checked_conv_id(conv_id: str) -> str:
    """Reject ids that are not safe as a filename, before any path is built."""
    try:
        return validate_conversation_id(conv_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _forgotten(conv_id: str) -> bool:
    return get_registry().is_forgotten("conversation", conv_id)


@router.get("", response_model=dict[str, Any])
def get_user_state_summary():
    """Return a summary of user state: settings, preferences, conversation IDs."""
    sd = _sync_dir()
    if not sd:
        return {"settings": {}, "preferences": {}, "conversation_ids": []}
    settings = read_settings(sd)
    preferences = read_preferences(sd)
    # ids are the filename stems — no need to decrypt every conversation (CR-058).
    return {
        "settings": settings,
        "preferences": preferences,
        "conversation_ids": [cid for cid in list_conversation_ids(sd) if not _forgotten(cid)],
    }


@router.get("/conversations")  # response-model-allowed: dynamic response (shape varies)
def list_conversations():
    """List all synced conversations."""
    sd = _sync_dir()
    if not sd:
        return []
    return [c for c in read_conversations(sd) if not _forgotten(str(c.get("id", "")))]


@router.get("/conversations/{conv_id}")  # response-model-allowed: dynamic response (shape varies)
def get_conversation(conv_id: str):
    """Return a single conversation by ID."""
    sd = _sync_dir()
    if not sd:
        raise HTTPException(status_code=404, detail="Conversation not found")
    conv_id = _checked_conv_id(conv_id)
    if _forgotten(conv_id):
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
    _checked_conv_id(body["id"])
    if _forgotten(body["id"]):
        raise HTTPException(status_code=410, detail="conversation forgotten")
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
        _checked_conv_id(conv["id"])
    if private_blocks(1):
        return {"saved": [], "gone": []}
    gone = [c["id"] for c in body if _forgotten(c["id"])]
    skip = set(gone)
    rest = [c for c in body if c["id"] not in skip]
    for conv in rest:
        write_conversation(sd, conv)
    return {"saved": len(rest), "gone": gone}


@router.delete("/conversations/{conv_id}", response_model=RemoveConversationResponse)
def remove_conversation(conv_id: str, permanent: bool = False):
    """Forget a conversation: Move to Trash by default, ``?permanent=true`` to purge now.

    Allowed at every private-mode level: forgetting only reduces data.
    """
    sd = _sync_dir()
    if not sd:
        raise HTTPException(status_code=412, detail="Sync directory not configured")
    conv_id = _checked_conv_id(conv_id)
    subjects = [Subject("conversation", conv_id)]
    try:
        if permanent:
            receipt = forget_engine.forget_permanently(subjects, requested_by="conversation_delete")
            done = all(a["status"] == "done" for a in receipt["adapters"].values())
            return {"deleted": conv_id, "forget_id": receipt["forget_id"], "state": "purged" if done else "trashed_pending"}
        forget_id = forget_engine.trash(subjects, requested_by="conversation_delete")
    except forget_engine.ForgetUnavailable as exc:
        raise HTTPException(status_code=412, detail=str(exc)) from exc
    return {"deleted": conv_id, "forget_id": forget_id, "state": "trashed"}


@router.get("/forgotten", response_model=ForgottenResponse)
def list_forgotten(since: str | None = None):
    """Forgotten conversations, newest last; clients drop their local copies."""
    rows = [e for e in get_registry().latest(since=since) if e.subject.kind == "conversation"]
    items = [
        {"kind": e.subject.kind, "id": e.subject.id, "state": e.state, "at": e.at, "forget_id": e.forget_id}
        for e in rows
    ]
    return {"items": items, "cursor": rows[-1].at if rows else since}


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
