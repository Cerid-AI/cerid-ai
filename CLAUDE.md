# CLAUDE.md - Cerid AI (Open Source)

## Global Configuration (REQUIRED)

This repo's Claude Code sessions **MUST** also load the global orchestrator config:
→ **https://github.com/sunrunnerfire/dotfiles/blob/main/claude.md**

Load both this file AND the global config at session start. The global config contains lead orchestrator/agent parameters that govern all Cerid development sessions.

---

## Project Overview

Cerid AI is a self-hosted, privacy-first AI Knowledge Companion. RAG-powered retrieval, intelligent agents, and an extensible SDK. Apache-2.0 licensed.

**Status:** v0.82 | **Docs:** [`docs/`](docs/) | **SDK:** [`docs/SDK_GUIDE.md`](docs/SDK_GUIDE.md)

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

| Inference detection | `detect_embedding_provider()` | `utils/inference_config.py` |
| Sidecar HTTP client | `sidecar_embed()` / `sidecar_rerank()` | `utils/inference_sidecar_client.py` |
| Startup prereqs | Port checks, Docker memory, python3/curl | `scripts/start-cerid.sh` |
| Sidecar auto-start | Detect installed → start in background | `scripts/start-cerid.sh` |

**Rules:**
- Typed errors only (`CeridError` subclasses). No `raise HTTPException` in business logic.
- `@require_feature()` is the only tier gate. No inline tier checks.
- Constants in `config/constants.py`. No magic numbers.
- Every `except` must log + degrade or raise typed error.
- HTTP client is `httpx` everywhere — `requests` is not a dependency.

## Tiered Inference

GPU-aware embedding/reranking provider detection. Auto-selects the best backend at startup:

| Tier | Provider | When |
|------|----------|------|
| Optimal | `fastembed-sidecar` or `onnx-gpu` | GPU available (Metal/CUDA/ROCm) |
| Good | `ollama` or CPU sidecar | Ollama running or native sidecar without GPU |
| Degraded | `onnx-cpu` | Docker CPU (default) |

```bash
# Check current inference tier
curl http://localhost:8888/health | jq .inference

# Override manually
INFERENCE_MODE=onnx-cpu  # or: onnx-gpu, ollama, fastembed-sidecar

# Install sidecar for native GPU acceleration
bash scripts/install-sidecar.sh
python scripts/cerid-sidecar.py  # runs outside Docker
```

Key files: `utils/inference_config.py` (detection), `utils/inference_sidecar_client.py` (HTTP client), `scripts/cerid-sidecar.py` (server).

Re-check loop runs every 300s — auto-detects Ollama start/stop mid-session.

## Dependency Strategy

The public repo keeps direct dependencies minimal for attack surface and image size. See [`docs/DEPENDENCY_AUDIT_2026-04-05.md`](docs/DEPENDENCY_AUDIT_2026-04-05.md) for the full audit.

**Core deps (14 direct):** fastapi, uvicorn, pydantic, httpx, chromadb, neo4j, redis, tiktoken, langgraph, pdfplumber, python-docx, openpyxl, pandas, apscheduler + utilities (cryptography, jinja2, mcp, python-multipart, bm25s, PyStemmer, sentry-sdk).

**What's NOT a direct dependency:**
- `requests` — replaced with `httpx` everywhere (scripts, server, CLI tools)
- `structlog` — replaced with stdlib `logging`
- `stripe` — internal-only (billing), not in public repo
- `faster-whisper` — internal-only (audio plugin), not in public repo
- `pytesseract` / `Pillow` — Pro-only OCR plugin, optional install
- `bcrypt` / `PyJWT` — only for multi-user mode (`CERID_MULTI_USER=true`), optional install

**Optional extras (install separately):**
```bash
pip install pytesseract Pillow          # OCR plugin (+ apt install tesseract-ocr)
pip install faster-whisper              # Audio transcription plugin (+ ffmpeg)
pip install stripe                      # Billing/subscription management
pip install bcrypt PyJWT                # Multi-user JWT authentication
```

**Docker image (3.18 GB):** Base image has no tesseract-ocr or ffmpeg — Pro plugins install their own system deps. ONNX models are pre-downloaded at build time for fast cold starts.

**Auth imports are graceful:** `routers/auth.py` and `middleware/jwt_auth.py` are only loaded when `CERID_MULTI_USER=true` (conditional import in `main.py`). Missing bcrypt/PyJWT won't crash the server.

**Protected Dependencies (do NOT remove):**
- `langgraph` — triage.py is 469 lines with 16 functions building a real conditional routing graph. Reimplementing would lose graph execution, error propagation, and routing visualization.
- `pandas` — CSV parser uses pd.read_csv for auto-delimiter detection, encoding fallback, column type inference, df.describe() statistics. These enrich KB artifacts. Already lazy-imported.
- `react-syntax-highlighter` — uses PrismLight with 25 registered languages (~200KB lazy chunk). npm install size is large but runtime bundle is small via tree-shaking + Vite manual chunks.

**Embedding runs on Docker CPU by default** but auto-detects GPU acceleration:
- Ollama on host → uses Ollama for LLM tasks
- FastEmbed sidecar on host → native GPU embeddings (Metal/CUDA/ROCm)
- See [Tiered Inference](#tiered-inference) section above

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
