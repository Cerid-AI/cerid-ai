#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Benchmark the local NLI gate (``core.utils.nli.nli_score``) on LLM-AggreFact.

Quality-Maximization Phase 0.2 decision tool: the verification pipeline's KB
entailment gate rejects/accepts claims using ``NLI_ENTAILMENT_THRESHOLD``
against the ONNX ``cross-encoder/nli-deberta-v3-xsmall``-class model in
``core/utils/nli.py``, but that threshold has never been benchmarked against
a real factual-consistency dataset — it was tuned informally. This script
scores a fixed-seed sample of ``lytang/LLM-AggreFact`` (the aggregate
fact-verification benchmark behind the MiniCheck paper, EMNLP 2024) using the
repo's actual decision rule (``entailment >= NLI_ENTAILMENT_THRESHOLD ->
supported``) and reports balanced accuracy overall + per source sub-dataset.

NOT CI-wired — this is the Phase-3 decision tool for whether/how to swap in
a MiniCheck-class local verifier (tasks/2026-07-13-quality-maximization-program.md
item 3.1), not a nightly gate.

``lytang/LLM-AggreFact`` is a GATED HuggingFace dataset: pass an ``HF_TOKEN``
with access granted at https://huggingface.co/datasets/lytang/LLM-AggreFact,
or run ``hf auth login`` first. HF Hub calls (dataset + first-run NLI model
download) retry on rate-limit/transient errors with exponential backoff and
exit cleanly (no traceback, exit code 0) with an actionable message when the
Hub is unreachable or the token lacks access — this repo has seen HF-429
flakes in other eval harnesses (see ``scripts/nli_faithfulness_ablation.py``).

Usage::

    PYTHONPATH=src/mcp .venv/bin/python scripts/bench_nli_aggrefact.py \\
        [--n 500] [--seed 42] [--threshold 0.7] [--output PATH]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable, TypeVar

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src" / "mcp"))

DATASET_ID = "lytang/LLM-AggreFact"
DEFAULT_N = 500
DEFAULT_SEED = 42
_BATCH_SIZE = 16

T = TypeVar("T")


class AggreFactUnavailable(Exception):
    """Raised when the benchmark can't run — auth/gating or network failure.

    ``main()`` catches this and exits cleanly with a human-readable message
    instead of a traceback.
    """


# --------------------------------------------------------------------------
# HF Hub retry/backoff helpers
# --------------------------------------------------------------------------


def _is_rate_limited(exc: Exception) -> bool:
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status == 429:
        return True
    text = str(exc).lower()
    return "429" in text or "rate limit" in text or "too many requests" in text


