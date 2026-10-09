# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Forget engine routes: trash or purge, restore, empty the trash."""
from __future__ import annotations

import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

import config
from app.services.forget import engine
from app.services.forget.preview import preview_conversation
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

_HTTP_KINDS = frozenset({"conversation", "artifact", "memory"})
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


class SubjectIn(BaseModel):
    kind: str
    id: str


class ForgetRequest(BaseModel):
    subjects: list[SubjectIn]
    mode: Literal["trash", "permanent"] = "trash"


class ForgetResponse(BaseModel):
    forget_id: str
    state: str
    receipt: dict[str, Any] | None = None


class ForgetPreviewRequest(BaseModel):
    kind: Literal["conversation"]
    id: str


class ForgetPreviewGroup(BaseModel):
    key: str
    default: Literal["always", "checked", "unchecked"]
    items: list[dict[str, Any]]


class ForgetPreviewResponse(BaseModel):
    subject: dict[str, str]
    title: str
    groups: list[ForgetPreviewGroup]
    derived_facts: int
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


def _subject(kind: str, raw: str) -> Subject:
    if kind == "conversation":
        return Subject(kind, _conversation_id(raw))
    if not _ID_RE.match(raw or ""):
        raise HTTPException(status_code=400, detail=f"Invalid {kind} id: {raw!r}")
    return Subject(kind, raw)


def _subjects(req: ForgetRequest) -> list[Subject]:
    if not req.subjects:
        raise HTTPException(status_code=400, detail="no subjects")
    bad = sorted({s.kind for s in req.subjects} - _HTTP_KINDS)
    if bad:
        raise HTTPException(status_code=400, detail=f"kind not supported: {', '.join(bad)}")
    return [_subject(s.kind, s.id) for s in req.subjects]


def _user_id(request: Request) -> str:
    return str(getattr(request.state, "user_id", "") or "")


@router.post("/preview", response_model=ForgetPreviewResponse)
def preview(req: ForgetPreviewRequest) -> dict[str, Any]:
    return preview_conversation(_conversation_id(req.id))


@router.post("", response_model=ForgetResponse)
def forget(req: ForgetRequest, request: Request) -> dict[str, Any]:
    subjects = _subjects(req)
    user_id = _user_id(request)
    try:
        if req.mode == "permanent":
            receipt = engine.forget_permanently(subjects, requested_by="api", user_id=user_id)
            done = all(a["status"] == "done" for a in receipt["adapters"].values())
            return {"forget_id": receipt["forget_id"], "state": "purged" if done else "trashed_pending", "receipt": receipt}
        forget_id = engine.trash(subjects, requested_by="api", user_id=user_id)
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
