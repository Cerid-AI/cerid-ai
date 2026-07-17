# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""LIVE-retrieval golden-query harness (Quality-Maximization Phase 0.1).

The nightly RAGAS gate scores PRE-BAKED golden contexts (``ragas_eval.py``
reads ``entry["contexts"]`` from ``golden_dataset.json``) — it never measures
live retrieval. This harness closes that gap: it self-seeds a deterministic
fixture corpus, then scores golden queries against **live** ``/query`` output
(the canonical ``agent_query_full`` path), reporting recall@5 / recall@10 /
MRR / nDCG@10 overall, per-domain, and per query-type.

Design notes
------------
* **Self-seeding** defeats the degenerate-corpus trap (invariants that passed
  only because the CI corpus was empty). Fixtures live in ``fixtures/*.md``;
  the seed manifest + gold mapping live in
  ``datasets/retrieval_golden_queries.json``.
* **Metrics** are the same IR primitives ``tests/eval/test_retrieval_baselines.py``
  uses — imported directly from ``app.eval.metrics`` (single source of truth).
* **Matching** is by returned source ``filename`` (the harness sets a stable
  ``eval-fixture-*`` filename at ingest; ``/query`` echoes it in ``sources[]``).
* Runs against the operator's LIVE personal instance: the real KB competes for
  top-k slots, which makes recall a realistic (harder) measurement, not a
  hermetic one.

Quality (recall/nDCG) is report-only by default — set
``RETRIEVAL_EVAL_MIN_RECALL5`` to gate on it. Corpus READINESS is not
report-only: if any seeded fixture is still not retrievable when the
readiness-poll budget (``--seed-ready-timeout-s``, default
``_live_eval_common.SEED_READY_TIMEOUT_S``) runs out, the harness refuses
to score (``failed: true`` in the results JSON, nonzero exit) rather than
silently reporting indexing lag as a recall regression — see
``decide_seed_failure``. Pass ``--allow-not-ready`` to restore the old
score-anyway behavior for diagnostics; the results JSON marks
``allow_not_ready: true`` so that run is never mistaken for a valid
baseline.

Run::

    cd src/mcp && ../../.venv/bin/python -m tests.eval.live_retrieval_eval
    # or via pytest (explicit path — not auto-collected by make test-eval):
    ../../.venv/bin/python -m pytest tests/eval/live_retrieval_eval.py -v
    # teardown any leftover fixtures without evaluating:
    ../../.venv/bin/python -m tests.eval.live_retrieval_eval --cleanup
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Any

import pytest

from app.eval.metrics import mrr, ndcg_at_k, recall_at_k
from tests.eval import _live_eval_common as common

# --- metric k-values (named so no bare literal appears in a comparison) ---
K5 = 5
K10 = 10
NDCG_K = 10
DEFAULT_TOP_K = 10
_ROUND = 4
_GATE_ENV = "RETRIEVAL_EVAL_MIN_RECALL5"
_RESULTS_PATH = common.OUT_DIR / "live_retrieval_results.json"

_METRIC_KEYS = ("recall@5", "recall@10", "mrr", "ndcg@10")

# --- exit codes (named so the CLI's nonzero contract is explicit) ---
EXIT_OK = 0
EXIT_GATE_FAILED = 1
EXIT_STACK_UNREACHABLE = 2
EXIT_SEED_NOT_READY = 3


def load_dataset() -> dict[str, Any]:
    path = common.DATASETS_DIR / "retrieval_golden_queries.json"
    with path.open() as f:
        data: dict[str, Any] = json.load(f)
    return data


def _score_query(
    ranked: list[str], expected: set[str]
) -> dict[str, float]:
    return {
        "recall@5": round(recall_at_k(ranked, expected, K5), _ROUND),
        "recall@10": round(recall_at_k(ranked, expected, K10), _ROUND),
        "mrr": round(mrr(ranked, expected), _ROUND),
        "ndcg@10": round(ndcg_at_k(ranked, expected, NDCG_K), _ROUND),
    }


def _mean_metrics(rows: list[dict[str, float]]) -> dict[str, float | None]:
    if not rows:
        return {k: None for k in _METRIC_KEYS}
    return {
        key: round(sum(r[key] for r in rows) / len(rows), _ROUND)
        for key in _METRIC_KEYS
    }


def _bucketed(
    per_query: list[dict[str, Any]], field: str
) -> dict[str, dict[str, float | None]]:
    buckets: dict[str, list[dict[str, float]]] = {}
    for row in per_query:
        buckets.setdefault(row[field], []).append(row["metrics"])
    return {name: _mean_metrics(rows) for name, rows in sorted(buckets.items())}


def decide_seed_failure(
    not_ready: list[str], *, allow_not_ready: bool
) -> tuple[bool, str | None]:
    """Pure pass/fail decision for the corpus-readiness poll outcome.

    Scoring golden queries against a corpus that isn't fully indexed yet
    measures ingest-indexing lag, not ranking quality (2026-07-13 baseline
    contamination: all 18 fixtures timed out, and the harness scored anyway).
    Returns ``(failed, reason)``: ``failed`` is True only when fixtures are
    still not retrievable at the poll deadline AND the ``--allow-not-ready``
    diagnostics escape hatch was not requested. ``reason`` names the
    offending fixture ids for the results JSON and CI logs.
    """
    if not_ready and not allow_not_ready:
        reason = (
            f"{len(not_ready)} seeded fixture(s) not retrievable at the "
            f"readiness-poll timeout: {sorted(not_ready)}"
        )
        return True, reason
    return False, None


def run_eval(
    client: Any,
    dataset: dict[str, Any],
    *,
    top_k: int = DEFAULT_TOP_K,
    max_queries: int | None = None,
    allow_not_ready: bool = False,
    seed_ready_timeout_s: float = common.SEED_READY_TIMEOUT_S,
) -> dict[str, Any]:
    """Seed the corpus, score every golden query against live ``/query``,
    and return the summary dict (also written to ``out/``).

    When the readiness poll times out with fixtures still not retrievable,
    scoring is skipped (``failed: true`` in the summary) unless
    ``allow_not_ready`` is set — see ``decide_seed_failure``.
    """
    corpus: list[dict[str, str]] = dataset["corpus"]
    queries: list[dict[str, Any]] = dataset["queries"]
    if max_queries is not None:
        queries = queries[:max_queries]

    common.seed_corpus(client, corpus)
    not_ready = common.wait_until_retrievable(
        client, corpus, timeout_s=seed_ready_timeout_s
    )
    failed, failure_reason = decide_seed_failure(
        not_ready, allow_not_ready=allow_not_ready
    )

    per_query: list[dict[str, Any]] = []
    if not failed:
        for q in queries:
            sources = common.query_ranked(
                client, q["query"], q["search_domains"], top_k=top_k
            )
            ranked = common.ranked_filenames(sources)
            expected = set(q["expected_docs"])
            metrics = _score_query(ranked, expected)
            per_query.append(
                {
                    "id": q["id"],
                    "query_type": q["query_type"],
                    "domain": q["domain"],
                    "expected_docs": q["expected_docs"],
                    "ranked_top5": ranked[:K5],
                    "metrics": metrics,
                }
            )

    overall = _mean_metrics([r["metrics"] for r in per_query])
    floor = common.gate_floor(_GATE_ENV)
    gate_passed: bool | None = None
    if not failed and floor is not None and overall["recall@5"] is not None:
        gate_passed = overall["recall@5"] >= floor

    summary: dict[str, Any] = {
        "harness": "live_retrieval_eval",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mcp_base": common.mcp_base(),
        "n_corpus_docs": len(corpus),
        "n_queries": len(per_query),
        "top_k": top_k,
        "overall": overall,
        "per_domain": _bucketed(per_query, "domain"),
        "per_query_type": _bucketed(per_query, "query_type"),
        "gate": {"min_recall@5": floor, "passed": gate_passed},
        "seed_not_ready": not_ready,
        "allow_not_ready": allow_not_ready,
        "failed": failed,
        "failure_reason": failure_reason,
        "per_query": per_query,
    }

    common.OUT_DIR.mkdir(parents=True, exist_ok=True)
    with _RESULTS_PATH.open("w") as f:
        json.dump(summary, f, indent=2)
    return summary


def _print_summary(summary: dict[str, Any]) -> None:
    """Human-readable digest to stderr; machine JSON to stdout."""
    ov = summary["overall"]
    print(
        f"\nLive-retrieval eval — {summary['n_queries']} queries over "
        f"{summary['n_corpus_docs']} fixture docs @ {summary['mcp_base']}",
        file=sys.stderr,
    )
    print("=" * 72, file=sys.stderr)
    print(
        f"OVERALL  recall@5={ov['recall@5']}  recall@10={ov['recall@10']}  "
        f"mrr={ov['mrr']}  ndcg@10={ov['ndcg@10']}",
        file=sys.stderr,
    )
    for scope_name, scope in (("per-domain", summary["per_domain"]),
                              ("per-type", summary["per_query_type"])):
        print(f"\n{scope_name}:", file=sys.stderr)
        for name, m in scope.items():
            print(
                f"  {name:14} recall@5={m['recall@5']}  recall@10={m['recall@10']}"
                f"  mrr={m['mrr']}  ndcg@10={m['ndcg@10']}",
                file=sys.stderr,
            )
    if summary["seed_not_ready"]:
        level = "WARNING (--allow-not-ready)" if summary["allow_not_ready"] else "FAILED"
        print(
            f"\n{level}: {len(summary['seed_not_ready'])} fixtures not "
            f"retrievable at timeout: {summary['seed_not_ready']}",
            file=sys.stderr,
        )
    if summary["failed"]:
        print(f"\n{summary['failure_reason']}", file=sys.stderr)
        print(
            "Scoring skipped — a not-ready corpus measures indexing lag, "
            "not retrieval quality. Re-run once ingest catches up, or pass "
            "--allow-not-ready for diagnostics only.",
            file=sys.stderr,
        )
    gate = summary["gate"]
    if gate["min_recall@5"] is not None:
        verdict = "PASS" if gate["passed"] else "FAIL"
        print(
            f"\nGATE {_GATE_ENV}={gate['min_recall@5']} -> {verdict}",
            file=sys.stderr,
        )
    print("=" * 72, file=sys.stderr)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Live-retrieval golden-query eval")
    parser.add_argument("--cleanup", action="store_true",
                        help="Delete seeded fixtures and exit (no evaluation).")
    parser.add_argument("--keep", action="store_true",
                        help="Leave fixtures seeded after the run (default tears them down).")
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--max-queries", type=int, default=None,
                        help="Cap number of queries (smoke/plumbing runs).")
    parser.add_argument(
        "--seed-ready-timeout-s", type=float, default=common.SEED_READY_TIMEOUT_S,
        help="Readiness-poll budget (seconds) before giving up on not-yet-"
             f"indexed fixtures (default {common.SEED_READY_TIMEOUT_S}).",
    )
    parser.add_argument(
        "--allow-not-ready", action="store_true",
        help="Diagnostics escape hatch: score anyway even when some "
             "fixtures are still not retrievable at the readiness-poll "
             "timeout. The results JSON marks allow_not_ready=true so this "
             "run can never be mistaken for a valid baseline.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)

    dataset = load_dataset()
    client = common.make_client()
    try:
        if not common.health_ok(client):
            print(f"Stack not reachable at {common.mcp_base()}", file=sys.stderr)
            return EXIT_STACK_UNREACHABLE
        if args.cleanup:
            n = common.cleanup_by_content(client, dataset["corpus"])
            print(f"Cleaned up {n} fixture artifacts.", file=sys.stderr)
            return EXIT_OK

        summary = run_eval(
            client, dataset, top_k=args.top_k, max_queries=args.max_queries,
            allow_not_ready=args.allow_not_ready,
            seed_ready_timeout_s=args.seed_ready_timeout_s,
        )
        _print_summary(summary)
        # Machine-readable JSON to stdout (no secrets in the summary).
        print(json.dumps(summary, indent=2))

        if summary["failed"]:
            return EXIT_SEED_NOT_READY

        gate = summary["gate"]
        return EXIT_GATE_FAILED if gate["passed"] is False else EXIT_OK
    finally:
        if not args.keep and not args.cleanup:
            common.cleanup_by_content(client, dataset["corpus"])
        client.close()


# ---------------------------------------------------------------------------
# Pytest entry (explicit-path collection; skips cleanly without a live stack)
# ---------------------------------------------------------------------------
@pytest.mark.eval
def test_live_retrieval_reports_or_gates() -> None:
    """End-to-end live-retrieval eval. Report-only unless
    ``RETRIEVAL_EVAL_MIN_RECALL5`` is set, in which case overall recall@5 is
    gated. Skips cleanly when the stack is unreachable."""
    if not common.resolve_api_key():
        pytest.skip("CERID_API_KEY not set — live-retrieval eval requires auth")
    dataset = load_dataset()
    client = common.make_client()
    try:
        if not common.health_ok(client):
            pytest.skip(f"Cerid stack not reachable at {common.mcp_base()}")
        summary = run_eval(client, dataset)
        _print_summary(summary)
        assert not summary["failed"], (
            f"seed not ready — refusing to trust this baseline: "
            f"{summary['failure_reason']}"
        )
        assert summary["n_queries"] > 0
        gate = summary["gate"]
        if gate["passed"] is not None:
            assert gate["passed"], (
                f"overall recall@5 {summary['overall']['recall@5']} "
                f"below floor {gate['min_recall@5']}"
            )
    finally:
        common.cleanup_by_content(client, dataset["corpus"])
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
