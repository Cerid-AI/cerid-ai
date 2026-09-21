# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""A degradation must survive quiet periods.

F343: ``degraded`` was ANDed with ``age <= 900s``, so a permanently broken lane
reported healthy again fifteen minutes after the last call. The rerank outage is
a configuration failure — quenchforge 503s every single request — yet it was
reported through a channel that decays on silence. On a low-traffic instance the
whole block went green while the backend was still hard-failing.
"""

from __future__ import annotations

import time

import core.utils.inference_health as ih


def setup_function(_fn: object) -> None:
    ih.reset()


def test_degradation_survives_a_quiet_period() -> None:
    """Silence is not recovery — only a success clears a degradation."""
    ih.record_fallback(
        "rerank", configured="quenchforge", served_by="onnx",
        detail="no rerank slot configured",
    )
    with ih._LOCK:
        ih._EVENTS["rerank"]["last_event_ts"] = time.time() - 86_400  # a day of quiet

    snap = ih.snapshot()
    assert snap["rerank"]["degraded"] is True
    assert snap["rerank"]["detail"] == "no rerank slot configured"
    assert snap["rerank"]["age_s"] >= 86_000  # staleness is disclosed, not erased


def test_only_a_success_clears_a_stale_degradation() -> None:
    ih.record_fallback("rerank", configured="quenchforge", served_by="onnx")
    with ih._LOCK:
        ih._EVENTS["rerank"]["last_event_ts"] = time.time() - 86_400
    ih.record_success("rerank", provider="quenchforge")
    assert ih.snapshot()["rerank"]["degraded"] is False


def test_never_exercised_lane_is_unknown_not_optimistically_healthy() -> None:
    """Nothing has failed, but nothing has succeeded either — don't claim the
    configured provider is answering."""
    block = ih.annotate_block("rerank", {"provider": "quenchforge"})
    assert block["degraded"] is False
    assert block["serving"] == "unknown"
