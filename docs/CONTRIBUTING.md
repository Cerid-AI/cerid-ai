# Contributing to Cerid AI

Developer reference for keeping the codebase consistent. Check this before every PR.

---

## 1. Quick Reference: What to Update When

| Change Type | Primary File | Must Also Update |
|-------------|-------------|-----------------|
| Add MCP tool | `src/mcp/tools.py` | `CLAUDE.md` (tool count), `README.md` (tool count) |
| Add API endpoint | `src/mcp/routers/new.py` | `main.py` (register), `docs/API_REFERENCE.md` |
| Add SDK endpoint | `src/mcp/routers/sdk.py` | `docs/DEPENDENCY_COUPLING.md`, `docs/INTEGRATION_GUIDE.md` |
| Add env var | `src/mcp/config/settings.py` | `.env.example` |
| Add feature toggle | `src/mcp/config/features.py` | `.env.example`, `CLAUDE.md` |
| Add domain | `src/mcp/config/taxonomy.py` | (auto-discovered) |
| Add plugin | `plugins/new/manifest.json` | (auto-discovered by loader) |
| Add Docker service | `stacks/new/docker-compose.yml` | `/docker-compose.yml`, `scripts/validate-env.sh` |
| Change Python deps | `src/mcp/requirements.txt` | Run `make lock-python`, commit both files |
| Change Node deps | `src/web/package.json` | Run `npm install`, commit `package-lock.json` |
| Change frontend types | `src/web/src/lib/types.ts` | `src/web/src/lib/api.ts` (response mapping) |
| Change backend schema | `src/mcp/models/*.py` | `src/web/src/lib/types.ts` (manual sync) |
| Bump Python version | `src/mcp/Dockerfile` | `pyproject.toml`, `.github/workflows/ci.yml` |
| Bump Node version | `src/web/.nvmrc` | `src/web/Dockerfile` |
| Bump test counts | (after adding tests) | `CLAUDE.md`, `README.md` |
| Release version | `pyproject.toml` | Git tag `vX.Y.Z` |

---

## 2. Architecture: Core vs Optional

### Core (cannot be removed)

These components form the foundation and must always be present:

- **Query pipeline** — RAG retrieval, reranking, context assembly
- **Agents** — query, curator, triage, rectify, audit, maintenance, hallucination, memory, self_rag
- **Ingestion** — file parsing, chunking, dedup, ChromaDB + Neo4j storage
- **Chat** — Bifrost-routed LLM conversations with KB context injection
- **KB Admin** — rebuild, rescore, clear, delete, stats endpoints
- **MCP SSE** — Server-Sent Events transport for MCP protocol
- **SDK** — Stable `/sdk/v1/` contract for external consumers
- **Health** — `/health` endpoint with DB connectivity checks

### Feature-gated (env var toggles)

| Feature | Env Var | Default |
|---------|---------|---------|
| Trading integration | `CERID_TRADING_ENABLED` | `false` |
| Multi-user auth | `CERID_MULTI_USER` | `false` |
| Eval harness | `CERID_EVAL_ENABLED` | `false` |
| Ollama proxy | `OLLAMA_ENABLED` | `false` |

### Plugins (auto-discovered)

Plugins in `plugins/` are scanned at startup. Each has a `manifest.json` with name, version, tier, and entry point. Licensed BSL-1.1 (pro tier). Current plugins: OCR, audio, vision, workflow-builder.

### Separate packages

| Package | Location | Deploy |
|---------|----------|--------|
| Desktop (Electron) | `packages/desktop/` | Local build |
| Marketing (Next.js 16) | `packages/marketing/` | Vercel (cerid.ai) |

---

## 3. CI Pipeline

The CI runs 7 jobs on every PR. All must pass to merge.

| Job | Tool | What Breaks It |
|-----|------|----------------|
| **lint** | ruff 0.15.4 | Style violations, import ordering, unused imports |
| **typecheck** | mypy (strict mode) | Type errors, missing annotations, incompatible types |
| **test** | pytest | Test failures, coverage below 70% threshold |
| **security** | detect-secrets, bandit, pip-audit, trivy | Leaked secrets, insecure patterns, vulnerable deps, CVEs |
| **lock-sync** | pip-compile (in Docker) | `requirements.lock` out of sync with `requirements.txt` |
| **frontend** | tsc, eslint, vitest, npm audit | Type errors, lint violations, test failures, vulnerable deps |
| **docker** | hadolint, trivy, build test | Dockerfile lint issues, image CVEs, build failures |

**Common fix:** If `lock-sync` fails after editing `requirements.txt`, run `make lock-python` locally and commit the updated `.lock` file.

---

## 4. Version Coupling Rules

These versions must stay in sync across all locations:

| Component | Version | Locations |
|-----------|---------|-----------|
| Python | 3.11 | `src/mcp/Dockerfile`, `.github/workflows/ci.yml`, `pyproject.toml` |
| Node.js | 22 | `src/web/.nvmrc`, `src/web/Dockerfile`, `.github/workflows/ci.yml` |
| ChromaDB | client `>=0.5,<0.6` | `requirements.txt` (client), `stacks/infrastructure/` (server `chromadb/chroma:0.5.23`) |
| pip-tools | 7.5.3 | CI lock-sync job, local `make lock-python` |

