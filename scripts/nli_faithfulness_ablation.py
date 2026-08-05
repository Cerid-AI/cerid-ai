#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""NLI-faithfulness ablation harness (GA Track-R C3).

Drives the existing `app.eval.ragas_metrics.faithfulness()` (NLI-based) over the
50-entry golden dataset, sweeping:

* the NLI entailment threshold ∈ {0.5, 0.6, 0.7, 0.8}, and
* claim decomposition OFF vs ON,

against an LLM-judge baseline (`faithfulness_llm`, the "NLI OFF" arm). The output
documents the causal link between NLI gating and the faithfulness number so the
verification claim is evidence-backed (closeout-plan Theme C / C3).

The metric calls are injected into `run_ablation` so the orchestration is unit-
testable without an NLI model or an LLM. The CLI wires the real metrics and routes
all LLM work (claim decomposition + the judge baseline) to a stable OpenRouter
judge — the NLI scorer runs locally on CPU.

Usage::

    PYTHONPATH=src/mcp .venv/bin/python scripts/nli_faithfulness_ablation.py \
        [--limit N] [--output PATH] [--judge-model openrouter/openai/gpt-4o-mini]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src" / "mcp"))

DEFAULT_THRESHOLDS = [0.5, 0.6, 0.7, 0.8]
DEFAULT_JUDGE_MODEL = "openrouter/openai/gpt-4o-mini"
_CONCURRENCY = 4


# --------------------------------------------------------------------------
# Pure helpers (unit-tested)
# --------------------------------------------------------------------------


def mean(scores: list[float]) -> float:
    """Mean of a score list, rounded to 4 dp. Empty → 0.0."""
    return round(sum(scores) / len(scores), 4) if scores else 0.0


def _fmt_threshold(t: float) -> str:
    return f"{t:.1f}"


async def run_ablation(
    dataset: list[dict[str, Any]],
    thresholds: list[float],
    *,
    nli_score_all: Callable[[float, bool], Awaitable[list[float]]],
    judge_score_all: Callable[[], Awaitable[list[float]]],
) -> dict[str, Any]:
    """Sweep (threshold × decomposition) for the NLI arm + one judge baseline.

    `nli_score_all(threshold, decompose)` returns one faithfulness score per
    dataset entry under those settings. `judge_score_all()` returns the
    threshold-independent LLM-judge baseline scores (computed once).
    """
    nli_arm: dict[str, dict[str, float]] = {}
    for t in thresholds:
        decomp_off = mean(await nli_score_all(t, False))
        decomp_on = mean(await nli_score_all(t, True))
        nli_arm[_fmt_threshold(t)] = {"decomp_off": decomp_off, "decomp_on": decomp_on}
    baseline = mean(await judge_score_all())
    return {"n": len(dataset), "nli_arm": nli_arm, "llm_judge_baseline": baseline}


async def faithfulness_by_intent(
    dataset: list[dict[str, Any]],
    *,
    classify: Callable[[str], str],
    score_entry: Callable[[dict[str, Any]], Awaitable[float]],
) -> dict[str, dict[str, float]]:
    """Group golden entries by surface intent; mean faithfulness per intent.

    `classify(query) -> intent`; `score_entry(entry) -> faithfulness`. Returns
    `{intent: {"faithfulness": mean, "n": count}}` — the shape the soak's
    `metric_faithfulness` consumes per intent.
    """
    buckets: dict[str, list[float]] = {}
    for entry in dataset:
        intent = classify(entry["query"])
        score = await score_entry(entry)
        buckets.setdefault(intent, []).append(score)
    return {
        intent: {"faithfulness": mean(scores), "n": len(scores)}
        for intent, scores in buckets.items()
    }


