# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""A dispatched memory-promotion task survives garbage collection until it ends.

The event loop holds tasks only weakly. A fire-and-forget promotion task that
nothing else references can be collected mid-flight, losing the promotion. The
pipeline is driven for real; only claim extraction, claim verification and the
promoter itself are stand-ins.
"""
from __future__ import annotations

import asyncio
import gc
import weakref
from typing import Any

import pytest

_RESPONSE = (
    "The sky is blue during the daytime because molecules in the atmosphere "
    "scatter blue light more strongly than red light across the sky overhead."
)
_CLAIM = "The sky is blue during the daytime."


class _Redis:
    def setex(self, *a: Any, **k: Any) -> None:
        return None

    def get(self, *a: Any, **k: Any) -> None:
        return None

    def setnx(self, *a: Any, **k: Any) -> bool:
        return True

    def expire(self, *a: Any, **k: Any) -> bool:
        return True

    def rpush(self, *a: Any, **k: Any) -> int:
        return 1

    def zadd(self, *a: Any, **k: Any) -> int:
        return 1

    def ltrim(self, *a: Any, **k: Any) -> bool:
        return True


def _patch_pipeline(monkeypatch: pytest.MonkeyPatch, started: list[asyncio.Task[Any]]) -> None:
    from core.agents.hallucination import streaming

    async def _extract(response_text: str, user_query: str | None = None) -> tuple[list[str], str]:
        return [_CLAIM], "heuristic"

    async def _verify(*a: Any, **k: Any) -> dict[str, Any]:
        return {
            "status": "verified", "confidence": 0.9, "similarity": 0.9,
            "nli_entailment": 0.9, "nli_contradiction": 0.05,
            "source_artifact_id": "art-1", "source_filename": "doc.md",
            "reason": "supported by KB", "verification_method": "kb_nli",
        }

    async def _promote(*a: Any, **k: Any) -> dict[str, int]:
        started.append(asyncio.current_task())  # type: ignore[arg-type]
        # A future nothing else holds: while the task waits on it, the only
        # path to the task is whatever the pipeline kept.
        await asyncio.get_running_loop().create_future()
        return {}

    monkeypatch.setattr("config.ENABLE_VERIFIED_MEMORY_PROMOTION", True)
    monkeypatch.setattr("core.forget.registry.is_forgotten", lambda kind, id: False)
    monkeypatch.setattr(streaming, "extract_claims", _extract)
    monkeypatch.setattr(streaming, "_extract_claims_heuristic", lambda text: [_CLAIM])
    monkeypatch.setattr(streaming, "_resolve_pronouns_heuristic", lambda claims, *a, **k: claims)
    monkeypatch.setattr(streaming, "verify_claim", _verify)

    async def _no_context(*a: Any, **k: Any) -> None:
        return None

    async def _no_kb(*a: Any, **k: Any) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(streaming, "_extract_response_context", _no_context)
    monkeypatch.setattr("core.agents.query_agent.lightweight_kb_query", _no_kb)
    monkeypatch.setattr("core.agents.verified_memory.promote_verified_facts", _promote)


async def _assert_task_survives_gc(started: list[asyncio.Task[Any]]) -> None:
    await asyncio.sleep(0)  # let the dispatched task start
    assert len(started) == 1, "promotion was not dispatched"
    ref = weakref.ref(started.pop())
    gc.collect()
    task = ref()
    assert task is not None, "in-flight promotion task was garbage-collected"
    assert not task.done()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)  # done callbacks run on the next loop pass

    from core.agents.hallucination import streaming
    assert task not in streaming._promotion_tasks, "finished task was not released"


async def test_check_hallucinations_keeps_its_promotion_task_alive(monkeypatch):
    from core.agents.hallucination import streaming

    started: list[asyncio.Task[Any]] = []
    _patch_pipeline(monkeypatch, started)
    await streaming.check_hallucinations(
        response_text=_RESPONSE, conversation_id="c-live",
        chroma_client=object(), neo4j_driver=object(), redis_client=_Redis(),
        create_memory_fn=lambda *_a: "mem-1",
    )
    await _assert_task_survives_gc(started)


async def test_verify_stream_keeps_its_promotion_task_alive(monkeypatch):
    from core.agents.hallucination import streaming

    started: list[asyncio.Task[Any]] = []
    _patch_pipeline(monkeypatch, started)
    stream = streaming.verify_response_streaming(
        response_text=_RESPONSE, conversation_id="c-live",
        chroma_client=object(), neo4j_driver=object(), redis_client=_Redis(),
        create_memory_fn=lambda *_a: "mem-1",
    )
    async for _event in stream:
        pass
    # Once the SSE response is done the generator, and its frame's local
    # reference to the task, is gone; reproduce that before collecting.
    await stream.aclose()
    del stream
    await _assert_task_survives_gc(started)
