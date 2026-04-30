# Eval Baselines

> **Workstream E Phase 1.** Tracks retrieval-quality + LLM-judge baselines
> across pipeline phases so every later change reports a delta against
> the same yardstick.

## Two gates, two baselines

| Gate | Baseline file | What it scores | When it fires |
|---|---|---|---|
| **RAGAS LLM-judge** | `src/mcp/tests/eval/baselines/ragas.json` | faithfulness, context_precision, answer_relevancy, context_recall | Nightly + `workflow_dispatch` (CI `ragas-eval` job) |
| **Retrieval IR metrics** | `src/mcp/tests/eval/baselines/retrieval.json` | Recall@10, MRR, NDCG@10, NDCG@5, Precision@5 | PR-time when `core/retrieval/**` or `core/agents/query_agent.py` change (live test in `tests/eval/test_retrieval_baselines.py`) |

The structural shape of both baseline files is checked on every PR via the structural tests in those files — they fail loudly if the JSON shape drifts even when no live infrastructure is available.

## Workstream E phase ledger

Every retrieval-side change records a row here so we can attribute lift (or regression) honestly:

| Date | Phase | Commit | Recall@10 | MRR | NDCG@10 | Faithfulness | Notes |
|---|---|---|---|---|---|---|---|
| 2026-04-28 | 0 (baseline) | _pending_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ | Initial capture; populate via `make eval-retrieval` after a clean stack boot. |

When the baseline is captured the row's "Notes" column is updated to record the model versions in play (embedding model, reranker, LLM internal model) so future regressions can be attributed to the right knob.

## Populating the retrieval baseline (first time)

Pre-flight:

```bash
docker compose up -d   # bring up Chroma + Neo4j + Redis + MCP
docker compose exec mcp-server bash
# Inside the container:
PYTHONPATH=src/mcp python -m tests.eval.test_retrieval_baselines
```

The script runs the harness against `app/eval/benchmark.jsonl`, captures the per-metric averages, and overwrites `tests/eval/baselines/retrieval.json` with the new numbers + today's date. Review the diff:

```bash
git diff src/mcp/tests/eval/baselines/retrieval.json
```

If the numbers look reasonable (Recall@10 > 0.5 on a healthy 20-doc corpus is a sane floor; less means either the benchmark is harder than the corpus or the retrieval pipeline is broken — investigate before committing), commit the baseline:

```bash
git add src/mcp/tests/eval/baselines/retrieval.json docs/EVAL_BASELINES.md
git commit -m "chore(eval): capture Phase 0 retrieval baseline"
```

## Populating the RAGAS baseline (first time)

Pre-flight: `OPENROUTER_API_KEY` set in the env (the gate hits a real LLM judge).

```bash
cd src/mcp
OPENROUTER_API_KEY=sk-... python -m pytest tests/eval/ragas_eval.py -v
```

The first run with no baseline returns scores; capture them by running the same module's `save_baselines()` helper. See `src/mcp/tests/eval/ragas_eval.py:24` for the harness contract.

## Regression threshold

Both baseline files carry a `regression_threshold` field (default `0.02`). The live tests fail when any metric falls below `baseline - threshold` absolute. To loosen during a deliberate trade-off (e.g. swapping in a faster but slightly noisier reranker), edit the threshold in the same PR that lands the change and call out the rationale in the commit message.

A regression that lands accidentally is the symptom the gate exists to catch; do not raise the threshold to silence it without root-causing first.

## When to re-baseline

Re-capture (overwrite the baseline) when:

1. Embedding model or version changes (Phase 5c migration; bumps `EMBEDDING_MODEL_VERSION`).
2. Reranker model changes (`RERANKER_MODEL`).
3. The benchmark JSONL file changes shape (new domain, new query format).

Do NOT re-capture when:

1. Random pipeline noise — investigate the regression first.
2. A retrieval-algorithm change that's *expected* to lift — capture the new (better) baseline AFTER the change lands, in a follow-up commit. This way the next phase's regression check uses the lifted floor.

## Expanding the benchmark

The current `app/eval/benchmark.jsonl` carries 5 starter queries and `relevant_ids: []` (recall is therefore not meaningful for them). Phase 1's deferred follow-up: a RAGAS testset generator (`app/eval/testset.py`) that auto-expands the benchmark by sampling from a frozen public corpus. Until that ships, expand the benchmark by hand:

```jsonl
{"query": "natural language description of the question",
 "relevant_ids": ["artifact_id_1", "artifact_id_2"],
 "domain": "code"}
```

Treat `relevant_ids` as the gold judgment — populate by querying the live KB, picking the top-3 truly relevant artifacts, and checking their IDs into the benchmark. This is the labour-intensive part; aim for ~20 queries per release cycle until the auto-generator ships.

## See also

- Driver doc: `tasks/2026-04-28-workstream-e-rag-modernization.md` (Phase 1)
- Retrieval test gate: `src/mcp/tests/eval/test_retrieval_baselines.py`
- LLM-judge gate: `src/mcp/tests/eval/ragas_eval.py`
- IR primitives: `src/mcp/app/eval/metrics.py`
- Harness: `src/mcp/app/eval/harness.py`
