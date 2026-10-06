# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Sender memory and rules. This is not a hook and it is not the knowledge base.

Core stays free of this module. The triage agent receives a lookup through
set_inbox_memory. Reconcile reads the ledger it is given and does not open
a second file.
"""
from __future__ import annotations

import json
from typing import Any

from app.inbox.ledger import InboxLedger
from core.agents.inbox_actions import (
    CATEGORIES,
    MODEL_ACTIONS,
    PROVIDER_TOOLS,
    operator_outcome,
)

_RULE_CONFIDENCE = 0.95


def _validate(action: str, category: str) -> None:
    if action not in MODEL_ACTIONS:
        raise ValueError("action is not in the enum")
    if category not in CATEGORIES:
        raise ValueError("category is not in the enum")


def _condition(raw: object) -> dict:
    if isinstance(raw, str):
        parsed = json.loads(raw)
    else:
        parsed = raw
    if not isinstance(parsed, dict):
        raise ValueError("condition must be an object")
    return parsed


def _memory_confidence(row: dict | None) -> float:
    if not row:
        return 0.0
    if row.get("pinned"):
        return 0.95
    return min(0.6, int(row.get("hits") or 0) * 0.3)


def _matches(condition: dict, *, sender: str, subject: str, list_id: str) -> bool:
    if not condition:
        return False
    sender_folded = sender.casefold()
    domain = sender_folded.split("@", 1)[1] if "@" in sender_folded else ""
    if "from" in condition and str(condition["from"]).casefold() != sender_folded:
        return False
    if "domain" in condition and str(condition["domain"]).casefold() != domain:
        return False
    if "subject_prefix" in condition:
        prefix = str(condition["subject_prefix"]).casefold()
        if not subject.casefold().startswith(prefix):
            return False
    if "list_id" in condition and str(condition["list_id"]).casefold() != list_id.casefold():
        return False
    return True


def _best_rule(rules: list[dict], source: str, sender: str, subject: str, list_id: str) -> dict | None:
    ranked: list[tuple[int, int, dict]] = []
    for rule in rules:
        if not rule.get("enabled"):
            continue
        rule_source = str(rule.get("source") or "*")
        if rule_source not in {source, "*"}:
            continue
        condition = rule.get("condition")
        if not isinstance(condition, dict):
            continue
        if not _matches(condition, sender=sender, subject=subject, list_id=list_id):
            continue
        ranked.append((1 if rule_source == source else 0, len(condition), rule))
    if not ranked:
        return None
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return ranked[0][2]


def signals_for(
    ledger: InboxLedger,
    source: str,
    sender: str,
    subject: str,
    list_id: str = "",
) -> dict[str, Any]:
    """Memory and the best matching rule. An unpinned streak stays below the skip band."""
    sender = sender.casefold().strip()
    signals: dict[str, Any] = {}
    memory = ledger.get_sender(source, sender) if sender else None
    if memory is not None:
        signals["memory_action"] = memory.get("action")
        signals["memory_category"] = memory.get("category")
        signals["memory_confidence"] = _memory_confidence(memory)
    rule = _best_rule(ledger.list_rules(source), source, sender, subject, list_id)
    if rule is not None:
        signals["rule_action"] = rule.get("action")
        signals["rule_category"] = rule.get("category")
        signals["rule_confidence"] = _RULE_CONFIDENCE
    return signals


def lookup_signals(source: str, sender: str, subject: str, list_id: str = "") -> dict[str, Any]:
    """Open the ledger only when triage asks. Importing this module does not."""
    from app.inbox.review import open_ledger

    return signals_for(open_ledger(), source, sender, subject, list_id)


def _receipt(raw: object) -> dict:
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


def reconcile_threads(ledger: InboxLedger, threads: list) -> int:
    """Learn from this pass's mailbox view. The pin applies on the next pass."""
    learned = 0
    for thread in threads:
        observation = getattr(thread, "observation", None) or {}
        if not isinstance(observation, dict) or not observation:
            continue
        source = str(getattr(thread, "source", "") or "")
        thread_id = str(getattr(thread, "thread_id", "") or "")
        sender = str(getattr(thread, "sender", "") or "").casefold().strip()
        if not source or not thread_id or not sender:
            continue
        row = ledger.latest_applied(source, thread_id)
        if row is None:
            continue
        decision_id = str(row.get("id") or "")
        before = ledger.get_sender(source, sender)
        outcome = operator_outcome(
            {
                "provider": source,
                "source": source,
                "action": row.get("action"),
                "category": row.get("category"),
                "receipt": _receipt(row.get("receipt_json")),
            },
            observation,
        )
        if outcome is None:
            continue
        action, category = outcome
        after = ledger.note_correction(
            source=source,
            sender=sender,
            category=category,
            action=action,
            decision_id=decision_id,
        )
        if before is None or int(after.get("hits") or 0) != int(before.get("hits") or 0):
            learned += 1
    return learned


def pin_sender(*, source: str, sender: str, action: str, category: str, domain: str = "") -> dict:
    _validate(action, category)
    if source not in PROVIDER_TOOLS:
        raise ValueError("source is not a mail provider")
    from app.inbox.review import open_ledger

    return open_ledger().pin_sender(
        source=source,
        sender=sender,
        category=category,
        action=action,
        domain=domain,
    )


def upsert_rule(
    *,
    rule_id: str = "",
    source: str = "*",
    condition: object,
    action: str,
    category: str,
    enabled: bool = True,
) -> dict:
    _validate(action, category)
    parsed = _condition(condition)
    from app.inbox.review import open_ledger

    return open_ledger().upsert_rule(
        rule_id=rule_id,
        source=source,
        condition=parsed,
        action=action,
        category=category,
        enabled=enabled,
    )
