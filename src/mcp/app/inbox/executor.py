# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Apply a closed mail action. Dry-run is the default and touches nothing.

A live write requires CERID_INBOX_ACTIONS_ENABLED. The sibling containers
stay on their read-only command until that same variable is set there, so
the flag in this process is not by itself a mailbox credential.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Protocol

from app.inbox.hooks import allowlist_hook, run_hooks
from core.agents.inbox_actions import FORBIDDEN_TOOLS, PROVIDER_TOOLS
from core.agents.inbox_apply import (
    APPLE_EXIT_AUTOMATION_DENIED,
    APPLE_EXIT_NOT_RUNNING,
    ApplyPlan,
    plan_apply,
)

_LABEL_LINE = re.compile(r"•\s+(.+?)\s+\(ID:\s*([^)]+)\)")
_CREATED_NAME = re.compile(r"Name:\s*(.+)")
_CREATED_ID = re.compile(r"ID:\s*(\S+)")
_WELL_KNOWN_OUTLOOK = {"inbox", "archive", "drafts", "sentitems"}
_REFUSED_OUTLOOK = {"deleteditems", "junkemail"}
_MOVE_ARGC = 3
_PAYLOAD_KIND = {
    "none": "none",
    "correspondence": "excerpt",
    "financial": "card",
}


class InboxTransport(Protocol):
    async def call_tool(self, provider: str, tool: str, arguments: dict) -> str: ...

    async def apple(self, argv: list[str]) -> tuple[int, dict]: ...


def inbox_actions_enabled() -> bool:
    """Call-time gate. The settings constant is the import-time snapshot."""
    raw = os.getenv("CERID_INBOX_ACTIONS_ENABLED", "false")
    return raw.strip().lower() in ("true", "1")


async def apply_decision(
    decision: dict,
    *,
    dry_run: bool = True,
    ledger: Any = None,
    transport: InboxTransport | None = None,
    actions_enabled: bool | None = None,
) -> dict:
    """Plan one decision and, only when asked, perform it.

    ``dry_run`` defaults true. A false dry-run still performs nothing while
    the actions flag is off. Neither path starts Mail or a sibling.
    """
    decision = _prepare(decision, ledger)
    provider = str(decision.get("provider") or decision.get("source") or "")
    enabled = inbox_actions_enabled() if actions_enabled is None else actions_enabled

    if not dry_run and not enabled:
        # The flag is the first check on a live apply: the plan is reported
        # and neither the ledger nor the mailbox is touched.
        preview = await apply_decision(
            decision, dry_run=True, ledger=ledger, transport=transport, actions_enabled=enabled,
        )
        return _result(False, "disabled", calls=preview["calls"], reason="actions flag is off")

    if _pending_account(decision):
        return _result(False, "skipped", reason="pending_account")

    if decision.get("in_inbox") is False:
        if not dry_run:
            _store_cursor(ledger, decision)
            _record(ledger, decision, "dropped", {"dropped": "left_inbox"})
        return _result(True, "dropped", reason="left_inbox")

    plan = plan_apply(decision)
    if plan.error:
        if not dry_run:
            _record(ledger, decision, "failed", {"error": plan.error})
        return _result(False, "failed", reason=plan.error)

    refused = _refused_tool(provider, plan)
    if refused:
        return _result(False, "failed", reason=refused)

    hooked = _hook_decision(decision, plan)
    for call in plan.calls:
        failed = allowlist_hook({**hooked, "tool_name": call.tool, "provider": provider})
        if failed is not None and not failed.ok:
            return _result(False, "failed", reason=failed.reason, hook=failed.hook)
    checked = run_hooks(hooked)
    if not checked.ok:
        return _result(False, "failed", reason=checked.reason, hook=checked.hook)

    planned = [_call_dict(call.tool, call.arguments) for call in plan.calls]
    if plan.noop:
        return _result(True, "noop", calls=planned)
    if dry_run:
        return _result(True, "dry_run", calls=planned)

    active = transport if transport is not None else _default_transport()
    try:
        if provider == "apple_mail":
            performed, status, reason = await _perform_apple(decision, plan, active, ledger)
        elif provider == "gmail":
            performed = await _perform_gmail(plan, active)
            status, reason = "applied", ""
        else:
            performed = await _perform_outlook(plan, active)
            status, reason = "applied", ""
    except _ApplyError as exc:
        _record(ledger, decision, "failed", {"error": exc.reason, "calls": exc.calls})
        return _result(False, "failed", calls=exc.calls, reason=exc.reason)
    if status == "queued":
        return _result(True, "queued", calls=performed, reason=reason)
    _store_cursor(ledger, decision)
    receipt: dict[str, Any] = {"calls": performed}
    if plan.labels_after is not None:
        receipt["current_labels"] = list(plan.labels_after)
    if provider == "outlook":
        moved = _outlook_ids_after(performed)
        if moved:
            receipt["message_ids"] = moved
    _record(ledger, decision, "applied", receipt)
    return _result(True, "applied", calls=performed)


