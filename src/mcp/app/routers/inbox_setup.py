# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Sources page for inbox accounts, the review queue, and apply/undo.

The mailbox work lives in app.inbox.review. These routes are the HTTP
shape the Companion web app already uses. pkb_inbox_apply and
pkb_inbox_undo call the same functions. The feature gate runs before
the ledger is opened.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/inbox", tags=["inbox"])


class AccountBody(BaseModel):
    provider: str
    address: str
    display_name: str = ""


class AccountPatch(BaseModel):
    provider: str
    address: str
    display_name: str | None = None
    included: bool | None = None
    folder_sort: bool | None = None
    auto_apply: list[str] | None = None
    utilities: list[str] | None = None


class RemoveBody(BaseModel):
    provider: str
    address: str


class ApplyBody(BaseModel):
    decision_ids: list[str] | str = []
    dry_run: bool = True


class UndoBody(BaseModel):
    decision_id: str
    dry_run: bool = True


class SkipBody(BaseModel):
    decision_id: str


def _gate() -> None:
    from config.features import is_feature_enabled

    if not is_feature_enabled("inbox_triage"):
        raise HTTPException(status_code=403, detail="feature_gated")


def get_ledger():
    from app.inbox.review import open_ledger

    return open_ledger()


def _fields(body: AccountPatch) -> dict:
    return {
        key: value
        for key, value in body.model_dump().items()
        if key not in ("provider", "address") and value is not None
    }


@router.get("/setup", response_model=dict[str, Any])
async def get_setup(provider: str = "") -> dict:
    _gate()
    from app.inbox.review import setup_view

    return setup_view(get_ledger(), provider)


@router.post("/accounts", response_model=dict[str, Any])
async def post_account(body: AccountBody) -> dict:
    _gate()
    from app.inbox.review import add_account

    try:
        return add_account(
            get_ledger(),
            provider=body.provider,
            address=body.address,
            display_name=body.display_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/accounts", response_model=dict[str, Any])
async def patch_account(body: AccountPatch) -> dict:
    _gate()
    from app.inbox.review import change_account

    fields = _fields(body)
    if not fields:
        raise HTTPException(status_code=400, detail="no fields")
    try:
        account = change_account(get_ledger(), body.provider, body.address, **fields)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if account is None:
        raise HTTPException(status_code=404, detail="account is not connected")
    return account


@router.post("/accounts/remove", response_model=dict[str, Any])
async def remove_account(body: RemoveBody) -> dict:
    _gate()
    ledger = get_ledger()
    if not ledger.remove_account(body.provider, body.address):
        raise HTTPException(status_code=404, detail="account is not connected")
    account = ledger.get_account(body.provider, body.address)
    return account or {"removed": True}


@router.post("/apply", response_model=dict[str, Any])
async def post_apply(body: ApplyBody) -> dict:
    _gate()
    from app.inbox.review import apply_ids

    return await apply_ids(body.decision_ids, dry_run=body.dry_run, ledger=get_ledger())


@router.post("/undo", response_model=dict[str, Any])
async def post_undo(body: UndoBody) -> dict:
    _gate()
    from app.inbox.review import undo_decision

    return await undo_decision(body.decision_id, dry_run=body.dry_run, ledger=get_ledger())


@router.post("/skip", response_model=dict[str, Any])
async def post_skip(body: SkipBody) -> dict:
    _gate()
    from app.inbox.review import skip_decision

    return skip_decision(get_ledger(), body.decision_id)


@router.get("/discovered", response_model=dict[str, Any])
async def get_discovered(discover: bool = False) -> dict:
    _gate()
    from app.inbox.review import discovered_addresses, scan_apple_addresses

    found = discovered_addresses()
    if discover:
        scanned = await scan_apple_addresses()
        found["apple_mail"] = scanned["addresses"]
        found["error"] = scanned["error"]
    return found