def format_results_markdown(results: dict[str, Any], *, judge_model: str) -> str:
    """Render the ablation result as a markdown table for EVAL_BASELINES / writeup."""
    lines = [
        f"**NLI-faithfulness ablation** (n={results['n']}, golden dataset)",
        "",
        "| NLI entailment threshold | faithfulness (decomp OFF) | faithfulness (decomp ON) |",
        "|---|---|---|",
    ]
    for t, vals in results["nli_arm"].items():
        lines.append(f"| {t} | {vals['decomp_off']:.4f} | {vals['decomp_on']:.4f} |")
    lines.append("")
    lines.append(
        f"LLM-judge baseline (NLI OFF, `{judge_model}`): "
        f"**{results['llm_judge_baseline']:.4f}**"
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Real metric wiring (CLI path)
# --------------------------------------------------------------------------


def _load_dotenv() -> None:
    env_path = _REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


async def _bounded_gather(make_coros: list[Callable[[], Awaitable[float]]]) -> list[float]:
    sem = asyncio.Semaphore(_CONCURRENCY)

    async def _run(fn: Callable[[], Awaitable[float]]) -> float:
        async with sem:
            return await fn()

    return list(await asyncio.gather(*[_run(fn) for fn in make_coros]))


def _make_real_nli_score_all(dataset: list[dict[str, Any]]):
    async def nli_score_all(threshold: float, decompose: bool) -> list[float]:
        import config
        from app.eval.ragas_metrics import faithfulness

        config.NLI_ENTAILMENT_THRESHOLD = threshold
        config.FAITHFULNESS_DECOMPOSE_CLAIMS = decompose

        def _one(entry: dict[str, Any]) -> Callable[[], Awaitable[float]]:
            async def _score() -> float:
                r = await faithfulness(entry["ground_truth"], entry["contexts"])
                return r.score

            return _score

        return await _bounded_gather([_one(e) for e in dataset])

    return nli_score_all


def _make_real_judge_score_all(dataset: list[dict[str, Any]], judge_model: str):
    async def judge_score_all() -> list[float]:
        from app.eval.ragas_metrics import faithfulness_llm

        def _one(entry: dict[str, Any]) -> Callable[[], Awaitable[float]]:
            async def _score() -> float:
                r = await faithfulness_llm(
                    entry["ground_truth"], entry["contexts"], model=judge_model
                )
                return r.score

            return _score

        return await _bounded_gather([_one(e) for e in dataset])

    return judge_score_all


def _connect_redis():
    """Best-effort Redis client from REDIS_URL; None when unreachable."""
    try:
        import redis

        client = redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
        client.ping()
        return client
    except Exception:  # noqa: BLE001 — producer no-ops when redis is down
        return None


async def emit_faithfulness_by_intent(
    dataset: list[dict[str, Any]],
    redis_client: Any,
    *,
    threshold: float | None = None,
    decompose: bool = True,
) -> dict[str, dict[str, float]]:
    """Compute per-intent faithfulness over the golden set and write it to the
    `cerid:ragas:by_intent:<intent>` keys the soak collector reads."""
    import config
    from app.eval.ragas_metrics import faithfulness
    from core.retrieval.surface_router import classify_intent
    from core.utils.cache import record_faithfulness_by_intent

    if threshold is not None:
        config.NLI_ENTAILMENT_THRESHOLD = threshold
    config.FAITHFULNESS_DECOMPOSE_CLAIMS = decompose

    def classify(query: str) -> str:
        intent, _conf, _rationale = classify_intent(query)
        return intent

    async def score_entry(entry: dict[str, Any]) -> float:
        r = await faithfulness(entry["ground_truth"], entry["contexts"])
        return r.score

    by_intent = await faithfulness_by_intent(dataset, classify=classify, score_entry=score_entry)
    for intent, vals in by_intent.items():
        record_faithfulness_by_intent(
            redis_client, intent=intent, faithfulness=vals["faithfulness"], n=int(vals["n"])
        )
    return by_intent


async def _main_async(args: argparse.Namespace) -> int:
    _load_dotenv()
    # Route all eval LLM work (claim decomposition + judge baseline) to a stable
    # OpenRouter judge; the local .env points INTERNAL_LLM_MODEL at a quenchforge
    # model that OpenRouter rejects. NLI still runs locally on CPU.
    os.environ["INTERNAL_LLM_PROVIDER"] = "openrouter"
    os.environ["INTERNAL_LLM_MODEL"] = _strip_or_prefix(args.judge_model)

    from tests.eval.ragas_eval import load_golden_dataset

    dataset = load_golden_dataset()
    if args.limit:
        dataset = dataset[: args.limit]

    if args.emit_by_intent:
        redis_client = _connect_redis()
        by_intent = await emit_faithfulness_by_intent(dataset, redis_client)
        print(json.dumps({"by_intent": by_intent, "redis": redis_client is not None}, indent=2))
        if redis_client is None:
            print("# NOTE: Redis unreachable — computed but not persisted (run during soak with stack up)")
        return 0

    results = await run_ablation(
        dataset,
        args.thresholds,
        nli_score_all=_make_real_nli_score_all(dataset),
        judge_score_all=_make_real_judge_score_all(dataset, args.judge_model),
    )
    results["judge_model"] = args.judge_model

    md = format_results_markdown(results, judge_model=args.judge_model)
    print(json.dumps(results, indent=2))
    print("\n" + md)
    if args.output:
        Path(args.output).write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    return 0


def _strip_or_prefix(model: str) -> str:
    return model[len("openrouter/"):] if model.startswith("openrouter/") else model


def main() -> int:
    parser = argparse.ArgumentParser(description="NLI-faithfulness ablation (GA Track-R C3)")
    parser.add_argument("--limit", type=int, default=0, help="cap dataset size (0 = full)")
    parser.add_argument("--output", type=str, default="", help="write results JSON to PATH")
    parser.add_argument("--judge-model", type=str, default=DEFAULT_JUDGE_MODEL)
    parser.add_argument(
        "--emit-by-intent",
        action="store_true",
        help="compute per-intent faithfulness and write the soak metric to Redis",
    )
    parser.add_argument(
        "--thresholds",
        type=lambda s: [float(x) for x in s.split(",")],
        default=DEFAULT_THRESHOLDS,
    )
    args = parser.parse_args()
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
