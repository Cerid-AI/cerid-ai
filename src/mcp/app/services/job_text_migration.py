# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Job records written before forget phase 6 hold the words they work on
(spec §6.5). This moves them out: a job that may still run (queued, running,
paused, or failed and retryable) gets its text stored under its conversation
and a ``text_key``; a completed job drops the text, and a completed memory
extraction keeps its result the same way. A job of a forgotten conversation
keeps nothing. Records already holding a reference are left alone, so a second
run changes nothing.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.db.redis import turn_text
from core.forget import registry as forget_registry
from core.processor.job import JobState

logger = logging.getLogger("ai-companion.job_text_migration")

TEXT_FIELDS: dict[str, tuple[str, ...]] = {
    "feedback_ingest": ("user_message", "assistant_response"),
    "memory_extract": ("response_text",),
}


def _text(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value or "")


def migrate_job_texts(redis_client: Any = None) -> dict[str, int]:
    if redis_client is None:
        from app.deps import get_redis
        redis_client = get_redis()
    counts = {"moved": 0, "dropped": 0}
    for key in redis_client.scan_iter(match="cerid:proc:job:*", count=500):
        record = {_text(k): _text(v) for k, v in (redis_client.hgetall(key) or {}).items()}
        fields = TEXT_FIELDS.get(record.get("job_type", ""))
        if not fields:
            continue
        try:
            payload = json.loads(record.get("payload") or "{}")
            metadata = json.loads(record.get("metadata") or "{}")
        except ValueError:
            continue
        result = metadata.pop("result", None) if isinstance(metadata, dict) else None
        if not any(f in payload for f in fields) and result is None:
            continue
        texts = {f: str(payload.pop(f) or "") for f in fields if f in payload}
        cid = str(payload.get("conversation_id") or "")
        keep = {} if record.get("state") == JobState.COMPLETED.value else texts
        if result is not None:
            keep = {**keep, "result": json.dumps(result)}
        if keep and not (cid and forget_registry.is_forgotten("conversation", cid)):
            payload["text_key"] = turn_text.new_key(cid)
            turn_text.put(redis_client, payload["text_key"], keep)
            counts["moved"] += 1
        else:
            payload["text_key"] = ""
            counts["dropped"] += 1
        redis_client.hset(key, mapping={"payload": json.dumps(payload), "metadata": json.dumps(metadata or {})})
    if any(counts.values()):
        logger.info("job text migration: %s", counts)
    return counts
