# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""One asyncio primitive per running event loop.

An ``asyncio.Semaphore`` or ``Lock`` binds to the first loop that has to wait
on it; from then on, any other loop that waits on it raises "is bound to a
different event loop". This process runs more than one loop (uvicorn's,
``core.utils.async_bridge``'s, ``asyncio.run`` in sync callers, and one per
pytest test), so a primitive created at module scope fails whichever caller
contends for it second. ``scripts/lint-no-module-asyncio-primitives.py``
forbids that form; declare the primitive through ``LoopLocal`` instead::

    _ingest_semaphore = LoopLocal(lambda: asyncio.Semaphore(config.INGEST_CONCURRENCY))

    async with _ingest_semaphore.get():
        ...

Within one loop every caller shares one instance, so a cap or a lock means
what it says. Instances are held weakly, so a loop's primitive goes with it.
"""

from __future__ import annotations

import asyncio
import weakref
from collections.abc import Callable
from typing import Generic, TypeVar

T = TypeVar("T")


class LoopLocal(Generic[T]):
    def __init__(self, factory: Callable[[], T]) -> None:
        self._factory = factory
        self._by_loop: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, T] = weakref.WeakKeyDictionary()

    def get(self) -> T:
        """The running loop's instance, created on first use. Call from a coroutine."""
        loop = asyncio.get_running_loop()
        value = self._by_loop.get(loop)
        if value is None:
            value = self._by_loop[loop] = self._factory()
        return value
