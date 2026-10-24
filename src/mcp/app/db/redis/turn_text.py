# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""The words a background job works on, stored apart from the job (forget spec §6.5).

A job record is the processor's audit trail and lives 14 days whatever its
state, so it holds only a reference: ``text_key``. The text (a chat turn, an
SDK caller's response text) and anything derived from it that the job must
hand back (a memory-extract result) live here, under the conversation they
belong to, so a conversation forget can erase every one of them by prefix.

The conversation id is hashed into the key: an id may contain ``:`` or glob
characters, and one conversation's prefix must never match another's.
"""
from __future__ import annotations

import hashlib
import uuid
from typing import Any

from app.db.redis.processor_queue import JOB_RECORD_TTL_S

PREFIX = "cerid:proc:text:"


def conversation_prefix(conversation_id: str) -> str:
    return f"{PREFIX}{hashlib.sha256(conversation_id.encode()).hexdigest()[:32]}:"


def new_key(conversation_id: str) -> str:
    return f"{conversation_prefix(conversation_id)}{uuid.uuid4().hex}"


def put(redis_client: Any, key: str, fields: dict[str, str]) -> None:
    redis_client.hset(key, mapping=fields)
    redis_client.expire(key, JOB_RECORD_TTL_S)


def get(redis_client: Any, key: str) -> dict[str, str]:
    """The stored fields, or {} when the text is gone (forgotten or expired)."""
    if not key:
        return {}
    raw = redis_client.hgetall(key) or {}
    return {
        (k.decode() if isinstance(k, bytes) else str(k)): (v.decode() if isinstance(v, bytes) else str(v))
        for k, v in raw.items()
    }


def replace_if_present(redis_client: Any, key: str, fields: dict[str, str], drop: tuple[str, ...] = ()) -> bool:
    """Write ``fields`` (and drop ``drop``) only while the key exists, atomically.
    A forget erases the key while a job runs; a plain write afterwards would
    bring back what was forgotten. Returns whether the write happened."""
    import redis as redis_lib

    with redis_client.pipeline() as pipe:
        try:
            pipe.watch(key)
            if not pipe.exists(key):
                pipe.unwatch()
                return False
            pipe.multi()
            pipe.hset(key, mapping=fields)
            if drop:
                pipe.hdel(key, *drop)
            pipe.execute()
            return True
        except redis_lib.WatchError:
            return False


def discard(redis_client: Any, key: str) -> None:
    if key:
        redis_client.delete(key)


def drop_fields(redis_client: Any, key: str, *fields: str) -> None:
    if key and fields:
        redis_client.hdel(key, *fields)


def forget_conversation(redis_client: Any, conversation_id: str) -> int:
    """Erase every stored text of one conversation. Returns how many keys went."""
    keys = list(redis_client.scan_iter(match=f"{conversation_prefix(conversation_id)}*", count=500))
    return int(redis_client.delete(*keys) or 0) if keys else 0
