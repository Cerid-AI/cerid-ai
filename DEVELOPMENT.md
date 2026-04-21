# Development Guide

Quick reference for contributors working on Cerid AI.

---

## Prerequisites

- Docker & Docker Compose v2+
- Python 3.11
- Node.js 22 (see `.nvmrc`)
- `age` encryption tool (`brew install age` on macOS)
- OpenRouter API key

---

## Starting the Stack

```bash
# Decrypt secrets (first time only, requires age key)
./scripts/env-unlock.sh

# Start all services (Infrastructure -> Bifrost -> MCP -> React GUI)
./scripts/start-cerid.sh

# Rebuild after code changes
./scripts/start-cerid.sh --build

# Validate environment (14 checks)
./scripts/validate-env.sh
./scripts/validate-env.sh --quick   # containers only
./scripts/validate-env.sh --fix     # auto-start missing services
```

Startup order: Infrastructure (Neo4j, ChromaDB, Redis) -> Bifrost -> MCP -> React GUI.

---

## Running Tests

**Python (backend):**
```bash
docker run --rm -v "$(pwd)/src/mcp:/work" -w /work python:3.11-slim \
  bash -c "pip install -q -r requirements.txt -r requirements-dev.txt && python -m pytest tests/ -v"
```

**Frontend:**
```bash
cd src/web && npx vitest run
```

**All checks (lint + typecheck + tests):**
```bash
make check-all
```

**Retrieval evaluation (Monte Carlo eval harness):**
```bash
make test-eval
```

**Integration tests (requires running Docker stack):**
```bash
python -m pytest tests/test_e2e_integration.py
```

**RAG resilience testing:**
```bash
python -m pytest tests/test_rag_resilience.py -v
```

### Synthetic Test Fixtures

Synthetic test data lives in `tests/fixtures/synthetic/`. These fixtures provide deterministic, reproducible test inputs for unit and integration tests without requiring a live knowledge base. Use them for testing retrieval pipelines, deduplication logic, and context assembly.

---

## Dependency Management

```bash
make lock-python       # Regenerate requirements.lock after editing requirements.txt
make lock-python-dev   # Regenerate requirements-dev.lock
make lock-all          # Both
make install-hooks     # Git pre-commit hook (lock file sync check)
make deps-check        # Verify all lock files are current
```

Cross-service version constraints: see `docs/DEPENDENCY_COUPLING.md`.

---

## Secrets Management

Single `.env` file at repo root, encrypted with `age`. Key at `~/.config/cerid/age-key.txt`.

```bash
./scripts/env-unlock.sh    # Decrypt .env.age -> .env
./scripts/env-lock.sh      # Re-encrypt after editing
```

---

## Configuration

| File | Purpose |
|------|---------|
| `.env` | All secrets (encrypted as `.env.age`) |
| `src/mcp/config/settings.py` | Domains, tiers, URLs, sync, model IDs |
| `src/mcp/config/taxonomy.py` | Domain taxonomy and sub-categories |
| `src/mcp/config/features.py` | Feature flags and tier gating |
| `stacks/bifrost/data/config.json` | LLM routing and provider config |

---

## Verification

```bash
curl http://localhost:8888/health
curl http://localhost:8888/collections
curl http://localhost:8888/artifacts
```

---

## What to Update When Making Changes

See `docs/CONTRIBUTING.md` for the full sync reference. Key points:

- **New MCP tool** -- update `tools.py` + tool count in `README.md`
- **New endpoint** -- register in `main.py` + update `docs/API_REFERENCE.md`
- **New env var** -- add to `settings.py` + `.env.example`
- **Python deps** -- edit `requirements.txt` then `make lock-python`
- **Backend schema change** -- manually update `src/web/src/lib/types.ts`
- **Version bump** -- `pyproject.toml` + git tag

---

## CI Pipeline (8 jobs)

| Job | What |
|-----|------|
| lint | `ruff check src/mcp/` |
| typecheck | `mypy src/mcp/` |
| test | pytest (70% coverage floor) + Codecov upload + license audit |
| security | detect-secrets + bandit + pip-audit + dlint ReDoS |
| lock-sync | pip-compile lock file freshness check |
| frontend | tsc + ESLint + Vitest + Vite build + bundle size check (800KB limit) |
| docker | hadolint + `docker build` + Trivy scan |
| frontend-desktop | npm ci + `npm run typecheck` |

## Platform Notes

### macOS (ARM + Intel)
- Docker Desktop uses virtiofs for mounts — `Errno 35` handled via retry (see `OPERATIONS.md`)
- Ollama: install natively for Metal GPU acceleration. Containers access via `host.docker.internal:11434`
- RAM: Docker reports VM memory, not host. `start-cerid.sh` sets `HOST_MEMORY_GB` via `sysctl`
- GPU: Metal is not accessible from Linux containers. Use host Ollama for GPU inference

### Linux (x86_64 + ARM64)
- Native Docker Engine — no virtiofs issues
- Ollama: host install, `localhost:11434`. NVIDIA GPU via Container Toolkit
- ARM64: ChromaDB ONNX may lack AVX2 — set `REBUILD_HNSWLIB=1` if embedding fails
- SELinux (Fedora/RHEL): volume mounts may need `:z` suffix

### Windows (WSL2)
- Requires WSL2 backend for Docker Desktop
- Ollama: install inside WSL2. Access at `localhost:11434`
- Keep data dirs on WSL2 filesystem (not `/mnt/c/`) for performance
- Recommended `.wslconfig`: `memory=12GB` minimum
