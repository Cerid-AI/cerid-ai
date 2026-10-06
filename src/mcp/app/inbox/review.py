# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Review queue for inbox decisions.

The MCP tools and the Sources page call these functions. Neither one
owns a second copy of apply or undo. Dry-run is the default. Automatic
filing runs only for categories the account has turned on, and it stops
after APPLY_CAP decisions a pass, successful or not.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.inbox.executor import apply_decision, inbox_actions_enabled
from app.inbox.ledger import InboxLedger
from core.agents.inbox_actions import CATEGORIES, LABEL_NAME, PROVIDER_TOOLS

APPLY_CAP = 50
_MAILBOX_ACTIONS = frozenset({"keep", "archive", "mark_read", "draft"})
_AUTO_ACTIONS = frozenset({"keep", "archive", "mark_read"})
_WELL_KNOWN_OUTLOOK = frozenset({"inbox", "archive", "drafts", "sentitems"})


def open_ledger() -> InboxLedger:
    data_dir = os.getenv("DATA_DIR", "data")
    return InboxLedger(Path(data_dir) / "inbox.sqlite")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _loads(raw: object) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _json_list(raw: object) -> list:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except ValueError:
            return [raw]
        if isinstance(parsed, list):
            return parsed
    return []


def _coerce_ids(raw: object) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        text = raw.strip()
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = None
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        return [part.strip() for part in text.split(",") if part.strip()]
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    text = str(raw).strip()
    return [text] if text else []


def _clamp(action: str) -> str:
    return action if action in _MAILBOX_ACTIONS else "keep"


def _calls_left_inbox(calls: list) -> bool:
    for call in calls:
        if not isinstance(call, dict):
            continue
        args = call.get("arguments")
        if not isinstance(args, dict):
            continue
        remove = args.get("remove_label_ids") or []
        if isinstance(remove, list) and "INBOX" in remove:
            return True
        body = args.get("body")
        if isinstance(body, dict):
            dest = str(body.get("destinationId") or "")
            if dest and dest.casefold() not in _WELL_KNOWN_OUTLOOK:
                return True
        argv = args.get("argv")
        if isinstance(argv, list) and argv and argv[0] == "move":
            target = str(argv[-1])
            if target.startswith("Cerid/"):
                return True
    return False


def _sorted_keep(row: dict, account: dict | None) -> bool:
    """A keep that filed into Cerid/<Category>, including after sorting is turned off."""
    if str(row.get("action") or "") != "keep":
        return False
    receipt = _loads(row.get("receipt_json"))
    calls = receipt.get("calls")
    if isinstance(calls, list):
        return _calls_left_inbox(calls)
    return bool(account and account.get("folder_sort"))


def _moved(row: dict, account: dict | None) -> bool:
    return str(row.get("action") or "") == "archive" or _sorted_keep(row, account)


def _labels_for_undo(row: dict, account: dict | None) -> list[str]:
    receipt = _loads(row.get("receipt_json"))
    stored = receipt.get("current_labels")
    if isinstance(stored, list):
        return [str(item) for item in stored]
    label = LABEL_NAME.get(str(row.get("category") or ""), "")
    action = str(row.get("action") or "")
    labels = [label] if label and action in ("keep", "archive") else []
    # INBOX is a Gmail label. Outlook keeps its folder apart from categories.
    if str(row.get("source") or "") == "gmail" and not _moved(row, account):
        labels.insert(0, "INBOX")
    return labels


