# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Forget engine routes: trash or purge, restore, empty the trash."""
from __future__ import annotations

import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

import config
from app.services.forget import engine
from app.services.forget.preview import preview_conversation, preview_items
from app.services.forget.subjects import KINDS, parse_subjects, resolve_passages
from app.sync.user_state import validate_conversation_id
from core.forget.registry import Subject


def _is_admin(request: Request) -> bool:
    return bool(getattr(request.state, "is_admin", False)) or getattr(request.state, "role", "") == "admin"


def _require_admin_in_multi_user(request: Request) -> None:
    """Multi-user mode: conversations carry no owner, so a member cannot be
    limited to their own data, and artifacts and memories are shared. Every
    forget route is therefore an admin action there. Single-user mode has one
    key holder, who owns everything."""
    if config.CERID_MULTI_USER and not _is_admin(request):
        raise HTTPException(status_code=403, detail="Forgetting needs an admin in multi-user mode")


router = APIRouter(prefix="/forget", tags=["forget"], dependencies=[Depends(_require_admin_in_multi_user)])

_PREVIEW_MAX = 200


class SubjectIn(BaseModel):
    kind: str
    id: str


class ForgetRequest(BaseModel):
    subjects: list[SubjectIn]
    mode: Literal["trash", "permanent"] = "trash"
    # Recorded as the forget's requester: the dialogs, or the forget assistant.
    source: Literal["api", "agent"] = "api"


class ForgetResponse(BaseModel):
    forget_id: str
    state: str
    receipt: dict[str, Any] | None = None


class ForgetPreviewRequest(BaseModel):
    """Either one conversation (``kind`` + ``id``) or a selection of documents,
    passages and memories (``subjects``)."""

    kind: Literal["conversation"] | None = None
    id: str | None = None
    subjects: list[SubjectIn] | None = Field(default=None, max_length=_PREVIEW_MAX)

    @model_validator(mode="after")
    def _one_form(self) -> ForgetPreviewRequest:
        if (self.kind is not None) == (self.subjects is not None):
            raise ValueError("send either kind and id, or subjects")
        if self.kind is not None and not self.id:
            raise ValueError("id is required with kind")
        return self


class ForgetPreviewGroup(BaseModel):
    key: str
    default: Literal["always", "checked", "unchecked"]
    items: list[dict[str, Any]]


class ForgetPreviewResponse(BaseModel):
    subject: dict[str, str] | None
    title: str
    groups: list[ForgetPreviewGroup]
    derived_facts: int
    notes: list[str] = []
    out_of_reach: list[str]


class RestoreResponse(BaseModel):
    restored: int


class EmptyTrashResponse(BaseModel):
    purged: list[str]


class TrashSubject(BaseModel):
    kind: str
    id: str
    label: str


class TrashGroup(BaseModel):
    forget_id: str
    at: str
    requested_by: str
    purge_started: bool
    subjects: list[TrashSubject]


class TrashResponse(BaseModel):
    items: list[TrashGroup]


class ReceiptSummary(BaseModel):
    forget_id: str
    at: str
    requested_by: str
    subjects: dict[str, int]
    status: Literal["done", "pending"]


class ReceiptsResponse(BaseModel):
    items: list[ReceiptSummary]


_FORGET_ID_RE = re.compile(r"^fg_[0-9a-f]{16}$")


def _conversation_id(raw: str) -> str:
    try:
        return validate_conversation_id(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _subjects(items: list[SubjectIn], kinds: frozenset[str] = KINDS) -> list[Subject]:
    try:
        return parse_subjects(items, kinds)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _user_id(request: Request) -> str:
    return str(getattr(request.state, "user_id", "") or "")


class AssistRequest(BaseModel):
    scope: str = Field(min_length=2, max_length=300)
    allow_cloud: bool = False


class AssistGroup(BaseModel):
    title: str
    explanation: str
    items: list[dict[str, Any]]


class AssistResponse(BaseModel):
    status: Literal["grouped", "needs_consent", "ungrouped"]
    model: Literal["local", "cloud"] | None
    reason: str
    cloud_model: str
    scope: str
    total: int
    groups: list[AssistGroup]


@router.post("/assist/search", response_model=AssistResponse)
async def assist_search(req: AssistRequest) -> dict[str, Any]:
    """Everything matching a plain-language scope, grouped by the local model.
    ``allow_cloud`` is the user's consent to group on the cloud model when the
    local one is unavailable; nothing is forgotten here."""
    from app.services.forget.assist import assist
    return await assist(req.scope, allow_cloud=req.allow_cloud)


@router.post("/preview", response_model=ForgetPreviewResponse)
def preview(req: ForgetPreviewRequest) -> dict[str, Any]:
    if req.subjects is not None:
        return preview_items(_subjects(req.subjects))
    return preview_conversation(_conversation_id(req.id or ""))


@router.post("", response_model=ForgetResponse)
def forget(req: ForgetRequest, request: Request) -> dict[str, Any]:
    subjects = resolve_passages(_subjects(req.subjects))
    user_id = _user_id(request)
    try:
        if req.mode == "permanent":
            receipt = engine.forget_permanently(subjects, requested_by=req.source, user_id=user_id)
            done = all(a["status"] == "done" for a in receipt["adapters"].values())
            return {"forget_id": receipt["forget_id"], "state": "purged" if done else "trashed_pending", "receipt": receipt}
        forget_id = engine.trash(subjects, requested_by=req.source, user_id=user_id)
        return {"forget_id": forget_id, "state": "trashed", "receipt": None}
    except engine.ForgetUnavailable as exc:
        raise HTTPException(status_code=412, detail=str(exc)) from exc


@router.post("/{forget_id}/restore", response_model=RestoreResponse)
def restore(forget_id: str) -> dict[str, int]:
    try:
        return {"restored": engine.restore(forget_id)}
    except engine.ForgetConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except engine.ForgetUnavailable as exc:
        raise HTTPException(status_code=412, detail=str(exc)) from exc


@router.post("/trash/empty", response_model=EmptyTrashResponse)
def empty_trash() -> dict[str, list[str]]:
    try:
        return {"purged": engine.empty_trash()}
    except engine.ForgetUnavailable as exc:
        raise HTTPException(status_code=412, detail=str(exc)) from exc


@router.get("/trash", response_model=TrashResponse)
def trash() -> dict[str, Any]:
    try:
        return {"items": engine.list_trash()}
    except engine.ForgetUnavailable as exc:
        raise HTTPException(status_code=412, detail=str(exc)) from exc


@router.get("/receipts", response_model=ReceiptsResponse)
def receipts() -> dict[str, Any]:
    return {"items": engine.list_receipts()}


@router.get("/receipts/{forget_id}", response_model=dict[str, Any])
def receipt(forget_id: str) -> dict[str, Any]:
    if not _FORGET_ID_RE.match(forget_id):
        raise HTTPException(status_code=400, detail="invalid forget id")
    found = engine.read_receipt(forget_id)
    if not found:
        raise HTTPException(status_code=404, detail="no receipt for that forget")
    return found
