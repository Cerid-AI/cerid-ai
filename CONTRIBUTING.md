# Contributing to Cerid AI

Thanks for your interest — contributions are welcome.

## Development setup

### Prerequisites

- Python 3.11+ (3.12 recommended; the dev venv is pinned to 3.12)
- Node.js 22+ (for the React GUI and Electron app)
- Docker + Docker Compose v2+

### Get running

```bash
git clone https://github.com/Cerid-AI/cerid-ai.git && cd cerid-ai
cp .env.example .env               # add OPENROUTER_API_KEY (or point at Ollama)
./scripts/setup-archive.sh         # creates ~/cerid-archive/ watch dir
./scripts/start-cerid.sh           # boots Neo4j + ChromaDB + Redis + MCP + GUI
```

Then open:

- **React GUI:** http://localhost:3000
- **MCP API:** http://localhost:8888 (docs at `/docs`)
- **Health:** `curl http://localhost:8888/health`

## Running the checks locally

The whole CI matrix runs as these commands. Run them before you push.

```bash
# Python (inside src/mcp/)
ruff check src/mcp/                                # lint (pinned 0.15.4)
cd src/mcp && python -m mypy .                     # typecheck
cd src/mcp && lint-imports                         # layer contract (core ↛ app)
PYTHONPATH=src/mcp pytest src/mcp/tests/ -v        # 2,600+ unit tests

# Frontend
cd src/web
npm install
npm run typecheck                                  # tsc --noEmit
npx eslint .
npx vitest run                                     # 750+ tests

# Preservation harness (integration; needs a running stack)
make preservation-check                            # 35 invariants in ~60s
```

### CI gates

Every PR runs: `lint`, `typecheck`, `test`, `security`, `lock-sync`, `frontend`, `docker`, plus the drift gates (`env-example-drift`, `router-registry-drift`, `sync-manifest-drift`, `sdk-openapi-drift`, `no-legacy-neo4j-tree`, `silent-catch`) and the `preservation` live-stack integration suite. All are blocking.

## Project layout

```
src/mcp/                       FastAPI backend (Python 3.11+)
├── core/                      Portable orchestrator — never imports app/
│   ├── agents/                Query, memory, hallucination, self-RAG, …
│   ├── contracts/             VectorStore, GraphStore, CacheStore, LLMClient ABCs
│   ├── retrieval/             BM25, reranker, semantic cache, query decomposition
│   └── utils/                 Embeddings, circuit breaker, LLM client, NLI, …
├── app/                       Application layer (imports core + framework code)
│   ├── routers/               47 FastAPI routers (new endpoints go here)
│   ├── agents/                Orchestration wrappers (assembler, curator, triage, …)
│   ├── db/neo4j/              The only Neo4j code path (artifacts, memory, schema,
│   │                          relationships, taxonomy, users, agents, migrations/)
│   ├── services/              ingestion.py (ingest_content, ingest_file, dedup)
│   ├── parsers/               PDF, office, structured, email, ebook
│   └── main.py                FastAPI entry + lifespan
├── config/                    settings.py, features.py, taxonomy.py, providers.py
└── tests/                     2,600+ unit tests + integration/ (preservation harness)

src/web/src/                   React 19 + Vite 7 + Tailwind v4 + shadcn/ui
├── components/                chat/, kb/, monitoring/, settings/, audit/, memories/
├── hooks/                     use-chat, use-verification-orchestrator, use-kb-context
├── contexts/                  Settings, KBInjection, Conversations, Auth
└── __tests__/                 750+ vitest tests
```

### Layer contract (hard rule)

`core/` never imports from `app/`. Enforced by `import-linter` in `src/mcp/.importlinter`. If you need a concrete implementation from inside `core/`, take it as a dependency-injected callback — see `core.agents.hallucination.streaming::verify_response_streaming` for the pattern.

## Coding standards

- **Canonical imports only:** `from core.utils.X`, `from app.routers.X`, `from app.agents.X`, `from app.db.neo4j.X`. There are no bridge paths.
- **Type-hint public functions.** `mypy` is clean on `src/mcp/`.
- **Typed errors, not `HTTPException` in business logic.** Use `CeridError` subclasses from `errors.py`.
- **`@require_feature()` is the only tier gate.** No inline `CERID_TIER` checks.
- **Constants in `config/constants.py`.** No magic numbers.
- **ChromaDB metadata values are strings or ints.** Lists are stored as JSON strings (see `keywords_json`).
- **Every broad `except Exception:` in a hot path calls `log_swallowed_error(module, exc)`** from `core.utils.swallowed`. Failures surface at `/health.swallowed_errors_last_hour`. Lint: `scripts/lint-no-silent-catch.py`.
- **HTTP client is `httpx` everywhere.** `requests` is not a dependency.
- **Keep changes focused.** A bug fix touches only the bug; a refactor addresses the specific root cause.

## Plugin development

Plugins extend the backend via a manifest + `register()` hook. See [`docs/PLUGIN_DEVELOPMENT.md`](docs/PLUGIN_DEVELOPMENT.md) for the full guide.

Minimal skeleton:

1. Create `src/mcp/plugins/your_plugin/manifest.json`:
   ```json
   {
     "name": "your_plugin",
     "version": "1.0.0",
     "type": "parser",
     "description": "What it does",
     "tier": "community",
     "requires": []
   }
   ```
2. Add `plugin.py` exporting a `register()` function that wires into the relevant registry (parser, agent, tool, connector, sync).
3. Auto-discovered on server startup.

**Tier gating:** set `"tier": "pro"` in the manifest to require `CERID_TIER=pro`. Licensing: plugins ship under BSL-1.1 and convert to Apache-2.0 after three years.

## Pull request process

1. Fork the repo; create a feature branch off `main`.
2. Make focused commits. **Never** add `Co-Authored-By: Claude` / `Anthropic` / etc. — commits are authored by the human developer.
3. Before pushing, run the full local check list in [Running the checks locally](#running-the-checks-locally). If you touched `core/` or `app/`, also run `make preservation-check`.
4. Update docs in the same commit when you change:
   - A route or SDK endpoint → update `docs/API_REFERENCE.md` and regenerate `docs/ROUTER_REGISTRY.md` (`python scripts/gen_router_registry.py`).
   - A new env var → add it to `src/mcp/config/settings.py`, then `python scripts/gen_env_example.py` to regen `.env.example`.
   - A Python dep → edit `src/mcp/requirements.txt`, then `./scripts/regen-lock.sh` (Docker-wrapped pip-compile).
5. Open a PR with a clear description of what and why.

### Compliance check

- [ ] No Chinese-origin AI models referenced (DeepSeek, Qwen, Alibaba, etc.)
- [ ] Default Ollama model is `llama3.2:3b` (Meta)
- [ ] `grep -rn "deepseek\|qwen\|alibaba" src/ --include="*.py" --include="*.ts"` → zero results

## License

By contributing, you agree that your contributions will be licensed under the Apache License 2.0 (or BSL-1.1 for plugins, which converts to Apache-2.0 after three years).
