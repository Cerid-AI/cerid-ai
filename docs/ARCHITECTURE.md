# Cerid AI — Architecture

> **Last refresh:** 2026-04-19 (post-Sprint E/F consolidation)
> **Scope:** System layout, service topology, Phase C layer contract, data flow
> **Owner:** Anyone modifying the stack topology, adding a service, or splitting core/app boundaries

## Mission

Cerid AI is a **self-hosted, privacy-first Personal AI Knowledge Companion.** It unifies multi-domain knowledge bases (code, finance, projects, artifacts) into a context-aware LLM interface with RAG-powered retrieval and intelligent agents. Knowledge base stays local; LLM API calls send query context to the configured provider. Optional cloud sync (Dropbox) for cross-machine settings/conversations, encrypted when `CERID_ENCRYPTION_KEY` is set.

Core capabilities inventory lives in [`docs/PRESERVATION.md`](PRESERVATION.md) (§ "Invariants"). The preservation harness guards those capabilities across every consolidation sprint.

## Services

Microservices on a shared `llm-network` Docker bridge network. Services communicate by container name.

| Service | Port | Tech | Location |
|---|---|---|---|
| MCP Server (API) | 8888 | FastAPI / Python 3.11 | `src/mcp/` |
| ChromaDB | 8001 | Vector DB | `stacks/infrastructure/` |
| Neo4j | 7474, 7687 | Graph DB | `stacks/infrastructure/` |
| Redis | 6379 | Cache + audit log | `stacks/infrastructure/` |
| React GUI | 3000 | React 19 + Vite + nginx | `src/web/` |
| Marketing Site | 3001 (dev) | Next.js 16 + Vercel | `packages/marketing/` |
| Ollama (optional) | 11434 | Local LLM pipeline tasks | External or Docker |

## Data flow

```
User → React GUI (:3000) → MCP Server (:8888) → OpenRouter → LLM Provider
                                              ↘ ChromaDB + Neo4j + Redis (RAG)

File ingestion:
~/cerid-archive/ → Watcher → POST /ingest_file → Parse → Dedup → Chunk
                                                                 ↘ ChromaDB
                                                                 ↘ Neo4j
                                                                 ↘ Redis
```

React GUI talks to MCP directly (CORS `*`). Chat + smart-router traffic routes through `core/utils/llm_client.py` straight to OpenRouter — no proxy layer.

## Directory structure

```
cerid-ai-internal/
├── CLAUDE.md                # Agent directives (under 150 lines post-Sprint H)
├── docker-compose.yml       # Unified root compose
├── .env.age / .env.example  # Encrypted secrets / template
├── Makefile                 # lock-python, install-hooks, deps-check, preservation-check
├── scripts/                 # start-cerid.sh, validate-env.sh, sync-repos.py, gen_*
├── docs/                    # ARCHITECTURE.md (this), API_REFERENCE.md, SYNC_PROTOCOL.md,
│                            # PRESERVATION.md, CONVENTIONS.md, ROUTER_REGISTRY.md
├── plugins/                 # BSL-1.1 pro-tier plugins
├── src/mcp/                 # FastAPI MCP server (Python 3.11)
│   ├── core/                # Portable orchestrator core (Apache-2.0)
│   │   ├── agents/          # Query, memory, hallucination, curator, self_rag, memory_consolidation
│   │   ├── contracts/       # ABCs: VectorStore, GraphStore, CacheStore, LLMClient
│   │   ├── retrieval/       # BM25, reranker, semantic cache, query decomposition
│   │   ├── routing/         # Smart router, model providers
│   │   └── utils/           # Embeddings, circuit breaker, LLM client, temporal, diversity, text, etc.
│   ├── app/                 # Application layer (concrete implementations)
│   │   ├── routers/         # 48 FastAPI routers (post-Sprint F consolidation)
│   │   ├── agents/          # Orchestration wrappers: assembler, curator, decomposer, memory,
│   │   │                    #                        retrieval_orchestrator, templates, triage,
│   │   │                    #                        hallucination/{confidence, verdict_parsing, ...}
│   │   ├── stores/          # ChromaVectorStore, Neo4jGraphStore, RedisCacheStore (internal-only)
│   │   ├── db/neo4j/        # Cypher queries, schema, artifacts, migrations (m0001, m0002)
│   │   ├── services/        # ingestion.py (ingest_content, ingest_file, dedup)
│   │   ├── middleware/      # auth, rate_limit, request_id, jwt, tenant_context
│   │   ├── parsers/         # PDF, office, structured, email, ebook
│   │   ├── eval/            # Retrieval evaluation harness + benchmark suite (internal-only)
│   │   ├── sync/            # CRDT, export, import, manifest, status
│   │   ├── models/          # Pydantic schemas
│   │   ├── main.py          # FastAPI entry + lifespan
│   │   ├── tools.py         # MCP tool registry + dispatcher (21 core + 5 trading)
│   │   └── internal_modules.py  # /health.invariants.internal_modules flags
│   ├── enterprise/          # Enterprise overlay (ABAC, SSO, classification, immutable audit)
│   ├── config/              # settings.py, taxonomy.py, features.py, providers.py
│   ├── routers/             # billing.py ONLY (internal-only; whole dir stripped from public)
│   ├── utils/               # 35 standalone utility modules (post-Sprint-E bridges retired)
│   ├── tests/
│   │   ├── integration/     # Preservation harness (I1-I8, 35 tests)
│   │   └── test_*.py        # 2,500+ unit tests
│   └── requirements.txt/.lock   # Python deps
├── src/web/                 # React GUI (React 19, Vite 7, Tailwind v4, shadcn/ui)
│   ├── src/components/      # chat/, kb/, settings/, monitoring/, audit/, memories/, ui/
│   ├── src/hooks/           # use-chat, use-verification-orchestrator, use-kb-context, ...
│   ├── src/contexts/        # Settings, KBInjection, Conversations, Auth
│   ├── src/lib/             # types.ts, api/, model-router.ts, canonical-claim alignment
│   └── src/__tests__/       # 751+ vitest tests
├── packages/marketing/      # Next.js 16 marketing site (cerid.ai)
├── packages/desktop/        # Electron desktop app (internal-only)
├── stacks/                  # infrastructure/ (Neo4j, ChromaDB, Redis)
├── artifacts/ → ~/Dropbox/AI-Artifacts   (symlink)
└── data/ → src/mcp/data                  (symlink)
```

