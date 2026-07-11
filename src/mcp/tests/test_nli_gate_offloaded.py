# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""The retrieval NLI gate must run OFF the event loop (2026-07-10).

Root cause of the MCP watchdog crashes (2026-07-08 deferred, reproduced
2026-07-10 18:44Z): ``batch_nli_score`` — DeBERTa ONNX inference over up
to 15 (doc, query) pairs — was called synchronously inside the async
query pipeline. Under CPU contention that blocks the loop past the 45s
watchdog, which force-exits the process mid-request. This pins the
off-loop contract.
"""

from __future__ import annotations

import threading

import pytest


@pytest.mark.asyncio
async def test_batch_nli_score_runs_off_the_event_loop(monkeypatch):
    import core.utils.nli as nli_mod
    from core.agents.query_agent import _nli_gate_scores

    loop_thread = threading.get_ident()
    seen: dict = {}

    def fake_batch(pairs):
        seen["thread"] = threading.get_ident()
        return [{"entailment": 0.9, "contradiction": 0.0} for _ in pairs]

    monkeypatch.setattr(nli_mod, "batch_nli_score", fake_batch)
    scores = await _nli_gate_scores([("doc", "query")] * 3)

    assert len(scores) == 3
    assert seen["thread"] != loop_thread, (
        "batch_nli_score ran ON the event-loop thread — ONNX inference "
        "in-loop is the documented watchdog-crash root cause"
    )
