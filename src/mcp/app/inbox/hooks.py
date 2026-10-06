# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Ordered checks a model result must pass before it is stored or applied.

A failure names the hook and writes nothing. The model never receives a mail tool.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.agents.inbox_actions import (
    ACTIONS,
    CATEGORIES,
    FORBIDDEN_TOOLS,
    PROVIDER_TOOLS,
    UTILITIES,
    moves,
)


@dataclass(frozen=True)
class HookResult:
    ok: bool
    hook: str
    reason: str = ""
    domain: str | None = None


def _fail(hook: str, reason: str) -> HookResult:
    return HookResult(ok=False, hook=hook, reason=reason)


def schema_hook(decision: dict) -> HookResult | None:
    if decision.get("action") not in ACTIONS:
        return _fail("schema", "action is not in the enum")
    if decision.get("category") not in CATEGORIES:
        return _fail("schema", "category is not in the enum")
    if decision.get("utility") not in UTILITIES:
        return _fail("schema", "utility is not in the enum")
    return None


def allowlist_hook(decision: dict) -> HookResult | None:
    """Reject a forbidden tool name in the tool field or the model text.

    Message body is data and is not inspected here. An instruction inside the
    body cannot select a tool; only the tool name the executor would call can.
    """
    tool = str(decision.get("tool_name") or "")
    model_text = str(decision.get("model_text") or "")
    blob = f"{tool}\n{model_text}"
    if any(name in blob for name in FORBIDDEN_TOOLS):
        return _fail("allowlist", "forbidden tool")
    provider = str(decision.get("provider") or "")
    if not tool:
        return None
    allowed = PROVIDER_TOOLS.get(provider)
    if allowed is None or tool not in allowed:
        return _fail("allowlist", "tool is not on the provider allowlist")
    return None


def account_hook(decision: dict) -> HookResult | None:
    if not decision.get("account_known"):
        return _fail("account", "account is not connected and included")
    # Removing or excluding an address stops new applies. Undo of a write
    # that already happened stays available.
    if str(decision.get("action") or "") != "undo" and not decision.get("account_included", True):
        return _fail("account", "account is not connected and included")
    if not str(decision.get("account") or "").strip():
        return _fail("account", "account is missing")
    return None


def folder_policy_hook(decision: dict) -> HookResult | None:
    action = str(decision.get("action") or "")
    folder_sort = bool(decision.get("folder_sort"))
    requested_move = bool(decision.get("move"))
    if requested_move and not moves(action, folder_sort):
        return _fail("folder_policy", "move requested while folder sorting is off")
    return None


def rag_route_hook(decision: dict) -> HookResult | None:
    utility = decision.get("utility")
    payload = decision.get("payload_kind") or "none"
    if utility == "none":
        if payload not in ("none",):
            return _fail("rag_route", "utility none stores no body")
        return HookResult(ok=True, hook="rag_route", domain=None)
    if utility == "financial":
        if payload != "card":
            return _fail("rag_route", "a financial thread stores a card, not the message body")
        # The card is an inbox row of its own record type; cerid-finance reads
        # it there through its record-typed grant and sees no other mail.
        return HookResult(ok=True, hook="rag_route", domain="inbox")
    if utility == "correspondence":
        if payload == "raw":
            return _fail("rag_route", "correspondence stores an excerpt, not the raw body")
        return HookResult(ok=True, hook="rag_route", domain="inbox")
    return _fail("rag_route", "unknown utility")


def audit_hook(decision: dict) -> HookResult | None:
    if not decision.get("decision_id"):
        return _fail("audit", "decision row is missing")
    return None


_HOOKS = (
    schema_hook,
    allowlist_hook,
    account_hook,
    folder_policy_hook,
    rag_route_hook,
    audit_hook,
)


def run_hooks(decision: dict, *, include_audit: bool = True) -> HookResult:
    """Run the chain. Stop at the first failure."""
    hooks = _HOOKS if include_audit else _HOOKS[:-1]
    for hook in hooks:
        failed = hook(decision)
        if failed is not None and not failed.ok:
            return failed
    routed = rag_route_hook(decision)
    return HookResult(ok=True, hook="audit" if include_audit else "rag_route", domain=routed.domain if routed else None)