def _prepare(decision: dict, ledger: Any) -> dict:
    prepared = dict(decision)
    provider = str(prepared.get("provider") or prepared.get("source") or "")
    prepared["provider"] = provider
    account = str(prepared.get("account") or prepared.get("account_address") or "").strip()
    if account:
        prepared["account"] = account
    if ledger is not None and account and "account_known" not in prepared:
        row = ledger.get_account(provider, account)
        prepared["account_known"] = row is not None
        prepared["account_included"] = bool(
            row and row.get("included", True) and not row.get("removed")
        )
        if row and "folder_sort" not in prepared:
            prepared["folder_sort"] = row["folder_sort"]
    utility = str(prepared.get("utility") or "none")
    prepared.setdefault("utility", utility)
    prepared.setdefault("payload_kind", _PAYLOAD_KIND.get(utility, "none"))
    return prepared


def _pending_account(decision: dict) -> bool:
    configured = str(decision.get("configured_address") or "").strip().casefold()
    account = str(decision.get("account") or "").strip().casefold()
    return bool(configured and account and configured != account)


def _hook_decision(decision: dict, plan: ApplyPlan) -> dict:
    hooked = dict(decision)
    hooked["move"] = bool(plan.move)
    if plan.calls:
        hooked["tool_name"] = plan.calls[0].tool
    return hooked


def _refused_tool(provider: str, plan: ApplyPlan) -> str:
    allowed = PROVIDER_TOOLS.get(provider, frozenset())
    for call in plan.calls:
        if call.tool in FORBIDDEN_TOOLS or call.tool not in allowed:
            return "tool is not on the provider allowlist"
        body = call.arguments.get("body")
        destination = str(body.get("destinationId") or "") if isinstance(body, dict) else ""
        if destination.casefold() in _REFUSED_OUTLOOK:
            return "junk and trash are not destinations"
        argv = call.arguments.get("argv")
        if (
            isinstance(argv, list)
            and argv
            and argv[0] == "move"
            and len(argv) >= _MOVE_ARGC
            and _banned_mailbox(str(argv[-1]))
        ):
            return "junk and trash are not destinations"
    return ""


def _banned_mailbox(mailbox: str) -> bool:
    leaf = mailbox.strip().casefold()
    return leaf in {"junk", "junk email", "spam", "trash", "deleted messages", "deleted items"}


async def _perform_gmail(plan: ApplyPlan, transport: InboxTransport) -> list[dict]:
    performed: list[dict] = []
    label_ids: dict[str, str] | None = None
    for call in plan.calls:
        arguments = dict(call.arguments)
        if call.tool == "batch_modify_gmail_message_labels":
            if label_ids is None:
                label_ids = await _gmail_label_ids(
                    str(arguments.get("user_google_email") or ""), transport, performed,
                )
            await _ensure_gmail_labels(arguments, label_ids, transport, performed)
            arguments = _with_label_ids(arguments, label_ids, performed)
        text = await _tool(transport, "gmail", call.tool, arguments, performed)
        if call.tool == "draft_gmail_message":
            thread_id = str(arguments.get("thread_id") or "")
            # v1.21.0 returns a draft id and does not echo the thread. A draft
            # whose thread was not confirmed is a failed decision: Gmail can
            # otherwise keep an unthreaded draft and report success.
            if not thread_id or thread_id not in text:
                raise _ApplyError("draft thread was not confirmed", performed)
        performed.append(_call_dict(call.tool, arguments, text))
    return performed


