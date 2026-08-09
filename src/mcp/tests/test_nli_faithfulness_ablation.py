# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for the NLI-faithfulness ablation harness
(`scripts/nli_faithfulness_ablation.py`).

The harness is a thin driver over the existing `faithfulness()` /
`faithfulness_llm()` metrics: it sweeps the NLI entailment threshold and the
claim-decomposition flag, plus an LLM-judge baseline (the "NLI OFF" arm). The
real metric calls are injected so these tests run with no NLI model / no LLM.
"""
from __future__ import annotations

import importlib.util

import pytest

from ._helpers import scripts_dir


def _load():
    sd = scripts_dir()
    if sd is None:
        pytest.skip("scripts/ dir not reachable from test env")
    spec = importlib.util.spec_from_file_location(
        "nli_faithfulness_ablation", sd / "nli_faithfulness_ablation.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_mean_basic():
    mod = _load()
    assert mod.mean([1.0, 2.0, 3.0]) == 2.0
    assert mod.mean([]) == 0.0


async def test_run_ablation_sweeps_thresholds_and_decomp():
    mod = _load()
    dataset = [{"x": 1}, {"x": 2}]
    calls: list[tuple[float, bool]] = []

    async def nli_score_all(threshold: float, decompose: bool) -> list[float]:
        calls.append((threshold, decompose))
        base = threshold + (0.1 if decompose else 0.0)
        return [base, base]

    judge_calls = {"n": 0}

    async def judge_score_all() -> list[float]:
        judge_calls["n"] += 1
        return [0.9, 0.9]

    res = await mod.run_ablation(
        dataset, [0.5, 0.7], nli_score_all=nli_score_all, judge_score_all=judge_score_all
    )

    assert res["n"] == 2
    assert set(res["nli_arm"].keys()) == {"0.5", "0.7"}
    assert res["nli_arm"]["0.5"]["decomp_off"] == 0.5
    assert res["nli_arm"]["0.5"]["decomp_on"] == 0.6
    assert res["nli_arm"]["0.7"]["decomp_off"] == 0.7
    assert res["llm_judge_baseline"] == 0.9
    # every threshold swept with both decomposition settings
    for t in (0.5, 0.7):
        assert (t, False) in calls
        assert (t, True) in calls
    # judge baseline is threshold-independent — computed exactly once
    assert judge_calls["n"] == 1


async def test_faithfulness_by_intent_groups_and_means():
    mod = _load()
    dataset = [
        {"query": "what is X", "ground_truth": "gt1", "contexts": ["c"]},
        {"query": "what is Y", "ground_truth": "gt2", "contexts": ["c"]},
        {"query": "how do I Z", "ground_truth": "gt3", "contexts": ["c"]},
    ]

    def classify(q: str) -> str:
        return "compiled_summary" if q.startswith("what is") else "procedural"

    async def score_entry(entry: dict) -> float:
        return {"gt1": 0.8, "gt2": 1.0, "gt3": 0.5}[entry["ground_truth"]]

    out = await mod.faithfulness_by_intent(dataset, classify=classify, score_entry=score_entry)

    assert out["compiled_summary"]["n"] == 2
    assert out["compiled_summary"]["faithfulness"] == 0.9  # mean(0.8, 1.0)
    assert out["procedural"]["n"] == 1
    assert out["procedural"]["faithfulness"] == 0.5


def test_format_results_markdown():
    mod = _load()
    results = {
        "n": 50,
        "nli_arm": {
            "0.5": {"decomp_off": 0.80, "decomp_on": 0.85},
            "0.7": {"decomp_off": 0.89, "decomp_on": 0.93},
        },
        "llm_judge_baseline": 0.91,
    }
    md = mod.format_results_markdown(results, judge_model="openrouter/openai/gpt-4o-mini")
    # threshold rows + the best decomp-on number present
    assert "0.7" in md
    assert "0.93" in md
    # the NLI-OFF baseline is reported
    assert "0.91" in md
    assert "gpt-4o-mini" in md
    # n is surfaced
    assert "50" in md
