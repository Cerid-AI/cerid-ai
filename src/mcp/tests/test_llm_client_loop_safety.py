# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for the OpenRouter singleton httpx client's loop-safety.

Background (2026-04-22 beta test): `core.utils.contextual._run_coro_isolated`
runs sync ingestion code by spinning up a throwaway event loop inside a
ThreadPoolExecutor worker. Before the fix, that throwaway loop was the
first to call `_get_client()` — binding the module-level singleton httpx
client to the throwaway loop. The worker thread exited, the loop closed,
and every later verification request on the main FastAPI loop failed with
``RuntimeError: Event loop is closed``. The user saw
"External verification failed: Event loop is closed" for every chunk.

The fix: `_get_client()` refuses to cache the singleton when called from
any thread other than the main thread. Worker threads get a one-shot
client that is closed on context-manager exit.

These tests assert the invariant directly — the signature of the bug is
the singleton state persisting across a worker-thread call.
"""
from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from core.utils import llm_client


@pytest.fixture(autouse=True)
def reset_singleton_between_tests():
    """Ensure each test starts with a clean module-level singleton state,
    and that the global asyncio event-loop policy is unchanged afterwards.

    Prior versions of this file called `asyncio.run()` directly, which
    implicitly resets the loop policy and breaks other tests that rely on
    `asyncio.get_event_loop()` (e.g. test_workflows, test_tools) running
    later in the same pytest session.
    """
    llm_client._client = None
    llm_client._client_loop = None
    saved_loop = None
    try:
        saved_loop = asyncio.get_event_loop_policy().get_event_loop()
    except RuntimeError:
        saved_loop = None
    yield
    llm_client._client = None
    llm_client._client_loop = None
    if saved_loop is not None and not saved_loop.is_closed():
        asyncio.set_event_loop(saved_loop)


def _run_on_isolated_loop(coro):
    """Run a coroutine on a dedicated new loop without touching the policy's
    current-loop state. Equivalent to `asyncio.run` but without the
    policy-level side effects that leak into sibling tests.
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _call_get_client_in_new_loop_in_worker_thread() -> tuple[str, bool]:
    """Simulate `contextual._run_coro_isolated`: fresh loop in a worker thread."""
    def _runner() -> tuple[str, bool]:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            client = loop.run_until_complete(llm_client._get_client())
            is_singleton = client is llm_client._client
            return ("got-client", is_singleton)
        finally:
            loop.close()

    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(_runner).result()


def test_worker_thread_call_does_not_poison_singleton():
    """The contextual-chunking path (throwaway loop in a worker thread)
    must NOT bind the module-level singleton. If it did, the next call on
    the main FastAPI loop would hit a dead loop.
    """
    # Before:
    assert llm_client._client is None, "singleton should be unset at test start"
    assert llm_client._client_loop is None

    result, is_singleton = _call_get_client_in_new_loop_in_worker_thread()
    assert result == "got-client"
    assert is_singleton is False, (
        "Worker-thread caller received the singleton. This is the bug — a "
        "throwaway loop would bind the singleton and kill it on thread exit. "
        "The worker should get a one-shot client instead."
    )

    # After: singleton must remain untouched.
    assert llm_client._client is None, (
        "Singleton was set by a worker-thread call. _get_client() must only "
        "cache when called from the main thread."
    )
    assert llm_client._client_loop is None


def test_main_thread_call_caches_singleton():
    """Sanity: on the main thread (where uvicorn owns the persistent loop)
    the singleton IS cached and re-used for repeat calls on the same loop."""
    assert threading.current_thread() is threading.main_thread()

    async def two_calls():
        c1 = await llm_client._get_client()
        c2 = await llm_client._get_client()
        return c1, c2

    c1, c2 = _run_on_isolated_loop(two_calls())
    assert c1 is c2, (
        "Two sequential calls on the same main-thread loop should share the singleton"
    )

    # New loop → owner-loop mismatch. _get_client should recycle to a fresh
    # singleton bound to the new loop, NOT return the dead one.
    c3 = _run_on_isolated_loop(llm_client._get_client())
    assert c3 is not c1, (
        "After owner-loop mismatch on main thread, _get_client() should "
        "create a fresh singleton — not return the dead one."
    )


def test_acquire_client_closes_one_shot_in_worker_thread():
    """Worker-thread users of _acquire_client() must see their one-shot
    client closed on context exit, preventing fd leaks when the throwaway
    loop dies."""
    captured: dict = {}

    def _runner() -> None:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)

            async def inner():
                async with llm_client._acquire_client() as client:
                    captured["client"] = client
                    captured["closed_inside"] = client.is_closed

            loop.run_until_complete(inner())
        finally:
            loop.close()

    with ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(_runner).result()

    assert captured["closed_inside"] is False, "client was closed before context exit"
    assert captured["client"].is_closed is True, (
        "One-shot client was not closed after leaving the context manager. "
        "This would leak connections every time contextual chunks run."
    )