async def _gmail_label_ids(
    account: str,
    transport: InboxTransport,
    performed: list[dict],
) -> dict[str, str]:
    text = await _tool(transport, "gmail", "list_gmail_labels", {"user_google_email": account}, performed)
    performed.append(_call_dict("list_gmail_labels", {"user_google_email": account}, text))
    found = {name: label_id for name, label_id in _LABEL_LINE.findall(text)}
    return found


def _with_label_ids(arguments: dict, known: dict[str, str], performed: list[dict]) -> dict:
    resolved = dict(arguments)
    for key in ("add_label_ids", "remove_label_ids"):
        names = arguments.get(key) or []
        if names:
            resolved[key] = [_label_id(name, known, performed) for name in names]
    return resolved


def _label_id(name: str, known: dict[str, str], performed: list[dict]) -> str:
    if name in ("INBOX", "UNREAD"):
        return name
    label_id = known.get(name)
    if not label_id:
        raise _ApplyError(f"label was not created: {name}", performed)
    return label_id


async def _ensure_gmail_labels(
    arguments: dict,
    known: dict[str, str],
    transport: InboxTransport,
    performed: list[dict],
) -> None:
    account = arguments.get("user_google_email", "")
    for name in arguments.get("add_label_ids") or []:
        if name in ("INBOX", "UNREAD") or name in known:
            continue
        created = await _tool(
            transport,
            "gmail",
            "manage_gmail_label",
            {"user_google_email": account, "action": "create", "name": name},
            performed,
        )
        performed.append(_call_dict(
            "manage_gmail_label",
            {"user_google_email": account, "action": "create", "name": name},
            created,
        ))
        name_match = _CREATED_NAME.search(created)
        id_match = _CREATED_ID.search(created)
        if not name_match or not id_match:
            raise _ApplyError("label create did not return an id", performed)
        known[name_match.group(1).strip()] = id_match.group(1)


async def _perform_outlook(plan: ApplyPlan, transport: InboxTransport) -> list[dict]:
    performed: list[dict] = []
    for call in plan.calls:
        arguments = dict(call.arguments)
        if call.tool == "move-mail-message":
            arguments = await _resolve_outlook_destination(arguments, transport, performed)
        text = await _tool(transport, "outlook", call.tool, arguments, performed)
        performed.append(_call_dict(call.tool, arguments, text))
    return performed


def _outlook_ids_after(performed: list[dict]) -> list[str]:
    """Message ids after the moves. A Graph move answers with a new id.

    Empty when nothing moved, or when a move did not echo an id: a partial
    list would make an undo skip the messages it could not name.
    """
    ids: list[str] = []
    for call in performed:
        if call.get("tool") != "move-mail-message":
            continue
        rows = _graph_rows(str(call.get("result") or ""))
        after = str(rows[0].get("id") or "") if rows else ""
        if not after:
            return []
        ids.append(after)
    return ids


async def _resolve_outlook_destination(
    arguments: dict,
    transport: InboxTransport,
    performed: list[dict],
) -> dict:
    body = dict(arguments.get("body") or {})
    destination = str(body.get("destinationId") or "")
    if destination.casefold() in _REFUSED_OUTLOOK:
        raise _ApplyError("junk and trash are not destinations", performed)
    if destination.casefold() in _WELL_KNOWN_OUTLOOK:
        body["destinationId"] = destination.casefold()
        return {**arguments, "body": body}
    if "/" not in destination:
        raise _ApplyError("outlook folder is not a Cerid mailbox", performed)
    parent, _, child = destination.partition("/")
    parent_id = await _outlook_folder(transport, parent, None, performed)
    child_id = await _outlook_folder(transport, child, parent_id, performed)
    body["destinationId"] = child_id
    return {**arguments, "body": body}