**Breaking mismatch examples:**
- ChromaDB client 0.6.x against server 0.5.x will fail silently on metadata queries
- pip-compile version mismatch produces different lock file hashes, failing CI
- Python version mismatch between Dockerfile and CI causes test divergence

---

## 5. External Consumer Contract

The SDK at `/sdk/v1/` is the stable API consumed by external agents. Changes here are **breaking** and require coordination with downstream consumers.

### Current consumers

| Consumer | Repo | Rate Limit | Client ID |
|----------|------|-----------|-----------|
| cerid-trading-agent | `Cerid-AI/cerid-trading-agent` | 80 req/min | `trading-agent` |
| cerid-boardroom | `Cerid-AI/cerid-boardroom` | 60 req/min | `boardroom-agent` |

### Stable endpoints

- `GET /sdk/v1/health` — Health check
- `POST /sdk/v1/query` — Knowledge base query
- `POST /sdk/v1/hallucination/check` — Hallucination detection
- `POST /sdk/v1/memory/extract` — Memory extraction
- `POST /sdk/v1/trading/signal` — Trading signal analysis (gated)
- `POST /sdk/v1/trading/herd-detect` — Herd behavior detection (gated)
- `POST /sdk/v1/trading/kelly-size` — Kelly criterion sizing (gated)
- `POST /sdk/v1/trading/cascade-confirm` — Cascade confirmation (gated)
- `POST /sdk/v1/trading/longshot-surface` — Longshot opportunity surfacing (gated)

### Rules for SDK changes

1. **Never remove or rename** an existing endpoint
2. **Never change** the response schema of an existing endpoint
3. **New fields** may be added to responses (consumers must tolerate unknown fields)
4. **New endpoints** may be added freely
5. **Deprecation** requires a 2-phase process: mark deprecated in docs, then remove in next major version
6. **All changes** must update `docs/DEPENDENCY_COUPLING.md` and `docs/INTEGRATION_GUIDE.md`
7. **Notify consumers** via GitHub issue before any behavioral change

Consumer configuration lives in `CONSUMER_REGISTRY` in `src/mcp/config/settings.py`, which defines `allowed_domains`, `strict_domains`, and rate limits per consumer. See `docs/INTEGRATION_GUIDE.md` for the full 13-step checklist for adding new agent integrations.

---

## 6. Cross-Platform Development

All scripts and configuration must work on macOS, Linux (Ubuntu/Debian), and Windows (WSL2). Follow these rules:

- **Shell scripts:** Use cross-platform patterns. No macOS-only flags (e.g., `sed -i.bak`, `df -g`, `readlink` without `-f` fallback). Use temp file pattern for in-place sed: `sed '...' "$f" > "$f.tmp" && mv "$f.tmp" "$f"`
- **Port detection:** Use `ss` (Linux) with `lsof` (macOS) fallback, not `lsof` alone
- **Network detection:** Check `ip route` first (Linux/WSL), then `ipconfig` (macOS), then `hostname -I` (Linux fallback)
- **Python paths:** Use `pathlib.Path` or `os.path.expanduser()` for `~` expansion. Never hardcode `/tmp/` -- use `tempfile.gettempdir()`
- **Docker Compose volumes:** Use `${HOME}/` not `~/` for tilde expansion (tilde expansion is not guaranteed in YAML)
- **Test fixtures:** Use `tempfile` module for temp paths, not hardcoded `/tmp/`
- **CI workflows:** Use `$(mktemp)` for temp files, not `/tmp/filename`
- **Disk space checks:** `df -g` is macOS-only. Use `df` with 1K-blocks and convert on Linux

---

## 7. Development Workflow

### Setup

```bash
./scripts/setup.sh                    # First-time guided setup
./scripts/env-unlock.sh               # Decrypt secrets
```

### Start

```bash
./scripts/start-cerid.sh --build      # Build and start all services
```

### Test

```bash
# Python (run in Docker — host macOS lacks chromadb)
docker run --rm -v "$(pwd)/src/mcp:/work" -w /work python:3.11-slim \
  bash -c "pip install -q -r requirements.txt -r requirements-dev.txt && python -m pytest tests/ -v"

# Frontend
cd src/web && npx vitest run
```

### Lint

```bash
cd src/mcp && ruff check .
cd src/web && npx tsc --noEmit && npx eslint .
```

### Lock files

```bash
make lock-python                      # After changing requirements.txt
cd src/web && npm install             # After changing package.json
```

### Pre-PR checklist

1. All tests pass (Python + frontend)
2. Lint clean (`ruff check` + `tsc --noEmit` + `eslint`)
3. Lock files committed if deps changed
4. Test counts updated in `CLAUDE.md` and `README.md` if tests were added
5. `docs/API_REFERENCE.md` updated if endpoints changed
6. `.env.example` updated if env vars added
