# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""The verification concurrency gates work on every event loop and still cap.

A module-level asyncio.Semaphore binds to the first loop that waits on it; a
second loop that then hit contention raised "is bound to a different event
loop", and each such verification task failed. Public CI lost two agreed-count
tests to it, depending only on which earlier test had contended first.
"""
from __future__ import annotations

import asyncio

import pytest

import config
from core.agents.hallucination.patterns import (
    _get_claim_verify_semaphore,
    _get_ext_verify_semaphore,
)

_GATES = [
    (_get_claim_verify_semaphore, "VERIFY_CLAIM_MAX_CONCURRENT"),
    (_get_ext_verify_semaphore, "EXTERNAL_VERIFY_MAX_CONCURRENT"),
]


async def _peak_concurrency(get_sem, capacity: int) -> int:
    active = peak = 0

    async def hold():
        nonlocal active, peak
        async with get_sem():
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1

    await asyncio.gather(*(hold() for _ in range(capacity + 3)))
    return peak


@pytest.mark.parametrize(("get_sem", "setting"), _GATES)
def test_contention_on_a_second_loop_does_not_raise(get_sem, setting):
    capacity = getattr(config, setting)

    first = asyncio.run(_peak_concurrency(get_sem, capacity))
    second = asyncio.run(_peak_concurrency(get_sem, capacity))

    assert first == capacity
    assert second == capacity


@pytest.mark.parametrize(("get_sem", "setting"), _GATES)
def test_one_loop_shares_one_gate(get_sem, setting):
    async def same_object_twice():
        return get_sem() is get_sem()

    assert asyncio.run(same_object_twice())
