# CLAUDE.md - Cerid AI (Open Source)

## Project Overview

Cerid AI is a self-hosted, privacy-first AI Knowledge Companion. RAG-powered retrieval, intelligent agents, and an extensible SDK. Licensed FSL-1.1-ALv2 (source-available, becomes Apache-2.0 at two years); the SDKs and client integrations are Apache-2.0 and the plugin trees are BUSL-1.1 — see [`CONTRIBUTING.md`](CONTRIBUTING.md#license) for the per-path table.

**Version:** 1.0.4 | **Changes:** [`CHANGELOG.md`](CHANGELOG.md) | **Docs:** [`docs/`](docs/) | **SDK:** [`docs/SDK_GUIDE.md`](docs/SDK_GUIDE.md) | **Conventions:** [`docs/CONVENTIONS.md`](docs/CONVENTIONS.md)

## Quick Start

```bash
cp .env.example .env                        # configure (set OPENROUTER_API_KEY)
./scripts/start-cerid.sh                    # start stack
./scripts/start-cerid.sh --build            # rebuild after code changes
curl http://localhost:8888/health           # verify
```

`--force` bypasses the pre-flight checks, `--legacy` uses the older four-step compose startup, and `--reclaim` takes over container names held by a foreign project directory.

## Architecture

| Service | Port | Path | Notes |
|---------|------|------|-------|
| MCP Server (API) | 8888 | `src/mcp/` | |
| React GUI | 3000 | `src/web/` | container serves on 80 |
| ChromaDB | 8001 | `stacks/infrastructure/` | container 8000 |
| Neo4j | 7474 / 7687 | `stacks/infrastructure/` | HTTP / Bolt |
| Redis | 6379 | `stacks/infrastructure/` | |
| Ollama | 11434 | — | `ollama` compose profile only |
| SearXNG | 8080 | — | `searxng` compose profile only |

Every published port binds `127.0.0.1` by default — `CERID_BIND_ADDR` widens that, deliberately and never by accident. Each port is overridable (`CERID_PORT_MCP`, `CERID_PORT_GUI`, …).

Chat + smart-router traffic goes straight from `core/utils/llm_client.py` to OpenRouter. No proxy layer.

## Key Patterns

| Concern | Pattern | Location |
|---------|---------|----------|
| Error handling | `@handle_errors()` | `utils/error_handler.py` |
| Feature gating | `@require_feature()` | `config/features.py` |
| Circuit breakers | `circuit_breaker(name)` | `core/utils/circuit_breaker.py` |
| Graceful degradation | `DegradationManager` | `utils/degradation.py` |
| Inference detection | `detect_embedding_provider()` | `utils/inference_config.py` |
| Stage classification | `INTERACTIVE_STAGES`, `is_background_stage()` | `config/stage_profiles.py` |
| NLI entailment | `nli_entailment()` | `core/utils/nli.py` |
| Swallowed-error observability | `log_swallowed_error(module, exc)` | `core/utils/swallowed.py` |

**Rules:**
- Typed errors only (`CeridError` subclasses). No `raise HTTPException` in business logic.
- `@require_feature()` is the only tier gate. No inline tier checks.
- Constants in `config/constants.py`. No magic numbers.
- Every `except` must log + degrade or raise typed error.
- HTTP client is `httpx` everywhere — `requests` is not a dependency.

## Local inference: tier, profile, priority

Three separate mechanisms. They are easy to conflate and they answer different questions.

**Tier — what hardware is here.** `detect_embedding_provider()` picks the embedding/rerank backend at startup:

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

**Profile — what routing defaults suit that hardware.** `CERID_ENVIRONMENT_PROFILE` is `cloud-first`, `hybrid`, or `local-only` (`config/environment_profiles.py`, documented in [`docs/ENVIRONMENT_PROFILES.md`](docs/ENVIRONMENT_PROFILES.md)). An operator-pinned value always wins over the profile's default. Without a cloud key, or under Private Mode L1 and above, the profile degrades to `local-only` — at settings load *and* again at call time, because the key can go away between the two. `INTERNAL_LLM_MODEL_BACKGROUND` names a smaller model for background stages.

**Priority — who gets the local model first.** `_PriorityGate` (`core/utils/internal_llm.py`) admits interactive stages — chat, memory extraction, claim extraction, MCP tools — ahead of ingest, streaming included. The interactive and background lists are derived from one classification in `config/stage_profiles.py` so they cannot drift apart.

A boot-time probe (`probe_local_throughput`, `utils/inference_config.py`) measures the local chat model's real prompt and generation rates and derives the per-function expectations shown in the setup wizard and Settings → System. It is retaken on every recheck, and a probe taken under load never overwrites a quiet one.

On a Mac with an AMD discrete GPU (where stock Ollama stays CPU-bound), use
[quenchforge](https://github.com/Cerid-AI/quenchforge) — an Ollama- and
OpenAI-compatible inference server tuned for exactly that hardware:
`brew install cerid-ai/tap/quenchforge`. Cerid talks to it on the standard
`:11434` port with no configuration changes.

## Dependency Strategy

43 direct runtime dependencies in `src/mcp/requirements.txt`. Each pin carries a comment saying *why* it sits where it does (CVE floor, license constraint, API break) — preserve that comment when you move a pin. Lock files are generated: edit `requirements.txt`, then `make lock-python`.

**Optional extras (not installed by default):**
```bash
pip install pytesseract Pillow    # OCR plugin (+ apt install tesseract-ocr)
pip install bcrypt PyJWT          # Multi-user JWT auth (CERID_MULTI_USER=true)
```

**Protected dependencies (do NOT remove):**
- `langgraph` — the real conditional routing graph in `app/agents/triage.py`, not a wrapper
- `pandas` — CSV enrichment in `app/parsers/structured.py` (auto-delimiter, encoding fallback, `df.describe()`)
- `react-syntax-highlighter` — PrismLight with the registered language set (~200KB runtime chunk)

## SDK (17 endpoints at /sdk/v1/)

See [`docs/SDK_GUIDE.md`](docs/SDK_GUIDE.md); the contract is [`docs/openapi-sdk-v1.json`](docs/openapi-sdk-v1.json), generated by `scripts/gen_sdk_openapi.py` and re-checked by `make drift-check`. Endpoints: `query`, `search`, `hallucination`, `memory/extract` (+ `memory/extract/jobs/{job_id}`), `llm/complete`, `ingest` (`/file`, `/external`, `/voice-note`, `/webhook/{token}`), `collections`, `taxonomy`, `settings`, `plugins`, `health`, `health/detailed`.

## MCP Tools (55)

Most tools register through `@register_tool` in `app/tool_registry.py`, with implementations under `app/mcp_tools/`; the remainder are the legacy `MCP_TOOLS` list in `app/tools.py`. `tools/list` at the MCP handshake is the always-current inventory — `docs/API_REFERENCE.md` is representative, not exhaustive. `test_tool_inventory_meets_minimum` pins the floor so a silent registry truncation lands in CI; bump it each release.

## Plugin System (5 types)

Base classes in `src/mcp/plugins/base.py`, all extending `CeridPlugin`: `ParserPlugin`, `AgentPlugin`, `ToolPlugin`, `ConnectorPlugin`, `SyncBackendPlugin`. Bundled plugins live under `plugins/`. See [`docs/PLUGIN_DEVELOPMENT.md`](docs/PLUGIN_DEVELOPMENT.md).

## Tests and local gates

The `make` targets assume a local `.venv` on Python 3.12.

```bash
make test            # backend suite, fast tier
make test-all        # the whole suite, no marker filter, eval tier included
make ci-local        # ruff + mypy + import contracts + tests + eslint/tsc/vitest + secrets + supply-chain guard
make drift-check     # generated docs and manifests match their generators (NOT in ci-local)
make prepush         # FULL parity with remote CI — run this before every push
make mutation-check  # do the tests DETECT faults? survivors are blind spots, not percentages
```

Without a venv, the backend suite runs in Docker:

```bash
docker run --rm -v "$(pwd)/src/mcp:/work" -w /work python:3.12-slim \
  bash -c "pip install -q -r requirements.txt -r requirements-dev.txt && python -m pytest tests/ -v"

cd src/web && npx vitest run      # frontend
```

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md); security policy in [`SECURITY.md`](SECURITY.md).

**Sync points when making changes:**
- New MCP tool → `@register_tool` in `app/tool_registry.py` (implementation under `app/mcp_tools/`) + tool count in README + the floor in `test_mcp_tool_schema_fidelity.py`
- New endpoint → `main.py` + `docs/API_REFERENCE.md`; SDK routes also regenerate `docs/openapi-sdk-v1.json`
- New env var → `settings.py` + `.env.example` (generated by `scripts/gen_env_example.py` — `make drift-check` catches a missed one)
- Python deps → `requirements.txt` then `make lock-python`

## CI (10 jobs)

`changes`, `lint`, `typecheck`, `test` (20% coverage floor), `security`, `lock-sync`, `frontend`, `license-scan`, `docker`, `ci-ok`. A separate `supply-chain-guard` workflow is a required check on `main`; the SDK release workflows publish the Python and TypeScript clients.
