# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Verification reports are stored once, in the graph (forget spec §6.2).

The web client used to embed every report in the conversation it belonged to,
so each report also lived in localStorage and in every sync file. A
conversation that still arrives with embedded reports (an older client, or a
sync file written before this change) gives them up here: each report moves to
``(:VerificationReport {conversation_id, message_id})`` unless the graph
already holds that message's report, and the conversation is stored without
them. When the graph cannot take them, the conversation keeps them and the
next save or boot tries again, so nothing is lost on the way.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.verification_reports")

EMBEDDED_KEYS = ("verificationReports", "verificationReport")
#: A report with more claims than this is not taken: a live verification never
#: comes close, and every claim costs graph writes.
MAX_CLAIMS = 200
_MESSAGE_ID = re.compile(r"^[A-Za-z0-9_\-]{1,128}$")


def _embedded(conversation: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The conversation's embedded reports, by message id. The singular legacy
    field belonged to the last assistant message."""
    reports = conversation.get("verificationReports")
    found = {str(k): v for k, v in reports.items()} if isinstance(reports, dict) else {}
    legacy = conversation.get("verificationReport")
    if isinstance(legacy, dict):
        assistant = [m for m in conversation.get("messages") or [] if isinstance(m, dict) and m.get("role") == "assistant"]
        if assistant and assistant[-1].get("id"):
            found.setdefault(str(assistant[-1]["id"]), legacy)
    return found


def _save(driver: Any, conversation_id: str, message_id: str, report: Any) -> bool:
    """Whether the graph holds this message's report afterwards. A report with
    nothing in it (skipped, no claims, an invalid message id) is not kept."""
    from app.db.neo4j.artifacts import get_verification_report, save_verification_report
    from core.agents.hallucination.streaming import _summarize_claims

    claims = report.get("claims") if isinstance(report, dict) else None
    if (
        not _MESSAGE_ID.match(message_id) or not isinstance(claims, list) or not claims
        or len(claims) > MAX_CLAIMS or report.get("skipped")
    ):
        return False
    if get_verification_report(driver, conversation_id, message_id) is not None:
        return True  # the graph's copy is the one kept
    claims = [c for c in claims if isinstance(c, dict)]
    counts, overall = _summarize_claims(claims)
    return save_verification_report(
        driver, conversation_id, claims, overall,
        verified=counts["verified"], unverified=counts["unverified"], uncertain=counts["uncertain"],
        total=counts["total"], agreed=counts["agreed"], message_id=message_id,
    ) is not None


def store_reports(conversation_id: str, reports: dict[str, Any]) -> list[str]:
    """Store the reports a client kept for a conversation; the message ids the
    graph holds afterwards (none for a forgotten conversation)."""
    from app.deps import get_neo4j

    driver = get_neo4j()
    return [mid for mid, report in reports.items() if _save(driver, conversation_id, str(mid), report)]


def absorb_embedded_reports(conversation: dict[str, Any]) -> dict[str, Any]:
    """The conversation to store: without embedded reports once the graph holds
    them, unchanged if it does not (an error is logged and retried later)."""
    if not any(k in conversation for k in EMBEDDED_KEYS):
        return conversation
    cid = str(conversation.get("id") or "")
    try:
        store_reports(cid, _embedded(conversation))
    except Exception as exc:  # noqa: BLE001 — the conversation keeps its reports; the next save retries
        log_swallowed_error("app.services.verification_reports.absorb", exc, context={"conversation_id": cid})
        return conversation
    return {k: v for k, v in conversation.items() if k not in EMBEDDED_KEYS}


def migrate_reports() -> dict[str, int]:
    """Boot: key the graph's reports per message, bring every stored report's
    source edges and ``edge_status`` up to date (S11-3: a report the writer
    skips below because the graph already holds it is covered here), then move
    the reports sync files still embed."""
    from app.db.neo4j.migrations import m0001_backfill_verification_edges, m0008_report_per_message
    from app.deps import get_neo4j

    driver = get_neo4j()
    return {
        **m0008_report_per_message.run(driver),
        **m0001_backfill_verification_edges.run(driver),
        **migrate_embedded_reports(),
    }


def migrate_embedded_reports(sync_dir: str | None = None) -> dict[str, int]:
    """Boot migration: every sync file still carrying reports gives them up.
    Skipped under Private Mode, which blocks conversation writes; a second run
    finds nothing to do."""
    import config
    from app.services.private_mode import private_blocks
    from app.sync.user_state import read_conversation, read_conversations, write_conversation
    from core.forget import registry as forget_registry

    sync_dir = sync_dir or config.SYNC_DIR
    counts = {"conversations": 0, "kept": 0}
    if not sync_dir or private_blocks(1):
        return counts
    for conversation in read_conversations(sync_dir):
        if not any(k in conversation for k in EMBEDDED_KEYS):
            continue
        cid = str(conversation.get("id") or "")
        if not cid or forget_registry.is_forgotten("conversation", cid):
            continue
        cleaned = absorb_embedded_reports(conversation)
        if cleaned is conversation:
            counts["kept"] += 1
            continue
        # The graph round trips took time: a forget, or a newer save from a
        # client, that landed meanwhile wins; the next boot tries again.
        current = read_conversation(sync_dir, cid)
        if forget_registry.is_forgotten("conversation", cid) or not current or (
            current.get("_synced_at") != conversation.get("_synced_at")
        ):
            counts["kept"] += 1
            continue
        write_conversation(sync_dir, {k: v for k, v in cleaned.items() if not k.startswith("_")})
        counts["conversations"] += 1
    if any(counts.values()):
        logger.info("embedded verification reports: %s", counts)
    return counts