async def _outlook_folder(
    transport: InboxTransport,
    name: str,
    parent_id: str | None,
    performed: list[dict],
) -> str:
    list_args: dict[str, Any]
    create_args: dict[str, Any]
    if parent_id is None:
        tool = "list-mail-folders"
        list_args = {}
        create_tool = "create-mail-folder"
        create_args = {"body": {"displayName": name}}
    else:
        tool = "list-mail-child-folders"
        list_args = {"mailFolderId": parent_id}
        create_tool = "create-mail-child-folder"
        create_args = {"mailFolderId": parent_id, "body": {"displayName": name}}
    listed = await _tool(transport, "outlook", tool, list_args, performed)
    performed.append(_call_dict(tool, list_args, listed))
    for row in _graph_rows(listed):
        if row.get("displayName") == name and row.get("id"):
            return str(row["id"])
    created = await _tool(transport, "outlook", create_tool, create_args, performed)
    performed.append(_call_dict(create_tool, create_args, created))
    for row in _graph_rows(created):
        if row.get("id"):
            return str(row["id"])
    raise _ApplyError("outlook folder was not created", performed)


async def _perform_apple(
    decision: dict,
    plan: ApplyPlan,
    transport: InboxTransport,
    ledger: Any,
) -> tuple[list[dict], str, str]:
    await _flush_outbox(transport, ledger)
    commands = [list(call.arguments["argv"]) for call in plan.calls]
    message_id = commands[0][1] if commands else ""
    mailbox = str(decision.get("mailbox_before") or decision.get("mailbox") or "")
    # Every apply records where the message was, so a later undo can put it
    # back without guessing at INBOX.
    if commands and not mailbox:
        code, payload = await transport.apple(["find", message_id])
        if code == APPLE_EXIT_NOT_RUNNING:
            _queue(ledger, decision, commands)
            return [_call_dict("find", {"argv": ["find", message_id]})], "queued", "mail_not_running"
        if code == APPLE_EXIT_AUTOMATION_DENIED:
            raise _ApplyError("automation_denied", [])
        if code != 0 or not payload.get("ok"):
            raise _ApplyError(str(payload.get("error") or "find failed"), [])
        mailbox = str(payload.get("mailbox") or "")
        if mailbox and ledger is not None and decision.get("decision_id"):
            ledger.set_mailbox_before(str(decision["decision_id"]), mailbox)
    performed: list[dict] = []
    for argv in commands:
        code, payload = await transport.apple(argv)
        if code == APPLE_EXIT_NOT_RUNNING:
            _queue(ledger, decision, commands)
            return performed, "queued", "mail_not_running"
        if code == APPLE_EXIT_AUTOMATION_DENIED:
            raise _ApplyError("automation_denied", performed)
        if code != 0 or not payload.get("ok", True):
            raise _ApplyError(str(payload.get("error") or f"ceridmail exited {code}"), performed)
        performed.append(_call_dict(argv[0], {"argv": argv}, json.dumps(payload)))
    return performed, "applied", ""


async def _flush_outbox(transport: InboxTransport, ledger: Any) -> None:
    if ledger is None:
        return
    for row in ledger.list_outbox():
        command = row.get("command") or {}
        if command.get("provider") != "apple_mail":
            continue
        for argv in command.get("commands") or []:
            code, payload = await transport.apple(list(argv))
            if code == APPLE_EXIT_NOT_RUNNING:
                return
            if code == APPLE_EXIT_AUTOMATION_DENIED:
                raise _ApplyError("automation_denied", [])
            if code != 0 or not payload.get("ok", True):
                raise _ApplyError(str(payload.get("error") or "queued apply failed"), [])
        ledger.delete_outbox(row["id"])