def decision_from_row(row: dict, account: dict | None) -> dict:
    provider = str(row.get("source") or "")
    address = str(row.get("account_address") or "")
    decision: dict[str, Any] = {
        "decision_id": row.get("id") or "",
        "provider": provider,
        "source": provider,
        "account": address,
        "account_address": address,
        "provider_thread_id": row.get("provider_thread_id") or "",
        "message_ids": _json_list(row.get("message_ids")),
        "category": row.get("category") or "",
        "utility": row.get("utility") or "none",
        "action": row.get("action") or "keep",
        "band": row.get("band") or "",
        "model": row.get("model") or "",
        "confidence": row.get("confidence") or 0,
        "mailbox_before": row.get("mailbox_before") or "",
        "account_known": account is not None,
        "account_included": bool(account and account.get("included") and not account.get("removed")),
        "folder_sort": bool(account and account.get("folder_sort")),
    }
    receipt = _loads(row.get("receipt_json"))
    stored = receipt.get("current_labels")
    if isinstance(stored, list):
        decision["current_labels"] = [str(item) for item in stored]
    draft_body = receipt.get("draft_body")
    if isinstance(draft_body, str) and draft_body.strip():
        decision["draft_body"] = draft_body.strip()
    subject = receipt.get("subject")
    if isinstance(subject, str) and subject.strip():
        decision["subject"] = subject.strip()
    classification_reason = receipt.get("classification_reason")
    if isinstance(classification_reason, str) and classification_reason.strip():
        decision["classification_reason"] = classification_reason.strip()
    if provider == "gmail":
        configured = os.getenv("USER_GOOGLE_EMAIL", "").strip()
        if configured:
            decision["configured_address"] = configured
    return decision


def undo_from_row(row: dict, account: dict | None) -> dict:
    decision = decision_from_row(row, account)
    decision["action"] = "undo"
    decision["undone_action"] = str(row.get("action") or "")
    decision["current_labels"] = _labels_for_undo(row, account)
    receipt = _loads(row.get("receipt_json"))
    moved_ids = receipt.get("message_ids")
    if isinstance(moved_ids, list) and moved_ids:
        # A Graph move gave the message a new id; the old one is gone.
        decision["message_ids"] = [str(item) for item in moved_ids]
    before = str(decision.get("mailbox_before") or "").strip()
    if decision["provider"] == "outlook":
        # Empty tells the planner the message never left its folder.
        decision["mailbox_before"] = (before or "inbox") if _moved(row, account) else ""
    elif not before:
        decision["mailbox_before"] = "INBOX"
    return decision


def _gmail_consent(address: str) -> str:
    configured = os.getenv("USER_GOOGLE_EMAIL", "").strip()
    if configured and address.casefold() == configured.casefold():
        return "readonly"
    return "pending"


def add_account(
    ledger: InboxLedger,
    *,
    provider: str,
    address: str,
    display_name: str = "",
) -> dict:
    if provider not in PROVIDER_TOOLS:
        raise ValueError("provider is not a mail source")
    address = address.strip()
    if "@" not in address or " " in address:
        raise ValueError("address is not an email")
    if provider == "gmail":
        configured = os.getenv("USER_GOOGLE_EMAIL", "").strip()
        if configured and address.casefold() == configured.casefold():
            address = configured
    existing = ledger.get_account(provider, address)
    if existing is not None:
        fields: dict[str, Any] = {"removed": False, "included": True}
        if display_name:
            fields["display_name"] = display_name
        if provider == "gmail" and _gmail_consent(address) == "pending":
            fields["consent"] = "pending"
        ledger.update_account(provider, address, **fields)
    else:
        consent = _gmail_consent(address) if provider == "gmail" else "readonly"
        ledger.upsert_account(
            provider=provider,
            address=address,
            display_name=display_name,
            included=True,
            folder_sort=False,
            consent=consent,
        )
    account = ledger.get_account(provider, address)
    if account is None:
        raise ValueError("account was not stored")
    return account


def change_account(ledger: InboxLedger, provider: str, address: str, **fields: object) -> dict | None:
    if "auto_apply" in fields:
        categories = fields["auto_apply"]
        if not isinstance(categories, list) or any(item not in CATEGORIES for item in categories):
            raise ValueError("category is not in the enum")
    if not fields or not ledger.update_account(provider, address, **fields):
        return None
    return ledger.get_account(provider, address)


def skip_decision(ledger: InboxLedger, decision_id: str) -> dict:
    row = ledger.get_decision(decision_id)
    if row is None or row.get("status") != "proposed":
        return {"ok": False, "status": "skipped", "reason": "not proposed", "decision_id": decision_id}
    ledger.record(decision_id, status="skipped", receipt={"skipped": True})
    return {"ok": True, "status": "skipped", "decision_id": decision_id}


