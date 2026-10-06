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
against: `fixture` (the `.md` filename), `expected` (the proper-noun
entities the text contains — the live gate needs recall
`hits / len(expected) >= RECALL_FLOOR (0.8)` against the production
extractor's output, matched case-insensitively as a substring either way),
and `forbidden` (names the extractor should never emit — an exact
case-insensitive match against the extracted set fails the fixture
outright). The runner scores each fixture best-of-3 and prints all three
attempts with their spread, and before scoring anything it probes the
inference server (`OLLAMA_URL`: `/api/version` plus a 1-token chat, threshold
`CERID_RECALL_PROBE_MAX_MS`, default 3000) — on a loaded or unreachable
server it prints `recall SKIPPED: <reason>` and exits 0, which the beta tier
reports as NOT MEASURED rather than as the floor being met.

**`expected` follows the extractor's contract, read from the text.** An
entry is a proper noun of one of the `EntityType` kinds the prompt in
`core/agents/entity_extraction.py` defines — a named person, organisation,
product, project, place, titled work, standard or identifier (`RS256`,
`JWKS`, `Retry-After`), a cultivar, or a date written as a date — in the
exact surface form the fixture uses; common-noun concepts, features,
quantities, money, times of day and relative dates (`"late July"`) are not
entities under that contract and go in `forbidden` where the quantity rule
rejects them. The first live run of the gate (2026-10-06) scored 11 of 15
annotated fixtures below the floor with zero spread because the 2026-09
annotations expected concepts (`"basil"`, `"guest SSID"`, `"exponential
backoff"`, `"$85,000"`) the contract excludes, so the gate was measuring
the annotations' disagreement with the prompt rather than the extractor;
annotations are therefore written from the fixture text, never from what
the extractor happens to return, and
`test_every_expected_name_is_proper_noun_shaped` trips on the old shape.

**Coverage is enforced, not assumed.** `entity_recall_runner.uncovered_fixtures()`
(exercised by `test_every_fixture_is_scored_or_excluded`, which runs in plain
`pytest` — no live gateway needed) fails when any `fixtures/*.md` other than
this README has neither an `entities/*.json` annotation nor an entry in
`entity_recall_runner.UNANNOTATED`, so a new fixture can't silently go
unscored. Four fixtures are deliberately excluded rather than force-fit:

- `eval-fixture-notes-coffee-recipe.md` / `eval-fixture-notes-tea-recipe.md`
  — recipe prose with no named entity either model recalls stably. The 3B
  background model returns nothing (or the spurious literal `"Sentinel
  fact"` lifted from the fixture's own scaffolding line) and the 7B model
  returns bare quantity/prose fragments (`"twenty-two grams of coffee"`,
  `"sencha green tea"`) rather than names — there's no stable target for
  either `expected` or `forbidden`.
- `eval-fixture-projects-standup-cadence.md` — the text names nothing:
  `"Team Cadence"` is the note's title-cased heading, not a team the body
  ever calls by name, and every other candidate (`"9:30am"`, `"15
  minutes"`, `"morning"`) is a time or a duration. Its 2026-09 annotation
  expected `"Team Cadence"` and `"morning"` and scored 0.00.
- `eval-fixture-coding-deploy-pipeline.md` — the 3B extraction silently
  returns `[]` because a chunk's JSON response comes back malformed
  (`JSONDecodeError: Expecting value: line 49 column 17`) and the retry also
  fails to parse; `extract_entities_from_text` can't distinguish that from
  "no entities in this text" (see the `tasks/todo.md` item filed against
  `core/agents/entity_extraction.py`). Excluding the fixture avoids grading
  a known extractor defect as a fixture-quality problem.

**`RECALL_FLOOR` never moves.** The 2026-09 annotations were assembled from
candidate lists the 3B and 7B models returned for each fixture, with names
that proved unstable across repeated runs pruned from `expected`; that is
how concepts the prompt never asks for came to be expected, and it is the
method the contract paragraph above replaces. A `"Sentinel fact"`
`forbidden` entry was tried then and dropped because the 3B model emits it
intermittently, so a forbidden hit on it would flake a fixture whose recall
is otherwise solid; that holds.
