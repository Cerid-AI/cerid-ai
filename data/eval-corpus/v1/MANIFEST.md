# Cerid AI Eval Corpus v1

**Workstream E Phase 1.2.** Frozen synthetic corpus for the retrieval-quality
regression gate. 20 self-authored Markdown documents covering 5 knowledge-worker
domains (coding, finance, projects, personal, general). All content is
public-domain, originally written for this corpus, with no third-party
attribution required.

## Layout

```
data/eval-corpus/v1/
├── MANIFEST.md              (this file)
├── coding/
│   ├── python-type-hints.md
│   ├── docker-networking.md
│   ├── postgres-indexing.md
│   └── git-workflow.md
├── finance/
│   ├── index-fund-investing.md
│   ├── budgeting.md
│   ├── ira-comparison.md
│   └── compound-interest.md
├── projects/
│   ├── agile-vs-waterfall.md
│   ├── risk-register.md
│   ├── stakeholder-communication.md
│   └── project-estimation.md
├── personal/
│   ├── time-blocking.md
│   ├── reading-habits.md
│   ├── sleep-hygiene.md
│   └── exercise-routine.md
└── general/
    ├── effective-writing.md
    ├── critical-thinking.md
    ├── communication-skills.md
    └── learning-techniques.md
```

## How the corpus pairs with `app/eval/benchmark.jsonl`

Each query in `benchmark.jsonl` has a `relevant_paths` field listing the
corpus files (relative to `data/eval-corpus/v1/`) that should appear in the
retrieval results. The Phase 1.2 harness adaptation
(`app/eval/harness.py:resolve_paths_to_artifact_ids`) looks up Neo4j
artifacts by filename to translate paths → artifact_ids at evaluation time.

Filenames stay stable across re-ingests; UUIDs do not — that's why the
gold judgment uses paths rather than artifact_ids.

## Seeding the corpus

```bash
./scripts/seed-eval-corpus.sh
```

The script ingests every Markdown file under `data/eval-corpus/v1/`
into the live cerid stack via the existing `/ingest_file` endpoint,
classifying each by its directory name. Idempotent — re-running on an
already-seeded corpus is a no-op (existing artifact hashes match).

## Capturing baselines

After seeding, run the harness to populate `tests/eval/baselines/retrieval.json`:

```bash
docker exec ai-companion-mcp bash -c \
  'cd /app && PYTHONPATH=/app python -m tests.eval.test_retrieval_baselines'
```

Or invoke `_capture_baselines_to_disk()` from `tests/eval/test_retrieval_baselines.py`
with PYTHONPATH set to `src/mcp/`.

## Versioning

The directory is suffixed with a version (`v1/`) so future expansions can
add a `v2/` without retroactively changing baselines. When swapping
versions, update `EMBEDDING_BASELINES.md` § Phase ledger with the new
corpus version + the captured baselines.

## License

CC0 — these documents are released to the public domain. They were written
specifically for this evaluation corpus and contain no third-party content.
Use, modify, and redistribute freely.

## Provenance

Authored 2026-05-03 as part of Workstream E Phase 1.2 (eval gold judgments).
Topics chosen to:
- Cover knowledge-worker domains broadly enough to test cross-domain retrieval
- Have factual claims specific enough that gold judgments are unambiguous
- Be long enough to chunk meaningfully (300-1000 words each)
- Avoid time-sensitive content that would age out the corpus quickly