def _queue(ledger: Any, decision: dict, commands: list[list[str]]) -> None:
    if ledger is None or not decision.get("decision_id"):
        raise _ApplyError("mail_not_running", [])
    ledger.enqueue_outbox(
        str(decision["decision_id"]),
        {"provider": "apple_mail", "commands": commands},
    )


async def _tool(
    transport: InboxTransport,
    provider: str,
    tool: str,
    arguments: dict,
    performed: list[dict],
) -> str:
    allowed = PROVIDER_TOOLS.get(provider, frozenset())
    if tool in FORBIDDEN_TOOLS or tool not in allowed:
        raise _ApplyError("tool is not on the provider allowlist", performed)
    try:
        return await transport.call_tool(provider, tool, arguments)
    except _ApplyError:
        raise
    except Exception as exc:
        raise _ApplyError(str(exc) or tool, performed) from exc


def _graph_rows(text: str) -> list[dict]:
    try:
        payload = json.loads(text)
    except ValueError:
        return []
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        values = payload.get("value")
        if isinstance(values, list):
            return [row for row in values if isinstance(row, dict)]
        if payload.get("id"):
            return [payload]
    return []


def _call_dict(tool: str, arguments: dict, text: str = "") -> dict:
    item = {"tool": tool, "arguments": arguments}
    if text:
        item["result"] = text
    return item


def _result(ok: bool, status: str, *, calls: list[dict] | None = None, reason: str = "", hook: str = "") -> dict:
    out = {"ok": ok, "status": status, "calls": calls or []}
    if reason:
        out["reason"] = reason
    if hook:
        out["hook"] = hook
    return out


def _store_cursor(ledger: Any, decision: dict) -> None:
    cursor = decision.get("sync_cursor")
    if ledger is None or not cursor:
        return
    provider = str(decision.get("provider") or "")
    address = str(decision.get("account") or "")
    ledger.set_sync_cursor(provider, address, str(cursor))


def _record(ledger: Any, decision: dict, status: str, receipt: dict) -> None:
    if ledger is None or not decision.get("decision_id"):
        return
    # record() replaces the receipt. A failed draft has to keep the body so a retry can see it.
    kept = dict(receipt)
    draft_body = str(decision.get("draft_body") or "").strip()
    if draft_body and "draft_body" not in kept:
        kept["draft_body"] = draft_body
    subject = str(decision.get("subject") or "").strip()
    if subject and "subject" not in kept:
        kept["subject"] = subject
    classification_reason = str(decision.get("classification_reason") or "").strip()
    if classification_reason and "classification_reason" not in kept:
        kept["classification_reason"] = classification_reason
    ledger.record(str(decision["decision_id"]), status=status, receipt=kept)


class _ApplyError(Exception):
    def __init__(self, reason: str, calls: list[dict]) -> None:
        super().__init__(reason)
        self.reason = reason
        self.calls = calls


def _default_transport() -> InboxTransport:
    from core.mcp_clients.result_text import is_error_result, tool_text
    from plugins.apple_mail.data_source import AppleMailDataSource
    from plugins.gmail.data_source import GmailDataSource
    from plugins.outlook.data_source import OutlookDataSource

    gmail = GmailDataSource()
    outlook = OutlookDataSource()
    apple = AppleMailDataSource()

    class _Transport:
        async def call_tool(self, provider: str, tool: str, arguments: dict) -> str:
            if provider == "gmail":
                raw = await gmail._call_mcp(tool, arguments)
            elif provider == "outlook":
                raw = await outlook._call_mcp(tool, arguments)
            else:
                raise _ApplyError("provider is not a mail source", [])
            if is_error_result(raw):
                raise _ApplyError(tool_text(raw)[:500] or tool, [])
            return tool_text(raw)

        async def apple(self, argv: list[str]) -> tuple[int, dict]:
            return await apple.invoke(argv)

    return _Transport()