def _touch(ledger: InboxLedger, decision: dict, **fields: object) -> None:
    provider = str(decision.get("provider") or "")
    address = str(decision.get("account") or "")
    if provider and address:
        ledger.update_account(provider, address, **fields)


async def _apply_proposed(
    ledger: InboxLedger,
    row: dict,
    *,
    dry_run: bool,
    transport: Any,
    actions_enabled: bool | None,
) -> dict:
    account = ledger.get_account(str(row.get("source") or ""), str(row.get("account_address") or ""))
    decision = decision_from_row(row, account)
    outcome = await apply_decision(
        decision,
        dry_run=dry_run,
        ledger=ledger,
        transport=transport,
        actions_enabled=actions_enabled,
    )
    status = str(outcome.get("status") or "")
    if dry_run:
        return outcome
    if status == "noop":
        receipt: dict[str, object] = {"noop": True, "calls": outcome.get("calls") or []}
        prior = _loads(row.get("receipt_json"))
        marker = prior.get("classification_reason")
        if isinstance(marker, str) and marker.strip():
            receipt["classification_reason"] = marker.strip()
        ledger.record(str(row["id"]), status="applied", receipt=receipt)
        outcome = {**outcome, "status": "applied", "noop": True}
        status = "applied"
    if status == "applied":
        _touch(ledger, decision, last_apply=_now())
    elif status == "failed":
        _touch(ledger, decision, last_rejection=str(outcome.get("reason") or ""))
    return outcome


async def apply_ids(
    decision_ids: object,
    *,
    dry_run: bool = True,
    ledger: InboxLedger,
    transport: Any = None,
    cap: int = APPLY_CAP,
    actions_enabled: bool | None = None,
) -> dict:
    """Apply proposed rows. The id list itself is capped; dry-run is the default."""
    results = []
    for decision_id in _coerce_ids(decision_ids)[:cap]:
        row = ledger.get_decision(decision_id)
        if row is None or row.get("status") != "proposed":
            results.append({
                "decision_id": decision_id,
                "ok": False,
                "status": "skipped",
                "reason": "not proposed",
            })
            continue
        outcome = await _apply_proposed(
            ledger,
            row,
            dry_run=dry_run,
            transport=transport,
            actions_enabled=actions_enabled,
        )
        results.append({"decision_id": decision_id, **outcome})
    return {"results": results, "dry_run": dry_run}


async def undo_decision(
    decision_id: str,
    *,
    dry_run: bool = True,
    ledger: InboxLedger,
    transport: Any = None,
    actions_enabled: bool | None = None,
) -> dict:
    """Undo one applied row. A dry-run, or the flag off, does not add a queue row."""
    row = ledger.get_decision(decision_id)
    if row is None or row.get("status") != "applied":
        return {"ok": False, "status": "skipped", "reason": "not applied", "decision_id": decision_id}
    account = ledger.get_account(str(row.get("source") or ""), str(row.get("account_address") or ""))
    undo = undo_from_row(row, account)
    enabled = inbox_actions_enabled() if actions_enabled is None else actions_enabled
    if dry_run or not enabled:
        outcome = await apply_decision(
            undo,
            dry_run=dry_run,
            ledger=ledger,
            transport=transport,
            actions_enabled=actions_enabled,
        )
        return {"decision_id": decision_id, **outcome}
    undo_id = ledger.propose(
        source=row["source"],
        provider_thread_id=row["provider_thread_id"],
        account_address=row["account_address"],
        message_ids=_json_list(row.get("message_ids")),
        category=row["category"],
        utility=row["utility"],
        action="undo",
        band=row.get("band") or "",
        model=row.get("model") or "",
        confidence=row.get("confidence") or 0,
        mailbox_before=undo.get("mailbox_before") or "",
    )
    undo["decision_id"] = undo_id
    outcome = await apply_decision(
        undo,
        dry_run=False,
        ledger=ledger,
        transport=transport,
        actions_enabled=actions_enabled,
    )
    if outcome.get("status") == "applied":
        ledger.record(decision_id, status="undone", receipt={"undo_id": undo_id})
        _touch(ledger, undo, last_apply=_now())
    elif outcome.get("status") == "failed":
        _touch(ledger, undo, last_rejection=str(outcome.get("reason") or ""))
    return {"decision_id": decision_id, "undo_id": undo_id, **outcome}


