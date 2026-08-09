# Contributing to Cerid AI

Thanks for your interest — contributions are welcome.

## Development setup

### Prerequisites

- Python 3.12 (the runtime, CI matrix, and dev venv are all pinned to 3.12)
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
PYTHONPATH=src/mcp pytest src/mcp/tests/ -v        # 4,800+ Python tests

# Frontend
cd src/web
npm install
npm run typecheck                                  # tsc --noEmit
npx eslint .
npx vitest run                                     # 2,700+ frontend tests

# Preservation harness (integration; needs a running stack)
make preservation-check                            # preservation harness of integration invariants
```

### CI gates

CI runs 10 jobs: `changes`, `lint`, `typecheck`, `test`, `security`, `lock-sync`, `frontend`, `license-scan`, `docker`, and `ci-ok`. All are blocking.

The single **required** status check is `ci-ok`, an aggregator that passes when the real jobs succeeded *or* were skipped. Docs-only PRs (changes confined to `docs/**`, `tasks/**`, or `*.md`) skip the code jobs via the `changes` gate, so `ci-ok` goes green without running the full suite — they merge without burning code CI.

## Project layout

```
src/mcp/                       FastAPI backend (Python 3.12)
├── core/                      Portable orchestrator — never imports app/
│   ├── agents/                Query, memory, hallucination, self-RAG, …
│   ├── contracts/             VectorStore, GraphStore, CacheStore, LLMClient ABCs
│   ├── retrieval/             BM25, reranker, semantic cache, query decomposition
│   └── utils/                 Embeddings, circuit breaker, LLM client, NLI, …
├── app/                       Application layer (imports core + framework code)
│   ├── routers/               62 FastAPI routers (new endpoints go here)
│   ├── agents/                Orchestration wrappers (assembler, curator, triage, …)
│   ├── db/neo4j/              The only Neo4j code path (artifacts, memory, schema,
│   │                          relationships, taxonomy, users, agents, migrations/)
│   ├── services/              ingestion.py (ingest_content, ingest_file, dedup)
│   ├── parsers/               PDF, office, structured, email, ebook
│   └── main.py                FastAPI entry + lifespan
├── config/                    settings.py, features.py, taxonomy.py, providers.py
└── tests/                     4,800+ Python tests + integration/ (preservation harness)

src/web/src/                   React 19 + Vite 7 + Tailwind v4 + shadcn/ui
├── components/                chat/, kb/, monitoring/, settings/, audit/, memories/
├── hooks/                     use-chat, use-verification-orchestrator, use-kb-context
├── contexts/                  Settings, KBInjection, Conversations, Auth
└── __tests__/                 2,700+ frontend tests
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

**Tier gating:** set `"tier": "pro"` in the manifest to require `CERID_TIER=pro`. Licensing: plugins ship under BUSL-1.1 and convert to Apache-2.0 after three years.

## Pull request process

1. Fork the repo; create a feature branch off `main`.
2. Make focused commits. **Never** add `Co-Authored-By: Claude` / `Anthropic` / etc. — commits are authored by the human developer.
3. Before pushing, run the full local check list in [Running the checks locally](#running-the-checks-locally). If you touched `core/` or `app/`, also run `make preservation-check`.
4. Update docs in the same commit when you change:
   - A route or SDK endpoint → update `docs/API_REFERENCE.md`.
   - A new env var → add it to `src/mcp/config/settings.py`, then `python scripts/gen_env_example.py` to regen `.env.example`.
   - A Python dep → edit `src/mcp/requirements.txt`, then `./scripts/regen-lock.sh` (Docker-wrapped pip-compile).
5. Open a PR with a clear description of what and why.

### Compliance check

- [ ] No Chinese-origin AI models referenced (DeepSeek, Qwen, Alibaba, etc.)
- [ ] Default Ollama model is `llama3.2:3b` (Meta)
- [ ] `grep -rn "deepseek\|qwen\|alibaba" src/ --include="*.py" --include="*.ts"` → zero results

## License

Cerid AI is licensed under the **Functional Source License 1.1 with an Apache-2.0
future license** (`FSL-1.1-ALv2`, [`LICENSE`](LICENSE)): every version becomes
Apache-2.0 on its second anniversary. This is **source-available, not open source**.

The repository is not uniformly licensed. Which license applies depends on where the
file lives:

| Path | License |
|---|---|
| Repository root, `src/mcp/`, `src/web/` | FSL-1.1-ALv2 |
| `packages/sdk/python`, `packages/sdk/typescript` | Apache-2.0 |
| `packages/cli`, `packages/widget`, `packages/extension` | Apache-2.0 |
| `plugins/`, `src/mcp/plugins/` | BUSL-1.1 (converts to Apache-2.0 after three years) |

The Apache-2.0 rows are the surfaces you build against — the SDKs and client
integrations — so depending on them never pulls FSL terms into your own code. Each of
those directories carries its own `LICENSE`, and every source file states its license
in an `SPDX-License-Identifier` header; that header is authoritative for the file.

Releases published before the August 2026 license transition were, and remain, Apache-2.0.

### Contributor license grant

The copyright holder offers commercial exceptions to the FSL and may dual-license the
software. That is only possible if the holder has the rights to relicense every line
in the tree, which means inbound contributions need an explicit grant. So:

**By submitting a contribution to this repository — a pull request, a patch, a code
suggestion in an issue, or any other form — you agree to the following.**

1. **Grant.** You grant Justin Michaels ("the Owner") a perpetual, worldwide,
   non-exclusive, royalty-free, irrevocable, sublicensable and transferable license to
   reproduce, modify, prepare derivative works of, publicly display, publicly perform,
   distribute and otherwise exploit your contribution, in whole or in part, in any
   medium and by any means now known or later developed.

2. **Relicensing and dual licensing.** That license expressly includes the right to
   license your contribution to third parties under **any** terms the Owner chooses,
   including FSL-1.1-ALv2, Apache-2.0, BUSL-1.1, a proprietary commercial license, or
   a commercial exception negotiated with a specific customer. You waive any
   requirement that the Owner seek further permission for such a relicense.

3. **Patents.** You grant the Owner and every recipient of the software a perpetual,
   worldwide, non-exclusive, royalty-free, irrevocable patent license to make, have
   made, use, offer to sell, sell, import and otherwise transfer your contribution,
   covering only those patent claims you can license that are necessarily infringed by
   your contribution alone or by its combination with the project.

4. **You keep your copyright.** This is a license, not an assignment. You may use your
   own contribution however you like, elsewhere.

5. **Representations.** You represent that (a) each contribution is your original work,
   or you have the right to submit it under these terms; (b) you are legally entitled
   to grant the above licenses; and (c) if your employer has rights in work you create,
   you have permission to contribute on their behalf or your employer has waived those
   rights.

If you use AI tooling to help write a contribution, follow the same convention the
project does: no AI attribution in commit messages, PR titles, PR descriptions, or
code comments. Contributions are authored by the human submitting them.
