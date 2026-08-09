# Live-eval fixture corpus

Deterministic seed corpus for the **live-retrieval** (Phase 0.1) and
**chat-path faithfulness** (Phase 0.3) harnesses. These files exist to defeat
the repo's #1 historical eval failure: invariants that passed only because the
CI corpus was empty (degenerate-corpus trap). Every harness self-seeds this
corpus before measuring, so retrieval is always scored against real content.

## Layout

18 small markdown docs across **3 domains** — `coding`, `projects`, `notes`
(6 each). Filenames follow `eval-fixture-<domain>-<slug>.md`. The
`eval-fixture-` prefix namespaces them so they are trivially identifiable and
removable on the operator's live personal instance.

Domain assignment and the query→doc gold mapping live in
`../datasets/retrieval_golden_queries.json` (the `corpus` block), which is the
single source of truth the harnesses read — do not infer the domain from the
filename in code.

## Design rules baked into the content

- **Unique retrievable fact per doc.** Each note carries a distinctive
  "sentinel fact" (a specific number, name, or date) that a query can target.
- **Deliberate near-miss distractors.** Some docs share vocabulary with
  another doc but hold a different fact, to test ranker precision:
  - `coding-retry-policy` vs `coding-rate-limiter` (both about the Zephyr
    service, requests, "rate").
  - `notes-tea-recipe` vs `notes-coffee-recipe` (both about water temp / brew
    time / grams).
  - `projects-orion-scope` vs `projects-orion-budget` (both "Orion").
  - `notes-home-network` shares "rate-limited" with `coding-rate-limiter`
    (cross-domain lexical trap).
- **Temporal facts.** `projects-vega-launch`, `notes-garden-planting`, and
  `notes-book-summary` carry explicit dates for temporal queries.

## Idempotency & cleanup

Ingest is content-addressed (`artifact_id = sha256(content)`), so re-seeding
identical content is a no-op ("duplicate"). Each harness supports `--cleanup`
to delete the fixtures via `DELETE /admin/artifacts/{id}` (id recomputed from
local content), and tears them down after a run unless `--keep` is passed.
