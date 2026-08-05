# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""E1 Phase-1k verifiability harness — A2A TASK-CANCELLATION probe.

Audit: ``docs/superpowers/plans/2026-07-19-e1-remediation-program.md`` Phase 1.
Registry: ``docs/superpowers/specs/2026-07-17-audit-e1-findings-registry.jsonl``
(CR-034, the cancel half).

``create_task`` executed the skill INLINE (``await executor(request.input)``)
before returning, so ``cancel_task`` either 409'd on the already-terminal state or
set ``canceled`` which the still-running inline executor then overwrote with
``completed`` / ``failed`` — cancellation never actually stopped execution and
corrupted the peer's task-state machine.

The fix runs the executor as a registered, cancellable ``asyncio.Task``;
``cancel_task`` cancels it (cooperative cancellation) so the skill stops and the
canceled state survives.

This probe drives the REAL ``create_task`` / ``cancel_task`` with an in-memory task
store and a long-running skill, cancelling it mid-flight. RED-then-GREEN; GREEN ->
preservation gates.
"""
from __future__ import annotations

import asyncio

import pytest


def _install_store(monkeypatch):
    """Back the task lifecycle with an in-memory store (no Redis)."""
    import app.routers.a2a as a2a_mod

    store: dict[str, dict] = {}
    monkeypatch.setattr(a2a_mod, "_save_task", lambda t: store.update({t["id"]: dict(t)}))
    monkeypatch.setattr(a2a_mod, "_load_task",
                        lambda tid: dict(store[tid]) if tid in store else None)
    monkeypatch.setattr(a2a_mod, "_append_history", lambda *a, **k: None)
    return store


@pytest.mark.preservation
async def test_cancel_actually_stops_a_running_task(monkeypatch):
    """Cancelling a mid-flight A2A task must stop the executor and leave the task
    'canceled' — not let the still-running skill overwrite it with 'completed'.
    RED on HEAD: the inline executor is uncancellable (CR-034)."""
    import app.routers.a2a as a2a_mod
    store = _install_store(monkeypatch)

    started = asyncio.Event()
    observed = {"cancelled": False}

    async def _slow(_inp):
        started.set()
        try:
            await asyncio.sleep(1.0)  # long-running relative to the cancel
        except asyncio.CancelledError:
            observed["cancelled"] = True
            raise
        return {"ok": True}

    monkeypatch.setitem(a2a_mod.SKILL_MAP, "knowledge-query", _slow)

    from app.routers.a2a import A2ATaskRequest, cancel_task, create_task

    create_coro = asyncio.ensure_future(
        create_task(A2ATaskRequest(skill_id="knowledge-query", input={}))
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=2.0)  # executor is running
        task_id = next(iter(store))  # the single in-flight task, persisted 'working'
        cancel_result = await cancel_task(task_id)
        finished = await asyncio.wait_for(create_coro, timeout=3.0)
    finally:
        if not create_coro.done():
            create_coro.cancel()

    assert observed["cancelled"] is True, (
        "cancel did not stop the running executor — the skill ran to completion "
        "despite being canceled (CR-034)"
    )
    assert cancel_result["status"] == "canceled"
    assert finished["status"] == "canceled", (
        "create_task overwrote the canceled state with completed/failed — "
        "cancellation is not durable (CR-034)"
    )


@pytest.mark.preservation
async def test_normal_task_still_completes(monkeypatch):
    """Green anchor: a task whose skill finishes normally still reaches
    'completed' with its output — the cancellable path must not break the happy
    path."""
    import app.routers.a2a as a2a_mod
    _install_store(monkeypatch)

    async def _quick(_inp):
        return {"answer": 42}

    monkeypatch.setitem(a2a_mod.SKILL_MAP, "knowledge-query", _quick)

    from app.routers.a2a import A2ATaskRequest, create_task
    task = await create_task(A2ATaskRequest(skill_id="knowledge-query", input={}))

    assert task["status"] == "completed"
    assert task["output"] == {"answer": 42}


@pytest.mark.preservation
async def test_cancel_terminal_task_409(monkeypatch):
    """Cancelling an already-completed task still 409s — terminal states are
    immutable."""
    from fastapi import HTTPException

    import app.routers.a2a as a2a_mod
    _install_store(monkeypatch)

    async def _quick(_inp):
        return {"ok": True}

    monkeypatch.setitem(a2a_mod.SKILL_MAP, "knowledge-query", _quick)

    from app.routers.a2a import A2ATaskRequest, cancel_task, create_task
    task = await create_task(A2ATaskRequest(skill_id="knowledge-query", input={}))
    assert task["status"] == "completed"

    with pytest.raises(HTTPException) as exc:
        await cancel_task(task["id"])
    assert exc.value.status_code == 409
