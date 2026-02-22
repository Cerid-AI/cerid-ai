# Phase 5: Multi-Machine Dev Environment & Knowledge Sync

> **Date:** 2026-02-22
> **Status:** Approved — ready for implementation
> **Context:** Next session will run on the "pre-Phase 4" machine with legacy Docker containers

## Problem Statement

Cerid AI runs on 2 dev machines. Three problems:

1. **Missing infrastructure**: Neo4j, ChromaDB, and Redis are referenced in code but **never defined** in any docker-compose file. Every Claude Code session wastes time discovering and fixing missing containers.
2. **No environment validation**: No way to quickly check if the stack is ready before starting work.
3. **No data portability**: Knowledge (vectors, graph, indexes) is trapped on whichever machine ingested it. Need sync via Dropbox or iCloud.

## Phase A: Fix Infrastructure

### A1. Create `stacks/infrastructure/docker-compose.yml`

Defines the 3 missing services:

| Service | Container | Image | Port | Volume |
|---------|-----------|-------|------|--------|
| Neo4j | ai-companion-neo4j | neo4j:5-community | 7474, 7687 | `./data/neo4j:/data` |
| ChromaDB | ai-companion-chroma | chromadb/chroma:0.5.23 | 8001→8000 | `./data/chroma:/chroma/chroma` |
| Redis | ai-companion-redis | redis:7-alpine | 6379 | `./data/redis:/data` |

All on `llm-network`, with healthchecks, bind-mount volumes (not named volumes).

### A2. Update `scripts/start-cerid.sh`

Add infrastructure as step 1/4:
```
[1/4] Infrastructure (Neo4j, ChromaDB, Redis)
[2/4] Bifrost
[3/4] MCP Services
[4/4] LibreChat
```

### A3. Create `scripts/validate-env.sh`

Pre-flight checks:
- Docker daemon running
- `llm-network` exists
- `.env` file present with required vars
- Infrastructure containers healthy
- Service containers running
- Data directories exist
- Sync directory accessible (if configured)
- `--quick` flag for fast container-only check
- `--fix` flag to auto-start missing infrastructure

### A4. Update `.gitignore`

Add `stacks/infrastructure/data/`

### A5. Update `CLAUDE.md`

Add "run `./scripts/validate-env.sh --quick` before starting work"

---

## Phase B: Knowledge Sync

### B1. Add config to `.env.example` and `config.py`

```
CERID_SYNC_DIR=~/Dropbox/cerid-sync     # or iCloud path
CERID_MACHINE_ID=<hostname>              # auto-detected default
```

### B2. Create `src/mcp/cerid_sync_lib.py`

Core export/import logic:

**Export** (safe while containers are running — all read-only API calls):
- `export_neo4j()` — Cypher MATCH queries → `neo4j/artifacts.jsonl`, `relationships.jsonl`
- `export_chroma()` — `collection.get(include=["documents","metadatas","embeddings"])` → per-domain JSONL (batches of 500)
- `export_bm25()` — copy JSONL corpus files directly
- `export_redis()` — `LRANGE ingest:log` → `redis/audit_log.jsonl`
- Writes `manifest.json` with timestamps, counts, SHA-256 checksums

**Import** (non-destructive by default):
- `import_neo4j()` — MERGE for domains, timestamp comparison for artifacts (newer wins), MERGE for relationships
- `import_chroma()` — skip existing chunk IDs, `collection.add()` with pre-computed embeddings (no re-embedding cost)
- `import_bm25()` — merge by chunk ID, append-only
- `import_redis()` — append newer entries only
- `--force` flag to overwrite all local data

Sync directory structure:
```
$CERID_SYNC_DIR/
├── manifest.json
├── neo4j/artifacts.jsonl, domains.jsonl, relationships.jsonl
├── chroma/domain_coding.jsonl, domain_finance.jsonl, ...
├── bm25/coding.jsonl, finance.jsonl, ...
└── redis/audit_log.jsonl
```

### B3. Create `scripts/cerid-sync.py`

CLI wrapper:
```bash
python scripts/cerid-sync.py export          # dump to sync dir
python scripts/cerid-sync.py import          # load from sync dir
python scripts/cerid-sync.py import --force  # overwrite local
python scripts/cerid-sync.py status          # compare local vs sync
```

### B4. Create `src/mcp/sync_check.py`

Auto-import on startup:
- Called from `main.py` lifespan after `graph.init_schema()`
- If Neo4j has 0 artifacts AND sync dir has a valid manifest → auto-import
- Only runs on fresh/empty databases (won't touch existing data)

### B5. Update `src/mcp/main.py`

Add auto-import call in lifespan.

---

## Critical Files

| File | Action |
|------|--------|
| `stacks/infrastructure/docker-compose.yml` | Create |
| `scripts/validate-env.sh` | Create |
| `scripts/cerid-sync.py` | Create |
| `src/mcp/cerid_sync_lib.py` | Create |
| `src/mcp/sync_check.py` | Create |
| `scripts/start-cerid.sh` | Modify — add infra step |
| `src/mcp/config.py` | Modify — add sync config |
| `src/mcp/main.py` | Modify — add auto-import |
| `.env.example` | Modify — add sync vars |
| `.gitignore` | Modify — add infra data dir |
| `CLAUDE.md` | Modify — add validation instructions |

## Edge Cases

- **First-time new machine**: Clone → unlock .env → start-cerid.sh → infra starts → MCP starts → empty DB → auto-import from Dropbox/iCloud sync dir
- **Both machines ingest different files**: No conflicts — artifact UUIDs are unique, content_hash prevents duplicates
- **Export during ingestion**: Safe — all export queries are read-only
- **Partial sync failure**: Each data source exports independently; manifest tracks what succeeded; import is idempotent
- **Embedding model mismatch**: manifest records model info; import warns and skips ChromaDB if models differ

## Verification

1. `./scripts/validate-env.sh` returns exit 0 with all checks passing
2. `docker ps` shows all 13 containers healthy
3. `python scripts/cerid-sync.py export` creates populated sync dir
4. Stop containers, delete `stacks/infrastructure/data/`, restart → auto-import restores data
5. `python scripts/cerid-sync.py status` shows local and sync counts match

## Notes for Next Session

The next session will run on the **pre-Phase 4 machine** which likely has:
- Legacy Docker containers with old names/configs
- Possibly running Neo4j/ChromaDB/Redis as standalone containers (not in compose)
- Old `.env` files scattered across stacks (now consolidated to repo root)
- No `age` encryption setup yet

**First steps on the other machine:**
1. `git pull` to get Phase 4 changes
2. Install `age`: `brew install age`
3. Copy `~/.config/cerid/age-key.txt` from primary machine (or generate new key and re-encrypt)
4. Run `./scripts/env-unlock.sh` to decrypt `.env`
5. Stop and remove legacy containers that conflict with new infrastructure compose
6. Begin Phase A implementation