def _is_transient(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(
        marker in text
        for marker in ("connection", "timed out", "timeout", "503", "502", "504", "temporarily unavailable")
    )


def _is_auth_or_gated(exc: Exception) -> bool:
    try:
        from datasets.exceptions import DatasetNotFoundError
        if isinstance(exc, DatasetNotFoundError):
            return True
    except ImportError:
        pass
    try:
        from huggingface_hub.errors import GatedRepoError, RepositoryNotFoundError
        if isinstance(exc, (GatedRepoError, RepositoryNotFoundError)):
            return True
    except ImportError:
        pass
    text = str(exc).lower()
    return any(marker in text for marker in ("gated", "authenticated", "401", "403"))


def _auth_message(exc: Exception) -> str:
    return (
        f"'{DATASET_ID}' is gated and requires an authenticated, access-granted HF "
        f"token. Request access at https://huggingface.co/datasets/{DATASET_ID}, "
        f"then run `hf auth login` (or set HF_TOKEN), and retry. "
        f"(underlying error: {exc})"
    )


def _retry_hf_call(fn: Callable[[], T], *, what: str, max_retries: int, base_backoff: float) -> T:
    """Run ``fn()``, retrying transient/rate-limited HF Hub failures.

    Auth/gating failures are terminal (not retryable) and re-raised as
    :class:`AggreFactUnavailable` immediately.
    """
    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            return fn()
        except AggreFactUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 -- classifying HF/network failure, not swallowing
            last_exc = exc
            if _is_auth_or_gated(exc):
                raise AggreFactUnavailable(_auth_message(exc)) from exc
            if attempt < max_retries and (_is_rate_limited(exc) or _is_transient(exc)):
                sleep_s = base_backoff * (2 ** (attempt - 1))
                print(
                    f"[bench-nli-aggrefact] transient error {what} "
                    f"(attempt {attempt}/{max_retries}): {exc}; retrying in {sleep_s:.0f}s",
                    file=sys.stderr,
                )
                time.sleep(sleep_s)
                continue
            raise AggreFactUnavailable(f"Could not complete {what}: {exc}") from exc
    raise AggreFactUnavailable(f"Exhausted retries for {what}: {last_exc}")


# --------------------------------------------------------------------------
# Dataset loading + NLI scoring
# --------------------------------------------------------------------------


def _fetch_rows(seed: int, n: int) -> list[dict[str, Any]]:
    from datasets import load_dataset

    ds = load_dataset(DATASET_ID, split="test")
    ds = ds.shuffle(seed=seed)
    if n:
        ds = ds.select(range(min(n, len(ds))))
    return [dict(row) for row in ds]


def _prime_nli_model() -> None:
    """Force ``core.utils.nli``'s lazy model load (and its HF download) once,
    up front, so it participates in the retry/backoff wrapper."""
    from core.utils.nli import nli_score

    nli_score("The sky is blue.", "The sky has a color.")


def score_rows(rows: list[dict[str, Any]], *, threshold: float) -> list[dict[str, Any]]:
    from core.utils.nli import batch_nli_score

    results: list[dict[str, Any]] = []
    for i in range(0, len(rows), _BATCH_SIZE):
        batch = rows[i : i + _BATCH_SIZE]
        pairs = [(str(r.get("doc", "")), str(r.get("claim", ""))) for r in batch]
        scores = batch_nli_score(pairs)
        for row, score in zip(batch, scores):
            results.append({
                "dataset": str(row.get("dataset", "unknown")),
                "label": bool(row.get("label")),
                "predicted": score["entailment"] >= threshold,
                "entailment": score["entailment"],
            })
    return results


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------


def balanced_accuracy(rows: list[dict[str, Any]]) -> float | None:
    """Average of sensitivity (recall on label=True) and specificity
    (recall on label=False). ``None`` when one class is absent — the
    standard LLM-AggreFact per-dataset metric."""
    pos = [r for r in rows if r["label"]]
    neg = [r for r in rows if not r["label"]]
    if not pos or not neg:
        return None
    sensitivity = sum(1 for r in pos if r["predicted"]) / len(pos)
    specificity = sum(1 for r in neg if not r["predicted"]) / len(neg)
    return round((sensitivity + specificity) / 2, 4)


def summarize(results: list[dict[str, Any]], *, threshold: float) -> dict[str, Any]:
    by_dataset: dict[str, list[dict[str, Any]]] = {}
    for r in results:
        by_dataset.setdefault(r["dataset"], []).append(r)

    per_dataset: dict[str, Any] = {}
    per_dataset_baccs: list[float] = []
    for name, rows in sorted(by_dataset.items()):
        bacc = balanced_accuracy(rows)
        n_pos = sum(1 for r in rows if r["label"])
        per_dataset[name] = {
            "n": len(rows),
            "n_pos": n_pos,
            "n_neg": len(rows) - n_pos,
            "balanced_accuracy": bacc,
        }
        if bacc is not None:
            per_dataset_baccs.append(bacc)

    accuracy = round(sum(1 for r in results if r["predicted"] == r["label"]) / len(results), 4) if results else 0.0

    return {
        "n": len(results),
        "threshold": threshold,
        "accuracy": accuracy,
        "balanced_accuracy_pooled": balanced_accuracy(results),
        "balanced_accuracy_macro_avg_per_dataset": (
            round(sum(per_dataset_baccs) / len(per_dataset_baccs), 4) if per_dataset_baccs else None
        ),
        "per_source_dataset": per_dataset,
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _run(args: argparse.Namespace) -> dict[str, Any]:
    rows = _retry_hf_call(
        lambda: _fetch_rows(args.seed, args.n),
        what=f"loading {DATASET_ID}",
        max_retries=args.max_retries,
        base_backoff=args.backoff,
    )
    if not rows:
        raise AggreFactUnavailable(f"{DATASET_ID} test split returned 0 rows for n={args.n}, seed={args.seed}")

    _retry_hf_call(
        _prime_nli_model,
        what="downloading the local NLI model",
        max_retries=args.max_retries,
        base_backoff=args.backoff,
    )

    import config

    threshold = args.threshold if args.threshold is not None else config.NLI_ENTAILMENT_THRESHOLD
    results = score_rows(rows, threshold=threshold)
    return summarize(results, threshold=threshold)


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark core.utils.nli against LLM-AggreFact")
    parser.add_argument("--n", type=int, default=DEFAULT_N, help="sample size (0 = full test split)")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="fixed shuffle seed for reproducibility")
    parser.add_argument("--threshold", type=float, default=None, help="override NLI_ENTAILMENT_THRESHOLD")
    parser.add_argument("--max-retries", type=int, default=5)
    parser.add_argument("--backoff", type=float, default=2.0, help="base seconds for exponential backoff")
    parser.add_argument("--output", type=Path, default=None, help="write results JSON to PATH")
    args = parser.parse_args()

    try:
        summary = _run(args)
    except AggreFactUnavailable as exc:
        print(f"[bench-nli-aggrefact] SKIPPED — {exc}", file=sys.stderr)
        return 0

    print(json.dumps(summary, indent=2))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
