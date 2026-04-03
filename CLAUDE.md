# CLAUDE.md - Cerid AI (Open Source)

## Project Overview

Cerid AI is a self-hosted, privacy-first AI Knowledge Companion. RAG-powered retrieval, intelligent agents, and an extensible SDK. Apache-2.0 licensed.

**Status:** v0.80 | **Docs:** [`docs/`](docs/) | **SDK:** [`docs/SDK_GUIDE.md`](docs/SDK_GUIDE.md)

## Quick Reference

```bash
cp .env.example .env                        # configure (set OPENROUTER_API_KEY)
./scripts/start-cerid.sh                    # start stack
./scripts/start-cerid.sh --build            # rebuild after code changes
curl http://localhost:8888/health/ready      # verify
```

## Architecture

| Service | Port | Path |
|---------|------|------|
| MCP Server (API) | 8888 | `src/mcp/` |
| Bifrost (LLM Gateway) | 8080 | `stacks/bifrost/` |
| ChromaDB | 8001 | `stacks/infrastructure/` |
| Neo4j | 7474 | `stacks/infrastructure/` |
| Redis | 6379 | `stacks/infrastructure/` |
| React GUI | 3000 | `src/web/` |

## Key Patterns

| Concern | Pattern | Location |
|---------|---------|----------|
| Error handling | `@handle_errors()` | `utils/error_handler.py` |
| Feature gating | `@require_feature()` | `config/features.py` |
| Circuit breakers | `circuit_breaker(name)` | `utils/circuit_breaker.py` |
| Graceful degradation | `DegradationManager` | `utils/degradation.py` |

**Rules:**
- Typed errors only (`CeridError` subclasses). No `raise HTTPException` in business logic.
- `@require_feature()` is the only tier gate. No inline tier checks.
- Constants in `config/constants.py`. No magic numbers.
- Every `except` must log + degrade or raise typed error.

## SDK (12 endpoints at /sdk/v1/)

See [`docs/SDK_GUIDE.md`](docs/SDK_GUIDE.md). Core: query, hallucination, memory/extract, health, ingest, search, collections, taxonomy, settings, plugins, health/detailed, ingest/file.

## Plugin System (5 types)

See [`docs/PLUGIN_DEVELOPMENT.md`](docs/PLUGIN_DEVELOPMENT.md) and [`plugins/README.md`](plugins/README.md).

Types: `parser`, `agent`, `tool`, `connector`, `sync`. Community plugins loaded from `CERID_PLUGIN_DIR` or `plugins/` directory.

## Tests

```bash
# Python (in Docker)
docker run --rm -v "$(pwd)/src/mcp:/work" -w /work python:3.11-slim \
  bash -c "pip install -q -r requirements.txt -r requirements-dev.txt && python -m pytest tests/ -v"

# Frontend
cd src/web && npx vitest run
```

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) and [`DEVELOPMENT.md`](DEVELOPMENT.md).

**Sync points when making changes:**
- New MCP tool → `tools.py` + tool count in README
- New endpoint → `main.py` + `docs/API_REFERENCE.md`
- New env var → `settings.py` + `.env.example`
- Python deps → `requirements.txt` then `make lock-python`

## Compliance

No Chinese-origin AI models (USG alignment). Approved: OpenAI, Anthropic, Google, xAI, Meta, Microsoft, Mistral.

## CI (6 jobs)

lint, typecheck, test (60% floor), security, frontend, docker.