**What changed post-Sprint E/F:**

| Directory | Before consolidation | After |
|---|---|---|
| `src/mcp/services/` | 3 bridge files re-exporting from `app.services` | **deleted** |
| `src/mcp/agents/` | 14 files: 7 bridges + 5 standalones + 1 adapter + 1 subpackage | **deleted** — standalones moved to `app/agents/` |
| `src/mcp/utils/` | 56 files (21 bridges + 35 standalones) | 35 standalones only |
| `src/mcp/routers/` | 43 files: 32 bridge stubs + 11 legacy real + billing | **billing only** (internal-strip target) |

Consumer code imports canonical paths (`core.utils.*`, `app.routers.*`, `app.agents.*`). No more "which of three paths?" ambiguity.

## Phase C layer contract

Three layers, one rule: **core must not import app.**

### `core/` — portable orchestrator
- Licensed Apache-2.0 for standalone reuse.
- Zero FastAPI, zero Chroma/Neo4j/Redis driver imports.
- Abstractions only: `core.contracts.VectorStore`, `GraphStore`, `CacheStore`, `LLMClient`.
- Houses pipeline algorithms: BM25, reranker, semantic cache, query decomposition, NLI entailment, smart routing, claim canonicalization (`core.agents.hallucination.models.ClaimVerification`).

### `app/` — concrete implementations
- FastAPI routers, store adapters (`ChromaVectorStore`, `Neo4jGraphStore`, `RedisCacheStore`), parsers, middleware, sync, eval, entry point.
- Free to import from `core` and bring in framework code (FastAPI, httpx, Pydantic).
- Houses orchestration wrappers that stitch core algorithms into runtime flows (`app/agents/assembler.py` etc.).

### `enterprise/` — optional overlay
- ABAC, SSO, classification, immutable audit.
- Scaffolded, not wired by default.
- Internal-only per `.sync-manifest.yaml`.

### `import-linter` gate
- Declared in `src/mcp/.importlinter` and `pyproject.toml`.
- Fails CI on: `core → app`, `core → routers`, `core → services`, `core → middleware`, `core → parsers`, `core → sync`, `core → models`, `core → db`, `core → deps`, `core → tools`, `core → main`, `core → scheduler`, `core → eval`, `core → stores`, `core → agents` (top-level bridge — now an empty dir but the rule stays).
- No layering exceptions. The former `utils.data_sources` narrow exception was resolved by the 2026-04-20 sprint: the package moved to `app/data_sources/` and `authoritative_verify` now receives the registry via dependency injection (see `set_data_source_registry()` wired from `app/main.py`).

## Observability contract

The canonical endpoint is `GET /health`. Every observability signal must appear in `/health.invariants`:

- `healthy_invariants: bool` — criticality gate, flips HTTP to 503 on failure
- `nli_model_loaded: bool` — hard gate, verification depends on it
- `verification_report_orphans: int` — Neo4j drift signal, m0002 keeps it at 0
- `collections_empty: [str]` — observability-only, empty domains
- `internal_modules: dict[str, bool]` — build identity flags (public vs internal distribution)
- `swallowed_errors_last_hour: {module: int}` — `log_swallowed_error` counter

Resist adding health signals anywhere else. The preservation harness (I1) enforces field presence.

## Version contract

Served by `/`, `/health`, and `/openapi.json`. Single source of truth: `pyproject.toml` via `core/utils/version.py::get_version()`. Docker builds require `make version-file` to write a stubbed VERSION before `docker build` — otherwise the container returns the 0.0.0 fallback. `scripts/start-cerid.sh --build` calls `make version-file` automatically.

## Where things live (quick index)

| Question | Where |
|---|---|
| What does `/agent/query` return? | [`docs/API_REFERENCE.md`](API_REFERENCE.md); preservation I2 |
| What's the canonical claim shape? | `src/mcp/core/agents/hallucination/models.py` |
| How do I add a new route? | Write in `src/mcp/app/routers/`; regenerate `docs/ROUTER_REGISTRY.md` |
| Where does internal code live? | `*_internal.py` files listed in `.sync-manifest.yaml` |
| How does the sync work? | [`docs/SYNC_PROTOCOL.md`](SYNC_PROTOCOL.md) |
| What must not break? | [`docs/PRESERVATION.md`](PRESERVATION.md) I1-I8 |
| What are the project conventions? | [`docs/CONVENTIONS.md`](CONVENTIONS.md) |
| What's resolved / shipped? | [`docs/COMPLETED_PHASES.md`](COMPLETED_PHASES.md) |
| Current sprint work? | `tasks/todo.md` |
