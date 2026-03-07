# CLAUDE.md - Cerid AI

> **Extends:** `~/Develop/CLAUDE.md` — all global workflow orchestration, core principles, and task
> management rules apply here. This file adds only project-specific context.

---

## Project Overview

Cerid AI is a self-hosted, privacy-first Personal AI Knowledge Companion. It unifies multi-domain knowledge bases (code, finance, projects, artifacts) into a context-aware LLM interface with RAG-powered retrieval and intelligent agents. All data stays local; only LLM API calls go external.

**Status:** Phase 27 complete. 950 Python tests, 320 frontend tests. 9 agents, 18 MCP tools, hybrid BM25s+vector search with semantic chunking, circuit breakers on all Bifrost + Neo4j calls, shared Bifrost call utility with narrowed exception handling, distributed request tracing, adaptive quality feedback, per-domain tag vocabulary with typeahead UI, improved synopsis generation, streaming verification with 4 claim types (evasion/citation/recency/ignorance), Self-RAG validation loop, smart routing (direct-to-OpenRouter chat proxy, capability-based model scoring, three-way routing mode), context-aware chat (corrections, token-budget KB injection, semantic dedup), incremental knowledge sync with tombstones and conflict resolution, sync GUI with export/import/status dashboard, drag-drop ingestion with pre-upload options dialog, archive storage mode, mypy type checking in CI, React GUI with iPad/tablet responsive touch UX, LAN access with robust multi-interface IP detection and stale-IP auto-fix, pre-flight validation (port conflicts, env vars, disk space), post-startup reachability checks, guided `setup.sh` installer, configurable port overrides (`CERID_PORT_*`), optional Caddy HTTPS gateway and Cloudflare Tunnel for demos. Infrastructure security hardened (Redis/MongoDB auth, port binding, resource limits, security headers, nginx hardening). CI/CD 7-job pipeline with timeouts. See [`docs/COMPLETED_PHASES.md`](docs/COMPLETED_PHASES.md) for history.

**Next:** Phase 26 (User Review UX fixes) or D2 conversation fork/branch UI. See [`tasks/todo.md`](tasks/todo.md).

**Open issues:** [`docs/ISSUES.md`](docs/ISSUES.md) (1 open: D2 conversation fork).

## Architecture

Microservices architecture with Docker Compose orchestration on a shared `llm-network` bridge network. Services communicate by container name.

### Services

| Service | Port | Stack Path | Tech |
|---------|------|------------|------|
| LibreChat (UI) | 3080 | `stacks/librechat/` | Node.js/React |
| MCP Server (API) | 8888 | `src/mcp/` | FastAPI / Python 3.11 |
| Bifrost (LLM Gateway) | 8080 | `stacks/bifrost/` | Semantic intent routing |
| ChromaDB (Vectors) | 8001 | `stacks/infrastructure/` | Vector DB |
| Neo4j (Graph) | 7474/7687 | `stacks/infrastructure/` | Graph DB |
| Redis (Cache) | 6379 | `stacks/infrastructure/` | Cache + audit log |
| MongoDB (Chat) | 27017 | via `stacks/librechat/` | LibreChat persistence |
| PostgreSQL+pgvector | 5432 | via `stacks/librechat/` | RAG vector storage |
| Meilisearch | 7700 | via `stacks/librechat/` | Full-text search |
| RAG API | 8000 | via `stacks/librechat/` | Document processing |
| Dashboard (legacy) | 8501 | `src/gui/` | Streamlit admin UI |
| React GUI | 3000 | `src/web/` | React 19 + Vite + nginx |

### Key Data Flow

```
User → React GUI (3000) → Bifrost (8080) → OpenRouter → LLM Provider
                        → MCP Server (8888) → ChromaDB/Neo4j (RAG context)

Legacy: User → LibreChat (3080) → Bifrost (8080) → OpenRouter → LLM Provider

File Ingestion:
~/cerid-archive/ → Watcher → POST /ingest_file → Parse → Dedup → Chunk → ChromaDB + Neo4j + Redis
```

React GUI talks to Bifrost via nginx proxy (`/api/bifrost/`) and to MCP directly (CORS `*`). Bifrost classifies intent (coding/research/simple/general) and routes to the appropriate model.

## Directory Structure