def _eligible(row: dict, account: dict | None) -> bool:
    if str(row.get("band") or "") == "needs_review":
        return False
    if str(row.get("action") or "") not in _AUTO_ACTIONS:
        return False
    if account is None or not account.get("included") or account.get("removed"):
        return False
    return str(row.get("category") or "") in (account.get("auto_apply") or [])


async def auto_apply_pass(
    ledger: InboxLedger,
    *,
    transport: Any = None,
    cap: int = APPLY_CAP,
    actions_enabled: bool | None = None,
) -> dict:
    """File the oldest eligible proposals. Every attempt uses the cap; ineligible rows do not."""
    enabled = inbox_actions_enabled() if actions_enabled is None else actions_enabled
    if not enabled:
        return {"applied": 0, "attempted": 0, "reason": "actions flag is off"}
    applied = 0
    attempted = 0
    for row in ledger.list_decisions(status="proposed", newest_first=False, limit=10_000):
        if attempted >= cap:
            break
        account = ledger.get_account(str(row.get("source") or ""), str(row.get("account_address") or ""))
        if not _eligible(row, account):
            continue
        attempted += 1
        outcome = await _apply_proposed(
            ledger,
            row,
            dry_run=False,
            transport=transport,
            actions_enabled=True,
        )
        if outcome.get("status") == "applied":
            applied += 1
    return {"applied": applied, "attempted": attempted}


def included_addresses(source: str) -> list[str]:
    """Addresses added and included for a provider, casefolded. Opens the ledger only when called."""
    return [
        str(row["address"]).casefold()
        for row in open_ledger().list_accounts()
        if row["provider"] == source and row.get("included") and not row.get("removed")
    ]


def _sole_account(ledger: InboxLedger, source: str) -> str:
    rows = [
        row for row in ledger.list_accounts()
        if row["provider"] == source and row.get("included") and not row.get("removed")
    ]
    if len(rows) == 1:
        return str(rows[0]["address"])
    return ""


def propose_triage(ledger: InboxLedger, threads: list) -> int:
    """Propose writable threads that name an included account. Subject-only groups are skipped."""
    proposed = 0
    for thread in threads:
        if not getattr(thread, "writable", False):
            continue
        message_ids = [str(item) for item in (getattr(thread, "message_ids", None) or []) if str(item).strip()]
        source = str(getattr(thread, "source", "") or "")
        account = str(getattr(thread, "account", "") or "").strip() or _sole_account(ledger, source)
        if not message_ids or not account or not source:
            continue
        row = ledger.get_account(source, account)
        if row is None or not row.get("included") or row.get("removed"):
            continue
        artifacts: list[dict[str, str]] = []
        inbox_id = getattr(thread, "artifact_id", None)
        finance_id = getattr(thread, "finance_artifact_id", None)
        if inbox_id:
            artifacts.append({"id": str(inbox_id), "domain": "inbox"})
        if finance_id:
            artifacts.append({"id": str(finance_id), "domain": "inbox"})
        observation = getattr(thread, "observation", None) or {}
        categories = str(observation.get("categories") or "") if source == "outlook" else ""
        ledger.propose(
            source=source,
            provider_thread_id=str(thread.thread_id),
            account_address=account,
            message_ids=message_ids,
            category=str(thread.category),
            utility=str(getattr(thread, "utility", "") or "none"),
            action=_clamp(str(getattr(thread, "action", "") or "keep")),
            band=str(getattr(thread, "band", "") or ""),
            model=str(getattr(thread, "model", "") or ""),
            confidence=getattr(thread, "confidence", 0) or 0,
            mailbox_before=str(getattr(thread, "mailbox", "") or ""),
            rag_artifact_ids=artifacts,
            draft_body=str(getattr(thread, "draft_body", "") or ""),
            subject=str(getattr(thread, "subject", "") or ""),
            classification_reason=str(getattr(thread, "classification_reason", "") or ""),
            current_labels=[part.strip() for part in categories.split(",") if part.strip()],
        )
        proposed += 1
    return proposed


