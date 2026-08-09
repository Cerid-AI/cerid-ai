# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Sync → async bridge backed by a single persistent event loop thread.

ChromaDB's ``EmbeddingFunction.__call__`` contract is synchronous; the
reranker's local-ONNX fallback path is also sync. Both have ``async``
fast-paths to call out to the local quenchforge GPU daemon (for embed)
or sidecar (for rerank), which need a running event loop.

The previous pattern was a per-call ``ThreadPoolExecutor`` + fresh
``asyncio.new_event_loop`` for each invocation. That tears down the
loop on every embed call, which voids any cached ``httpx.AsyncClient``
state (the client is tagged to the creating loop and breaks the next
time). The visible symptoms were:

- repeated ``RuntimeError: Event loop is closed`` warnings on every
  call from ``quenchforge_client``;
- ~10× throughput regression vs the Go bench rate (5.7 req/s in Go
  vs ~0.5 req/s through the Python sync-bridge) because every call
  rebuilt the HTTPS client, redid the TCP setup, and lost connection
  keep-alive.

This module replaces that pattern with a single background-thread
event loop, started on first use and kept alive for the process
lifetime. Sync callers submit coroutines via
``run_coroutine_threadsafe`` and block on the resulting future. The
cached ``httpx.AsyncClient`` instances inside the async clients now
live as long as the loop, which means connection pools persist, TLS
handshakes amortise, and per-call overhead drops to the underlying
HTTP round-trip.

Usage::

    from core.utils.async_bridge import run_async

    def my_sync_callsite():
        return run_async(some_async_fn(args), timeout=30.0)

Thread safety: ``run_async`` is safe to call from any thread (the
sync-bridge thread, FastAPI worker threads, the main thread, …); the
underlying loop schedules them in arrival order.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any, Coroutine, TypeVar

T = TypeVar("T")

_loop: asyncio.AbstractEventLoop | None = None
_loop_thread: threading.Thread | None = None
_loop_lock = threading.Lock()


def _start_loop() -> asyncio.AbstractEventLoop:
    """Create the background event loop + thread. Idempotent."""
    global _loop, _loop_thread
    if _loop is not None and _loop.is_running():
        return _loop
    with _loop_lock:
        if _loop is not None and _loop.is_running():
            return _loop
        _loop = asyncio.new_event_loop()
        ready = threading.Event()

        def _runner() -> None:
            assert _loop is not None
            asyncio.set_event_loop(_loop)
            # Mark the loop ready so the spawning thread can return.
            _loop.call_soon(ready.set)
            _loop.run_forever()

        _loop_thread = threading.Thread(
            target=_runner,
            name="cerid-async-bridge",
            daemon=True,
        )
        _loop_thread.start()
        ready.wait(timeout=5.0)
        return _loop


def run_async(coro: Coroutine[Any, Any, T], *, timeout: float | None = 60.0) -> T:
    """Run an async coroutine to completion from synchronous code.

    Submits the coroutine to the persistent background event loop and
    blocks the calling thread until the coroutine completes or the
    timeout elapses. Re-raises any exception the coroutine raised so
    callers can handle errors with normal try/except.

    Parameters
    ----------
    coro:
        An awaitable coroutine. Must be a fresh coroutine; reusing one
        across calls is a programmer error (asyncio's contract).
    timeout:
        Wall-clock seconds before ``concurrent.futures.TimeoutError`` is
        raised. ``None`` disables the timeout. Default 60s — reasonable
        for embed / rerank / chat calls; raise for long-running ones.
    """
    loop = _start_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=timeout)


def shutdown() -> None:
    """Stop the background loop. Tests only; never call in production.

    Operators don't need this — the loop is daemon-thread-backed and
    exits with the process. Provided so unit tests can isolate state
    across runs when they need to.
    """
    global _loop, _loop_thread
    with _loop_lock:
        if _loop is None or not _loop.is_running():
            _loop = None
            _loop_thread = None
            return
        _loop.call_soon_threadsafe(_loop.stop)
        if _loop_thread is not None:
            _loop_thread.join(timeout=5.0)
        _loop = None
        _loop_thread = None