```
├── CLAUDE.md                    # This file
├── .env.age / .env.example      # Encrypted secrets / template
├── Makefile                     # lock-python, install-hooks, deps-check
├── scripts/                     # start-cerid.sh, validate-env.sh, cerid-sync.py, env-(un)lock.sh
├── docs/                        # API_REFERENCE.md, ISSUES.md, OPERATIONS.md, plans/
├── src/mcp/                     # FastAPI MCP server (Python 3.11)
│   ├── main.py                  # Entry point
│   ├── config/                  # settings.py, taxonomy.py, features.py
│   ├── db/neo4j/                # schema, artifacts, relationships, taxonomy
│   ├── sync/                    # export, import_, manifest, status
│   ├── parsers/                 # registry, pdf, office, structured, email, ebook
│   ├── services/                # ingestion.py (ingest_content, ingest_file, dedup)
│   ├── eval/                    # Retrieval evaluation harness (NDCG, MRR, P@K, R@K)
│   ├── agents/                  # query, curator, triage, rectify, audit, maintenance, hallucination, memory, self_rag
│   ├── routers/                 # FastAPI routers (health, query, ingestion, agents, taxonomy, etc.)
│   ├── middleware/              # auth.py, rate_limit.py, request_id.py
│   ├── tools.py                 # MCP tool registry + dispatcher (18 tools)
│   ├── plugins/                 # Plugin system (OCR scaffold)
│   ├── utils/                   # bm25, cache, query_cache, embeddings, chunker, dedup, encryption, etc.
│   ├── scripts/                 # watch_ingest.py, watch_obsidian.py, ingest_cli.py
│   └── requirements.txt/.lock   # Python deps (ranges / pinned with hashes)
├── src/web/                     # React GUI (React 19, Vite 7, Tailwind v4, shadcn/ui)
│   ├── docker-compose.yml       # cerid-web service (separate from MCP)
│   ├── src/lib/                 # types.ts, api.ts, model-router.ts
│   ├── src/hooks/               # use-chat, use-kb-context, use-settings, use-verification-stream, etc.
│   ├── src/contexts/            # SettingsContext, KBInjectionContext, ConversationsContext
│   ├── src/components/          # layout/, chat/, kb/, monitoring/, audit/, memories/, settings/, ui/
│   └── src/__tests__/           # 320 vitest tests (24 test files)
├── src/gui/                     # Streamlit dashboard (legacy)
├── stacks/                      # infrastructure/ (Neo4j, ChromaDB, Redis), bifrost/, librechat/
├── artifacts/ → ~/Dropbox/AI-Artifacts (symlink)
└── data/ → src/mcp/data (symlink)
```

> For API endpoints, agent details, ingestion pipeline, and sync commands, see [`docs/API_REFERENCE.md`](docs/API_REFERENCE.md).

## Development

### Secrets Management

Single `.env` file at repo root, encrypted with `age`. Key at `~/.config/cerid/age-key.txt`.

```bash
./scripts/env-unlock.sh          # Decrypt .env.age → .env
./scripts/env-lock.sh            # Re-encrypt after editing
```

### Starting the Stack

```bash
./scripts/start-cerid.sh            # start all 5 service groups
./scripts/start-cerid.sh --build    # rebuild images after code changes
```

Startup order: `[1/5]` Infrastructure (Neo4j, ChromaDB, Redis) → `[2/5]` Bifrost → `[3/5]` MCP + Dashboard → `[4/5]` React GUI → `[5/5]` LibreChat.

### Environment Validation

```bash
./scripts/validate-env.sh          # full validation (14 checks)
./scripts/validate-env.sh --quick  # containers only (Docker + health checks)
./scripts/validate-env.sh --fix    # auto-start missing infrastructure
```

### Second-Machine Bootstrap

```bash
git clone git@github.com:sunrunnerfire/dotfiles.git ~/dotfiles && cd ~/dotfiles && bash install.sh
brew install age
git clone git@github.com:sunrunnerfire/cerid-ai.git ~/cerid-ai && cd ~/cerid-ai
./scripts/env-unlock.sh
ln -s ~/Dropbox/cerid-archive ~/cerid-archive
./scripts/start-cerid.sh          # auto-imports KB from Dropbox sync if Neo4j empty
./scripts/validate-env.sh
```

### Dependency Management

```bash
make lock-python                   # Regenerate requirements.lock after editing requirements.txt
make install-hooks                 # Git pre-commit hook (lock file sync check)
make deps-check                    # Verify all lock files are current
```

Cross-service version constraints: see `docs/DEPENDENCY_COUPLING.md`.

### Configuration

- `.env` (repo root) — All secrets. Encrypted as `.env.age`. Never committed in plaintext.
- `src/mcp/config/settings.py` — Domains, tiers, URLs, sync, model IDs
- `stacks/bifrost/config.yaml` — Intent classification, model routing, budget
- `stacks/librechat/librechat.yaml` — MCP servers, endpoints, model list

### Verification

```bash
curl http://localhost:8888/health
curl http://localhost:8888/collections
curl http://localhost:8888/artifacts
```

## Claude Code Setup (New Machine)

1. **Verify prerequisites:** Docker running, `.env` decrypted, `age` installed, archive directory exists
2. **Run `./scripts/validate-env.sh`** to check all 14 environment validations
3. **If containers are down:** `./scripts/start-cerid.sh` (or `--build` after a `git pull`)
4. **Check `.claude/settings.json`** — shared hooks config is committed; per-machine permissions go in `.claude/settings.local.json` (gitignored)
5. **MCP server is at `http://localhost:8888/mcp/sse`** — configured in `.mcp.json` (committed), exposes 18 `pkb_*` tools
6. **React GUI dev server:** configured in `.claude/launch.json` (committed) — Vite on port 5173

