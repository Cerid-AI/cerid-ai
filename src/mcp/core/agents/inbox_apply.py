# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Pure mail apply plans. No mailbox I/O and no app imports.

The executor turns a plan into sibling calls. Dry-run returns the plan.
Label names and mailbox paths stay symbolic here; id lookup happens at
execution, because that lookup is I/O.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from core.agents.inbox_actions import (
    ACTIONS,
    FLAG_COLOR,
    LABEL_NAME,
    PROVIDER_TOOLS,
)

# Distinct from 77 (Full Disk Access). The helper prints the name on stderr.
APPLE_EXIT_USAGE = 64
APPLE_EXIT_IO = 74
APPLE_EXIT_NOT_RUNNING = 75
APPLE_EXIT_AUTOMATION_DENIED = 78

_BANNED_MAILBOX = re.compile(
    r"(^|/)(junk|junk email|junk e-mail|spam|trash|deleted messages|deleted items)(/|$)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PlannedCall:
    tool: str
    arguments: dict


@dataclass(frozen=True)
class ApplyPlan:
    calls: tuple[PlannedCall, ...] = field(default_factory=tuple)
    move: bool = False
    noop: bool = False
    error: str = ""


def plan_apply(decision: dict) -> ApplyPlan:
    """Map one decision onto the provider allowlist. Unknown actions plan nothing."""
    provider = str(decision.get("provider") or decision.get("source") or "")
    action = str(decision.get("action") or "")
    if provider not in PROVIDER_TOOLS:
        return ApplyPlan(error="provider is not a mail source")
    if action not in ACTIONS:
        return ApplyPlan(error="action is not in the enum")
    category = str(decision.get("category") or "")
    if category not in LABEL_NAME:
        return ApplyPlan(error="category is not in the enum")
    message_ids = _message_ids(decision)
    if not message_ids:
        return ApplyPlan(error="message id is required")
    if provider == "gmail":
        return _gmail(decision, message_ids)
    if provider == "outlook":
        return _outlook(decision, message_ids)
    return _apple(decision, message_ids)


def _message_ids(decision: dict) -> list[str]:
    raw = decision.get("message_ids")
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = [raw]
        raw = parsed
    ids: list[str] = []
    if isinstance(raw, list):
        ids = [str(item).strip() for item in raw if str(item).strip()]
    if ids:
        return ids
    one = str(decision.get("provider_message_id") or "").strip()
    return [one] if one else []


def _current_labels(decision: dict) -> list[str]:
    """Labels we believe are on the message. Absent means still in the inbox."""
    if "current_labels" not in decision:
        return ["INBOX", "UNREAD"]
    raw = decision.get("current_labels") or []
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw]


def _gmail_delta(
    action: str,
    category: str,
    folder_sort: bool,
    current_labels: list[str],
) -> tuple[list[str], list[str]] | None:
    """Label names to add and remove. None when the mailbox already matches."""
    current = set(current_labels)
    label = LABEL_NAME[category]
    cerid = {name for name in current if name.startswith("Cerid/")}
    add: set[str] = set()
    remove: set[str] = set()
    if action == "archive":
        add.add(label)
        remove.add("INBOX")
        remove.update(name for name in cerid if name != label)
    elif action == "keep":
        add.add(label)
        remove.update(name for name in cerid if name != label)
        if folder_sort:
            remove.add("INBOX")
    elif action == "mark_read":
        remove.add("UNREAD")
    elif action == "undo":
        add.add("INBOX")
        remove.update(cerid or {label})
    else:
        return None
    add -= current
    remove &= current
    if not add and not remove:
        return None
    return sorted(add), sorted(remove)


def _gmail(decision: dict, message_ids: list[str]) -> ApplyPlan:
    account = str(decision.get("account") or "")
    action = str(decision["action"])
    category = str(decision["category"])
    if action == "draft":
        body = str(decision.get("draft_body") or "").strip()
        thread_id = str(decision.get("provider_thread_id") or "").strip()
        if not body:
            return ApplyPlan(error="draft body is missing")
        if not thread_id:
            return ApplyPlan(error="thread id is required")
        call = PlannedCall(
            "draft_gmail_message",
            {
                "user_google_email": account,
                "subject": str(decision.get("subject") or "(no subject)"),
                "body": body,
                "thread_id": thread_id,
                "include_signature": False,
                "quote_original": False,
            },
        )
        return ApplyPlan((call,), False)
    delta = _gmail_delta(action, category, bool(decision.get("folder_sort")), _current_labels(decision))
    if delta is None:
        return ApplyPlan(noop=True)
    add, remove = delta
    arguments: dict = {"user_google_email": account, "message_ids": message_ids}
    if add:
        arguments["add_label_ids"] = add
    if remove:
        arguments["remove_label_ids"] = remove
    relocates = "INBOX" in add or "INBOX" in remove
    return ApplyPlan((PlannedCall("batch_modify_gmail_message_labels", arguments),), relocates)


