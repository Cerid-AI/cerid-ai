# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Every caller of the NLI coalescer gets an answer while a flush is running.

F122: ``submit`` cancelled the timer task on a force-flush without checking
whether that task had already drained a batch and was waiting on inference.
Cancelling it there left the drained batch's futures unresolved, so those
callers waited forever. The same stale reference also meant a pair submitted
during a running flush started no timer of its own.

Only ``batch_nli_score`` (the ONNX model boundary) is replaced; the batcher
is the real one.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest

from core.utils import nli

_ENTAIL = {"contradiction": 0.0, "entailment": 1.0, "neutral": 0.0, "label": "entailment"}


class _BlockingModel:
    """Stands in for the model; the first batch blocks until released."""

    def __init__(self) -> None:
        self.first_batch_started = threading.Event()
        self.release_first_batch = threading.Event()
        self.batch_sizes: list[int] = []
        self._guard = threading.Lock()

    def __call__(self, pairs: list[tuple[str, str]]) -> list[dict[str, Any]]:
        with self._guard:
            is_first = not self.batch_sizes
            self.batch_sizes.append(len(pairs))
        if is_first:
            self.first_batch_started.set()
            self.release_first_batch.wait(timeout=10)
        return [dict(_ENTAIL) for _ in pairs]


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch):
    fake = _BlockingModel()
    monkeypatch.setattr(nli, "batch_nli_score", fake)
    monkeypatch.setattr(nli, "_batchers", {})
    yield fake
    fake.release_first_batch.set()


async def _until_first_batch_is_running(model: _BlockingModel) -> None:
    started = await asyncio.to_thread(model.first_batch_started.wait, 5)
    assert started, "the first batch never reached the model"


async def test_force_flush_does_not_strand_the_batch_already_in_flight(
    model: _BlockingModel,
) -> None:
    first = asyncio.ensure_future(nli.nli_score_async("premise", "first"))
    await _until_first_batch_is_running(model)

    # Enough new pairs to hit the max batch while the first is still running.
    forced = [
        asyncio.ensure_future(nli.nli_score_async("premise", f"h{i}"))
        for i in range(nli._COALESCE_MAX_BATCH)
    ]
    forced_results = await asyncio.wait_for(asyncio.gather(*forced), timeout=5)
    model.release_first_batch.set()

    first_result = await asyncio.wait_for(first, timeout=5)

    assert first_result["label"] == "entailment"
    assert len(forced_results) == nli._COALESCE_MAX_BATCH
    assert model.batch_sizes == [1, nli._COALESCE_MAX_BATCH]


async def test_pair_submitted_during_a_flush_is_flushed_on_its_own_timer(
    model: _BlockingModel,
) -> None:
    first = asyncio.ensure_future(nli.nli_score_async("premise", "first"))
    await _until_first_batch_is_running(model)

    late = asyncio.ensure_future(nli.nli_score_async("premise", "late"))
    # The late pair must resolve while the first batch is still blocked.
    late_result = await asyncio.wait_for(late, timeout=5)
    assert not first.done()
    model.release_first_batch.set()

    assert late_result["label"] == "entailment"
    assert (await asyncio.wait_for(first, timeout=5))["label"] == "entailment"