**Key files for Claude Code:**
- `.mcp.json` — MCP server connection (Cerid KB tools)
- `.claude/settings.json` — shared hooks config (session-start, safety-check, typecheck, pythonlint)
- `.claude/settings.local.json` — per-machine permission allowlist (gitignored, create from scratch)
- `.claude/launch.json` — React dev server config
- `.claudeignore` — excludes node_modules, dist, runtime data, binaries, lock files

**Hooks (4 total, run automatically):**
- `session-start.sh` (SessionStart) — Docker + MCP + GUI health check
- `safety-check.sh` (PreToolUse/Bash) — blocks destructive commands
- `typecheck.sh` (PostToolUse/Edit|Write) — `npx tsc --noEmit` for `.ts`/`.tsx` in `src/web/`
- `pythonlint.sh` (PostToolUse/Edit|Write) — `ruff check` for `.py` in `src/mcp/`

**Tests:** Run Python tests in Docker (`host macOS lacks chromadb`):
```bash
docker run --rm -v "$(pwd)/src/mcp:/work" -w /work python:3.11-slim bash -c "pip install -q -r requirements.txt -r requirements-dev.txt && python -m pytest tests/ -v"
```
Frontend tests: `cd src/web && npx vitest run`

## Conventions

- **Session start:** Run `./scripts/validate-env.sh --quick` at the beginning of every session
- Docker services use container-name-based discovery on `llm-network`
- MCP protocol uses SSE transport with session-based message queuing
- Secrets go in root `.env`, encrypted as `.env.age` via `age`. Key at `~/.config/cerid/age-key.txt`
- User files (`~/cerid-archive/`) mounted read-only, never in git repo. Symlinked to `~/Dropbox/cerid-archive` for multi-machine sync
- Symlinks used for `artifacts/` and `data/` — don't break them
- Infrastructure DB data at `stacks/infrastructure/data/` (.gitignored)
- ChromaDB metadata values are strings/ints only (lists stored as JSON strings)
- ChromaDB client version must match server (currently `>=0.5,<0.6`)
- Error responses use `HTTPException` (returns `{"detail": "..."}`)
- Neo4j Cypher: use explicit RETURN clauses, not map projections (breaks with Python string ops)
- Deduplication: SHA-256 of parsed text, atomic via Neo4j UNIQUE CONSTRAINT on `content_hash`
- Batch ChromaDB writes: single `collection.add()` call per ingest, not per-chunk
- PDF parsing: pdfplumber extracts tables as Markdown, non-table text extracted separately to avoid duplication
- Host: Mac Pro (16-Core Xeon W, 160GB RAM), macOS
- **React GUI (`src/web/`):** Tailwind CSS v4 (uses `@tailwindcss/vite` plugin — no `tailwind.config.ts`); shadcn/ui New York style, Zinc base color; path alias `@/*` → `./src/*`; Bifrost CORS handled via Vite dev proxy (`/api/bifrost` → `localhost:8080`) and nginx proxy in Docker; `VITE_MCP_URL` and `VITE_BIFROST_URL` are `ENV` defaults baked into Dockerfile (not runtime-configurable without rebuild); `VITE_CERID_API_KEY` is a build `ARG`; bundle splitting via React.lazy + Vite manualChunks (75% main chunk reduction); iPad/tablet responsive: sidebar auto-collapses at 1024px, KB pane becomes bottom Sheet drawer on narrow viewports, toolbar overflow menu, `@media (hover: none)` touch visibility overrides, `@media (pointer: coarse)` 44px touch targets, iOS safe area insets, zoom prevention
- **Backend Hardening (`src/mcp/middleware/`):** API key auth is opt-in — set `CERID_API_KEY` env var to enable (header: `X-API-Key`). Rate limiting uses in-memory sliding window with path-specific limits (`/agent/` 20 req/min, `/ingest` and `/recategorize` 10 req/min). Redis query cache with 5-min TTL (`utils/query_cache.py`) — caches `/query` and `/agent/query` results. LLM feedback loop toggled via `ENABLE_FEEDBACK_LOOP` env var. CORS origins configurable via `CORS_ORIGINS` (defaults to `*`)
- **Docker env var pattern:** `src/mcp/docker-compose.yml` uses `env_file: ../../.env` to load secrets into the MCP container. Do NOT add `${VAR}` interpolation in the `environment:` section for passthrough vars (e.g., `NEO4J_PASSWORD`) — it fails when running without `--env-file` and the empty value overrides the env_file entry. Container-specific overrides (service URLs, paths) are fine in `environment:` since they're literal values. Always rebuild MCP via `docker compose -f src/mcp/docker-compose.yml --env-file .env up -d --build` or use `scripts/start-cerid.sh`.
- **Neo4j auth validation:** `deps.py` `get_neo4j()` validates credentials by running `RETURN 1` (not just `verify_connectivity()` which only checks transport). `/health` endpoint also runs a Cypher query on every call. Empty `NEO4J_PASSWORD` raises `RuntimeError` immediately.
