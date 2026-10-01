# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""A merge the entailment guard could not check must not be applied.

The guard exists so a merged memory that drops a fact from either original is
refused. When the NLI model could not run, the resolver returned the merge
anyway and the caller stored the merged text in place of the new memory.

The LLM call and the NLI scorer are the two boundaries faked here.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from core.agents.memory import resolve_memory_conflict

_MERGE = json.dumps({
    "action": "merge",
    "reason": "overlapping facts",
    "merged_text": "The user works at Acme.",
})


def _resolve(monkeypatch: pytest.MonkeyPatch, nli) -> dict:
    async def _llm(*_a, **_kw) -> str:
        return _MERGE

    monkeypatch.setattr("core.agents.memory.call_internal_llm", _llm)
    monkeypatch.setattr("core.utils.nli.nli_score", nli)
    return asyncio.run(resolve_memory_conflict(
        "The user works at Acme as a staff engineer",
        {"memory_id": "m1", "text": "The user works at Acme in the Berlin office"},
    ))


def test_merge_is_refused_when_the_guard_cannot_run(monkeypatch):
    def _unavailable(_premise: str, _hypothesis: str) -> dict:
        raise RuntimeError("NLI model not loaded")

    result = _resolve(monkeypatch, _unavailable)

    assert result["action"] == "coexist", result
    assert result["merged_text"] is None, result


def test_merge_still_proceeds_when_the_guard_passes(monkeypatch):
    def _entailed(_premise: str, _hypothesis: str) -> dict:
        return {"entailment": 0.95, "neutral": 0.04, "contradiction": 0.01}

    result = _resolve(monkeypatch, _entailed)

    assert result["action"] == "merge", result
    assert result["merged_text"] == "The user works at Acme."
