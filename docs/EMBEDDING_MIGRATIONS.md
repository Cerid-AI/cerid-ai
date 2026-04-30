# Embedding Model Migrations

> **Workstream E Phase 5c.** This playbook documents the dual-collection
> pattern for migrating an existing ChromaDB collection to a new embedding
> model without downtime.

## When to use this

You need to migrate when:

- You change `EMBEDDING_MODEL` (e.g., `Snowflake/snowflake-arctic-embed-m-v1.5` → a newer/larger model).
- You change `EMBEDDING_DIMENSIONS` (e.g., enabling Matryoshka truncation from 768 → 256).
- The embedding model itself is updated upstream and you want to re-encode the corpus.

Vector spaces are **not interchangeable across models**: cosine similarity in one model's space is meaningless in another's. Don't mix; migrate.

## Why dual-collection

The naive approach — drop the old collection, re-ingest everything — has two problems:

1. **Downtime.** Queries return empty results until ingestion completes (hours on large corpora).
2. **No rollback.** If the new model performs worse on your eval set, you're stuck.

The dual-collection pattern solves both: write to a new versioned collection alongside the existing one, validate against the eval set (Workstream E Phase 1), then atomic-swap when ready.

## Pre-flight checklist

Before kicking off a migration:

- [ ] **Eval baseline captured** for the current model. The Phase 1 RAGAS harness is the source of truth — confirm `docs/EVAL_BASELINES.md` has a row for your current `EMBEDDING_MODEL_VERSION`.
- [ ] **Disk space**: each collection roughly equals the size of the chunk corpus times the embedding dimension × 4 bytes. Verify free space ≥ 2× current ChromaDB storage.
- [ ] **Backup**: snapshot the existing ChromaDB volume (`backup-kb.sh`). Re-embed is read-only on the source, but a snapshot guards against operator error during swap.
- [ ] **Tier check**: re-embedding all collections triggers many embedder calls. If using a hosted embedder (paid), confirm budget / rate limits.

## Step-by-step procedure

### 1. Stage the new model in settings

Add the new model and its version label to `EMBEDDING_MODEL_VERSIONS_PER_DOMAIN` in `config/settings.py` for the domain you're migrating, leaving other domains unchanged. Example:

```python
EMBEDDING_MODEL_VERSIONS_PER_DOMAIN = {
    "code":    "snowflake-arctic-embed-l-v2.0",   # NEW
    # other domains keep the global default (EMBEDDING_MODEL_VERSION)
}
```

The per-domain dict is read by the chunk-write path — chunks ingested after this point into the `code` domain will be stamped with the new version on their metadata.

### 2. Run the migration script in dry-run mode

```bash
docker compose exec mcp-server \
  python -m scripts.reembed_collection \
  --domain code \
  --target-model "Snowflake/snowflake-arctic-embed-l-v2.0" \
  --target-version "snowflake-arctic-embed-l-v2.0" \
  --batch-size 256 \
  --dry-run
```

Dry-run prints:
- Source collection name + cardinality
- Target collection name (e.g. `code__snowflake-arctic-embed-l-v2.0`)
- Estimated batches
- Estimated cost (if using a hosted embedder — placeholder for now)

Review carefully. Stop here if any number looks wrong.

### 3. Execute the dual-write

Drop `--dry-run`, add `--execute`:

```bash
docker compose exec mcp-server \
  python -m scripts.reembed_collection \
  --domain code \
  --target-model "Snowflake/snowflake-arctic-embed-l-v2.0" \
  --target-version "snowflake-arctic-embed-l-v2.0" \
  --batch-size 256 \
  --execute
```

The script:
1. Creates the target collection `<domain>__<target_version>` (idempotent — resumable).
2. Iterates the source collection in batches of `--batch-size`.
3. For each batch: skips chunks already present in the target (by `chunk_id`), embeds remaining with the new model, writes to target.
4. Logs progress every batch via `stage="reembed_collection"` (Langfuse picks this up).
5. On completion, prints **cardinality match status** — source N must equal target N.