async def record_and_apply(result: Any, ledger: InboxLedger | None = None) -> dict:
    """Propose the triage pass, then one capped auto-apply. Opens the ledger when omitted."""
    own = ledger if ledger is not None else open_ledger()
    threads = getattr(result, "threads", None) or []
    from app.inbox.learn import reconcile_threads

    learned = reconcile_threads(own, threads)
    proposed = propose_triage(own, threads)
    filed = await auto_apply_pass(own)
    return {
        "proposed": proposed,
        "applied": filed.get("applied", 0),
        "reason": filed.get("reason", ""),
        "learned": learned,
    }


def discovered_addresses(
    *,
    discover_apple: bool = False,
    apple: Callable[[], list[str]] | None = None,
) -> dict:
    """Gmail comes from the env. Apple is scanned only when discover_apple is set."""
    gmail = os.getenv("USER_GOOGLE_EMAIL", "").strip()
    found: list[str] = []
    error = ""
    if discover_apple:
        try:
            found = list((apple or (lambda: []))())
        except Exception as exc:  # noqa: BLE001 — a scan failure is an empty list, not a crash
            found = []
            error = str(exc)
    return {
        "gmail": [gmail] if gmail else [],
        "outlook": [],
        "apple_mail": found,
        "error": error,
    }


async def scan_apple_addresses() -> dict:
    """Read addresses from ceridmail scan. Never raises and never runs unless asked."""
    try:
        from plugins.apple_mail.data_source import AppleMailDataSource

        code, payload = await AppleMailDataSource().invoke(["scan"])
    except Exception as exc:  # noqa: BLE001 — discovery is optional and must not take down the page
        return {"addresses": [], "error": str(exc)}
    if isinstance(payload, dict) and payload.get("error") == "runs_on_desktop":
        return {"addresses": [], "error": "runs_on_desktop"}
    if code != 0 or not isinstance(payload, dict):
        return {"addresses": [], "error": "scan failed"}
    raw = payload.get("addresses") or []
    if not isinstance(raw, list):
        return {"addresses": [], "error": "scan failed"}
    addresses = [str(item) for item in raw if isinstance(item, str) and "@" in item]
    return {"addresses": addresses, "error": ""}


def source_state(provider: str) -> str:
    """configured, not_configured, not_registered, or a finer state the source names (runs_on_desktop)."""
    if not provider:
        return ""
    from app.data_sources import registry

    source = registry.get(provider)
    if source is None:
        return "not_registered"
    state_fn = getattr(source, "configured_state", None)
    state = state_fn() if callable(state_fn) else None
    if isinstance(state, str) and state:
        return state
    return "configured" if source.is_configured() else "not_configured"


def setup_view(ledger: InboxLedger, provider: str = "") -> dict:
    from config import settings

    def _match(row: dict, key: str) -> bool:
        return not provider or str(row.get(key) or "") == provider

    accounts = [row for row in ledger.list_accounts(include_removed=True) if _match(row, "provider")]
    proposals = [
        row for row in ledger.list_decisions(status="proposed", newest_first=True, limit=100)
        if _match(row, "source")
    ]
    recent = [
        row for row in ledger.list_decisions(status="applied", newest_first=True, limit=20)
        if _match(row, "source")
    ]
    pins = [row for row in ledger.list_sender_pins() if _match(row, "source")]
    chat = settings.INTERNAL_LLM_MODEL or settings.INTERNAL_LLM_MODEL_DEFAULT
    return {
        "actions_enabled": inbox_actions_enabled(),
        "source_state": source_state(provider),
        "background_model": settings.INTERNAL_LLM_MODEL_BACKGROUND,
        "chat_model": chat,
        "accounts": accounts,
        "proposals": proposals,
        "recent": recent,
        "pins": pins,
    }
