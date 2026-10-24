# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""m0008: verification reports are keyed per message (forget spec §6.2).

Idempotent. A report is ``(:VerificationReport {conversation_id, message_id})``;
reports written before the key existed belong to no message and get
``message_id = ''``, the key a caller without messages still writes. The index
serves both the keyed MERGE and the per-conversation listing.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("ai-companion.migrations.m0008")


def run(driver) -> dict[str, int]:
    with driver.session() as session:
        session.run(
            "CREATE INDEX verification_report_key IF NOT EXISTS "
            "FOR (r:VerificationReport) ON (r.conversation_id, r.message_id)"
        )
        rec = session.run(
            "MATCH (r:VerificationReport) WHERE r.message_id IS NULL SET r.message_id = '' RETURN count(r) AS n"
        ).single()
    keyed = int(rec["n"]) if rec else 0
    if keyed:
        logger.info("m0008: %d earlier reports keyed to no message", keyed)
    return {"reports_keyed": keyed}