The script is **resumable**: kill it and re-run with the same args; it picks up from the next un-migrated chunk.

### 4. Validate against eval set

Run the Phase 1 eval harness pointed at the new collection. There are two ways:

- **Shadow mode** (recommended): set `EMBEDDING_DOMAIN_OVERRIDE_<domain>=__<target_version>` env on a non-prod replica, run eval, compare to baseline.
- **Side-by-side**: query both collections with the same eval queries, compare result sets per query.

Pass criteria (default — adjust per workstream):
- Recall@10 ≥ baseline - 0.02
- NDCG@10 ≥ baseline - 0.02
- Faithfulness ≥ baseline - 0.02

Record results in `docs/EVAL_BASELINES.md` with a new row.

### 5. Atomic swap

If eval passes, do the swap. **Two patterns**, pick one:

**Pattern A — settings-driven (preferred):** flip `EMBEDDING_MODEL_VERSIONS_PER_DOMAIN[domain]` to the new version in `.env`, restart the MCP container. The query path reads from `<domain>__<new_version>` going forward; old `<domain>` collection becomes orphaned.

**Pattern B — collection-rename (only if you can't restart):** stop ingest, rename source `<domain>` → `<domain>__<old_version>_archived`, rename target `<domain>__<new_version>` → `<domain>`, restart ingest.

Pattern A is reversible by flipping the env back. Pattern B requires another rename to roll back.

### 6. Keep the old collection for 7 days

After cutover, **do not delete** the old collection for at least 7 days. If a regression surfaces in production traffic, you flip the env back to recover instantly.

After 7 days:

```bash
docker compose exec mcp-server \
  python -c 'import chromadb; from app.deps import get_chroma; \
             get_chroma().delete_collection("code__snowflake-arctic-embed-m-v1.5")'
```

## Rollback

If you need to roll back during validation (step 4 fails), it's a settings flip:

```bash
# In .env, remove the per-domain override or revert to old version:
unset EMBEDDING_DOMAIN_OVERRIDE_code
docker compose restart mcp-server
```

The dual-write target collection stays on disk; delete it manually when you're satisfied the rollback is permanent.

## Common pitfalls

| Symptom | Likely cause | Fix |
|---|---|---|
| Target collection has fewer rows than source after re-embed | Embedder hit rate limit and silently dropped batches | Check `stage="reembed_collection"` logs in Langfuse for 429s; lower `--batch-size`; resume |
| Search after cutover returns wrong-shape vectors | `EMBEDDING_DIMENSIONS` mismatch between old and new model | Each ChromaDB collection locks its dim at first write; verify the target collection was created with the right dim |
| Costs blow up | Hosted embedder + large batch + retry storm | Use the local Snowflake / `nomic-embed` ONNX paths if available; budget alert on `stage="reembed_collection"` |
| Re-embed never completes | Source collection mutated mid-migration | Disable ingestion (`INGEST_QUEUE_MODE=disabled`) before kicking off; or accept that newly-ingested chunks need a second pass |

## Forward compatibility

Workstream E Phase 0 stamps `embedding_model_version` on every newly-ingested chunk's metadata. This means you can identify which chunks need re-encoding without scanning the embeddings themselves — the metadata field is the source of truth.

Older chunks (pre-Phase-0 ingest) lack this field and are assumed to use the model that was active when they were written. Treat absence as `EMBEDDING_MODEL_VERSION` at the time of the corpus's last full migration.

## See also

- Driver doc: `tasks/2026-04-28-workstream-e-rag-modernization.md`
- ChromaDB collection naming: `config/taxonomy.py:collection_name`
- Phase 1 eval harness: `app/eval/ragas_metrics.py`, `app/eval/testset.py`
- Migration script: `src/mcp/scripts/reembed_collection.py`
