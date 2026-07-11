# Eval Baselines — Published Headline Numbers

> Public excerpt of Cerid's evaluation ledger. Each row is a measured,
> dated result from an isolated harness (no user-KB contact). Retrieval
> IR metrics gate regressions in CI; RAGAS runs nightly.

## Memory — LongMemEval-S (full 500-item set)

**2026-07-10:** end-to-end QA accuracy **0.448** (224/500),
95% Wilson CI [0.405, 0.492]. gpt-4o reader + type-aware LLM judge,
production retrieval stack, three paired full-set runs (McNemar-validated
fix wave: +42 net vs the pre-fix arm, p < 0.001).

Per-type accuracy: single-session-assistant 0.839 · single-session-user
0.714 · preference 0.600 · knowledge-update 0.590 · temporal 0.256 ·
multi-session 0.218.

## Generation faithfulness — RAGAS (golden-50 dataset)

**2026-07-09:** faithfulness **0.92** (floor ≥ 0.90 — met),
context_precision **0.996**. Gated nightly in CI (`nightly-eval.yml`);
regressions below the floor fail the run.

## Hybrid retrieval fusion — weighted-sum vs tri-retrieval A/B

**2026-07-09 (re-measured 2026-07-10 on the Apache-2.0 sparse model
`Qdrant/Splade_PP_en_v1`):** on the isolated hermetic harness,
recall@5 parity at 1.000; MRR weighted_sum **1.000** vs tri_rrf 0.875
at small-corpus scale. Default stays `weighted_sum`; the Settings pane
recommends the SPLADE tri-retrieval leg once a corpus crosses ~100
documents, where learned-sparse synonym expansion is expected to pay off.

## Layout-aware parsing (default on since 2026-05-03)

Versus the legacy flat-text chunker, on the seeded eval corpus:
recall@10 0.842 → **0.858**, MRR 0.900 → **0.950**, NDCG@10
0.854 → **0.878**, precision@5 +0.088 — while *reducing* latency
(p50 −14%, p95 −5%, p99 −8%).

**2026-06-14 confirmatory re-run** (same harness, post RAG Quality
Program): recall@10 **0.933** / MRR **0.912** / NDCG@10 **0.905**,
faithfulness 0.90 — no regression against the published floor.
