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

## Entity-extraction recall annotations

`entities/eval-fixture-<slug>.json` pairs a fixture with the recall spec
`entity_recall_runner.py` and `test_entity_extraction_recall.py` score it
against: `fixture` (the `.md` filename), `expected` (names a human would
count as real entities in that doc — the live gate needs recall
`hits / len(expected) >= RECALL_FLOOR (0.8)` against the production
extractor's output, matched case-insensitively as a substring either way),
and `forbidden` (names the extractor should never emit — an exact
case-insensitive match against the extracted set fails the fixture
outright).

**Coverage is enforced, not assumed.** `entity_recall_runner.uncovered_fixtures()`
(exercised by `test_every_fixture_is_scored_or_excluded`, which runs in plain
`pytest` — no live gateway needed) fails when any `fixtures/*.md` other than
this README has neither an `entities/*.json` annotation nor an entry in
`entity_recall_runner.UNANNOTATED`, so a new fixture can't silently go
unscored. Three fixtures are deliberately excluded rather than force-fit:

- `eval-fixture-notes-coffee-recipe.md` / `eval-fixture-notes-tea-recipe.md`
  — recipe prose with no named entity either model recalls stably. The 3B
  background model returns nothing (or the spurious literal `"Sentinel
  fact"` lifted from the fixture's own scaffolding line) and the 7B model
  returns bare quantity/prose fragments (`"twenty-two grams of coffee"`,
  `"sencha green tea"`) rather than names — there's no stable target for
  either `expected` or `forbidden`.
- `eval-fixture-coding-deploy-pipeline.md` — the 3B extraction silently
  returns `[]` because a chunk's JSON response comes back malformed
  (`JSONDecodeError: Expecting value: line 49 column 17`) and the retry also
  fails to parse; `extract_entities_from_text` can't distinguish that from
  "no entities in this text" (see the `tasks/todo.md` item filed against
  `core/agents/entity_extraction.py`). Excluding the fixture avoids grading
  a known extractor defect as a fixture-quality problem.

**Annotations are built from both models, but graded against one.** Per the
`expected`-selection rule above, each annotation's candidate list came from
running the production extractor with both the 3B background model
(`extract_entities_from_text(..., llm_caller=default_llm_caller)`, the
model `run_one` actually calls in production for the `entity_extraction`
background stage) and the 7B chat model (via
`core.utils.internal_llm.llm_call_override`) against the fixture text. Only
the live 3B run is graded — `run_one` never overrides the model — so
`expected` keeps only names recurring across repeated 3B runs, not merely
names either model happened to return once.

**Pruning unstable names, not the floor.** `RECALL_FLOOR` never moves.
Where a name proved unstable across repeated live runs of the same fixture,
it was pruned from `expected` (or, for one fixture, its `forbidden` entry
was dropped) rather than accepted as a flaky pass/fail:

- `eval-fixture-coding-db-index.md` — `"events jsonb column"` matched the
  3B output on one run but not two others (`"events.jsonb"` instead);
  dropped, leaving `"GIN index"`, `"events table"`, `"audit-search"`, all
  three stable across every observed run. A `"Sentinel fact"` `forbidden`
  entry was tried and dropped for the same reason: the 3B model emits it
  intermittently, not reliably enough to gate on without flaking the
  fixture's own otherwise-solid recall.
- `eval-fixture-projects-standup-cadence.md` — `"daily standup"` matched
  one live run's output but was absent from another (`"nine-thirty"`
  appeared instead); replaced with `"Team Cadence"`, which recurred in
  every observed run alongside `"morning"`.
