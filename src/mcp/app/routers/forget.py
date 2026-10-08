# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Forget engine routes: trash or purge, restore, empty the trash."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.forget import engine
from app.sync.user_state import validate_conversation_id
from core.forget.registry import Subject

router = APIRouter(prefix="/forget", tags=["forget"])

_HTTP_KINDS = frozenset({"conversation"})


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


class RestoreResponse(BaseModel):
    restored: int


class EmptyTrashResponse(BaseModel):
    purged: list[str]


def _subjects(req: ForgetRequest) -> list[Subject]:
    if not req.subjects:
        raise HTTPException(status_code=400, detail="no subjects")
    bad = sorted({s.kind for s in req.subjects} - _HTTP_KINDS)
    if bad:
        raise HTTPException(status_code=400, detail=f"kind not supported yet: {', '.join(bad)}")
    try:
        return [Subject(s.kind, validate_conversation_id(s.id)) for s in req.subjects]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("", response_model=ForgetResponse)
def forget(req: ForgetRequest) -> dict[str, Any]:
    subjects = _subjects(req)
    try:
        if req.mode == "permanent":
            receipt = engine.forget_permanently(subjects, requested_by="api")
            done = all(a["status"] == "done" for a in receipt["adapters"].values())
            return {"forget_id": receipt["forget_id"], "state": "purged" if done else "trashed_pending", "receipt": receipt}
        return {"forget_id": engine.trash(subjects, requested_by="api"), "state": "trashed", "receipt": None}
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