def _outlook(decision: dict, message_ids: list[str]) -> ApplyPlan:
    action = str(decision["action"])
    label = LABEL_NAME[str(decision["category"])]
    folder_sort = bool(decision.get("folder_sort"))
    if action == "draft":
        body = str(decision.get("draft_body") or "").strip()
        if not body:
            return ApplyPlan(error="draft body is missing")
        call = PlannedCall(
            "create-reply-draft",
            {"messageId": message_ids[0], "body": {"comment": body}},
        )
        return ApplyPlan((call,), False)
    calls: list[PlannedCall] = []
    for message_id in message_ids:
        if action in ("keep", "archive"):
            calls.append(PlannedCall(
                "update-mail-message",
                {"messageId": message_id, "body": {"categories": [label]}},
            ))
        elif action == "mark_read":
            calls.append(PlannedCall(
                "update-mail-message",
                {"messageId": message_id, "body": {"isRead": True}},
            ))
        elif action == "undo":
            # Clear before the move. Graph move returns a new message id, so a
            # category patch after the move would miss the message.
            calls.append(PlannedCall(
                "update-mail-message",
                {"messageId": message_id, "body": {"categories": []}},
            ))
            calls.append(_outlook_move(message_id, "inbox"))
        if action == "keep" and folder_sort:
            calls.append(_outlook_move(message_id, label))
        elif action == "archive":
            calls.append(_outlook_move(message_id, "archive"))
    move = any(call.tool == "move-mail-message" for call in calls)
    return ApplyPlan(tuple(calls), move)


def _outlook_move(message_id: str, destination: str) -> PlannedCall:
    return PlannedCall(
        "move-mail-message",
        {"messageId": message_id, "body": {"destinationId": destination}},
    )


def _apple(decision: dict, message_ids: list[str]) -> ApplyPlan:
    action = str(decision["action"])
    category = str(decision["category"])
    folder_sort = bool(decision.get("folder_sort"))
    subject = str(decision.get("subject") or "(no subject)")
    body = str(decision.get("draft_body") or "")
    mailbox = str(decision.get("mailbox_before") or decision.get("mailbox") or "INBOX")
    if action == "undo" and _banned(mailbox):
        return ApplyPlan(error="junk and trash are not destinations")
    if action == "draft" and not body.strip():
        return ApplyPlan(error="draft body is missing")
    calls: list[PlannedCall] = []
    for message_id in message_ids:
        if action == "keep":
            calls.append(PlannedCall("flag", {"argv": ["flag", message_id, FLAG_COLOR[category]]}))
            if folder_sort:
                dest = LABEL_NAME[category]
                if _banned(dest):
                    return ApplyPlan(error="junk and trash are not destinations")
                calls.append(PlannedCall("move", {"argv": ["move", message_id, dest]}))
        elif action == "archive":
            calls.append(PlannedCall("move", {"argv": ["move", message_id, "Archive"]}))
        elif action == "mark_read":
            calls.append(PlannedCall("read", {"argv": ["read", message_id]}))
        elif action == "draft":
            calls.append(PlannedCall("draft", {"argv": ["draft", message_id, subject, body]}))
        elif action == "undo":
            calls.append(PlannedCall("move", {"argv": ["move", message_id, mailbox]}))
    move = any(call.tool == "move" for call in calls)
    return ApplyPlan(tuple(calls), move)


def _banned(mailbox: str) -> bool:
    """Provider Junk, Spam, and Trash. Cerid/Spam is a sort folder, not Junk."""
    text = mailbox.strip()
    if text.casefold().startswith("cerid/"):
        return False
    return bool(_BANNED_MAILBOX.search(text))
