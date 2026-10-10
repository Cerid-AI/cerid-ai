# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Single-use confirm tokens for forgets requested by MCP clients and products.

A preview issues a token bound to the exact subjects and mode it showed, and
to the caller; executing takes only the token, so what runs is what was
previewed. The token is consumed atomically (GETDEL): it works once, and a
refused attempt (another caller) uses it up too. It expires after 15 minutes.
Redis holds the subject ids, never content.
"""
from __future__ import annotations

import hmac
import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from core.forget.registry import Subject

TTL_SECONDS = 15 * 60
_PREFIX = "cerid:forget:confirm:"
_TOKEN_MAX_LEN = 128


class ConfirmTokenError(Exception):
    """The token is unknown, expired, already used, or issued to another caller."""


@dataclass(frozen=True)
class Confirmed:
    subjects: list[Subject]
    mode: str


def issue(redis: Any, subjects: list[Subject], mode: str, caller: str) -> str:
    token = secrets.token_urlsafe(24)
    value = {"caller": caller, "mode": mode, "subjects": [[s.kind, s.id] for s in subjects]}
    redis.set(f"{_PREFIX}{token}", json.dumps(value), ex=TTL_SECONDS)
    return token


def consume(redis: Any, token: str, caller: str) -> Confirmed:
    if not token or len(token) > _TOKEN_MAX_LEN:
        raise ConfirmTokenError("missing or malformed confirm token")
    raw = redis.getdel(f"{_PREFIX}{token}")
    if not raw:
        raise ConfirmTokenError("the confirm token is unknown, expired or already used")
    try:
        value = json.loads(raw)
        subjects = [Subject(str(k), str(i)) for k, i in value["subjects"]]
        mode = str(value["mode"])
        bound_caller = str(value["caller"])
    except (ValueError, KeyError, TypeError) as exc:
        raise ConfirmTokenError("the confirm token is corrupt") from exc
    if not hmac.compare_digest(bound_caller, caller):
        raise ConfirmTokenError("the confirm token was issued to another caller")
    return Confirmed(subjects=subjects, mode=mode)


def _put_back(redis: Any, token: str, confirmed: Confirmed, caller: str) -> None:
    value = {"caller": caller, "mode": confirmed.mode, "subjects": [[s.kind, s.id] for s in confirmed.subjects]}
    redis.set(f"{_PREFIX}{token}", json.dumps(value), ex=TTL_SECONDS)


def preview_for_token(
    redis: Any, subjects: list[Subject], mode: str, caller: str,
    *, may_forget: Callable[[list[Subject]], None] | None = None,
) -> dict[str, Any]:
    """The selection preview, plus a token for exactly the items it lists.
    Items already forgotten or gone are not listed and not covered; with
    nothing listed there is no token. ``may_forget`` sees everything listed,
    including what the preview added (a memory's earlier versions), and raises
    to refuse before a token exists."""
    from app.services.forget.preview import preview_items
    from app.services.forget.subjects import resolve_passages

    preview = preview_items(resolve_passages(subjects))
    shown = [Subject(i["kind"], i["id"]) for g in preview["groups"] for i in g["items"]]
    if may_forget is not None:
        may_forget(shown)
    token = issue(redis, shown, mode, caller) if shown else ""
    return {**preview, "mode": mode, "confirm_token": token, "expires_in": TTL_SECONDS if token else 0}


def execute(redis: Any, token: str, caller: str, *, requested_by: str, user_id: str = "") -> dict[str, Any]:
    """Run the forget a token was issued for. Raises ConfirmTokenError, and
    the engine's ForgetUnavailable when no sync dir is configured."""
    from app.services.forget import engine

    confirmed = consume(redis, token, caller)
    if not engine.forget_available():
        # Nothing has changed yet, so the token goes back and a retry can use it.
        _put_back(redis, token, confirmed, caller)
        raise engine.ForgetUnavailable("sync dir not configured")
    if confirmed.mode == "permanent":
        receipt = engine.forget_permanently(confirmed.subjects, requested_by=requested_by, user_id=user_id)
        done = all(a.get("status") == "done" for a in receipt["adapters"].values())
        return {"forget_id": receipt["forget_id"], "state": "purged" if done else "trashed_pending",
                "subjects": len(confirmed.subjects)}
    forget_id = engine.trash(confirmed.subjects, requested_by=requested_by, user_id=user_id)
    return {"forget_id": forget_id, "state": "trashed", "subjects": len(confirmed.subjects)}
