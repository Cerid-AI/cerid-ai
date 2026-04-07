# CLAUDE.md - Cerid AI (Open Source)

## Project Overview

Cerid AI is a self-hosted, privacy-first AI Knowledge Companion. RAG-powered retrieval, intelligent agents, and an extensible SDK. Apache-2.0 licensed.

**Version:** 0.82.0 | **Docs:** [`docs/`](docs/) | **SDK:** [`docs/SDK_GUIDE.md`](docs/SDK_GUIDE.md)

## Quick Start

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
| Inference detection | `detect_embedding_provider()` | `utils/inference_config.py` |
| Sidecar client | `sidecar_embed()` / `sidecar_rerank()` | `utils/inference_sidecar_client.py` |

**Rules:**
- Typed errors only (`CeridError` subclasses). No `raise HTTPException` in business logic.
- `@require_feature()` is the only tier gate. No inline tier checks.
- Constants in `config/settings.py`. No magic numbers.
- Every `except` must log + degrade or raise typed error.
- HTTP client is `httpx` everywhere — `requests` is not a dependency.

## Tiered Inference

GPU-aware embedding/reranking. Auto-selects the best backend at startup:

| Tier | Provider | When |
|------|----------|------|
| Optimal | `fastembed-sidecar` or `onnx-gpu` | GPU available (Metal/CUDA/ROCm) |
| Good | `ollama` or CPU sidecar | Ollama running or native sidecar without GPU |
| Degraded | `onnx-cpu` | Docker CPU (default) |

```bash
curl http://localhost:8888/health | jq .inference     # check tier
bash scripts/install-sidecar.sh                        # install GPU sidecar
python scripts/cerid-sidecar.py                        # run sidecar (outside Docker)
```

Re-check loop runs every 300s — auto-detects Ollama start/stop mid-session.

## Dependency Strategy

Direct dependencies are minimal (14 core). See [`docs/DEPENDENCY_AUDIT_2026-04-05.md`](docs/DEPENDENCY_AUDIT_2026-04-05.md).

**Optional extras (not installed by default):**
```bash
pip install pytesseract Pillow    # OCR plugin (+ apt install tesseract-ocr)
pip install bcrypt PyJWT          # Multi-user JWT auth (CERID_MULTI_USER=true)
```

**Protected dependencies (do NOT remove):**
- `langgraph` — real conditional routing graph in triage.py (469 lines, 16 functions)
- `pandas` — CSV enrichment with auto-delimiter, encoding fallback, df.describe()
- `react-syntax-highlighter` — PrismLight with 25 languages (~200KB runtime chunk)

**Docker image (3.18 GB):** No tesseract/ffmpeg in base. Set `CERID_PRELOAD_MODELS=false` at build time for ~500MB image (models download on first use).

## SDK (12 endpoints at /sdk/v1/)

See [`docs/SDK_GUIDE.md`](docs/SDK_GUIDE.md). Core: query, hallucination, memory/extract, health, ingest, search, collections, taxonomy, settings, plugins, health/detailed, ingest/file.

## Plugin System (5 types)

See [`docs/PLUGIN_DEVELOPMENT.md`](docs/PLUGIN_DEVELOPMENT.md). Types: `parser`, `agent`, `tool`, `connector`, `sync`.

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

## CI (7 jobs)

lint, typecheck, test (20% floor), security, lock-sync, frontend, docker.
