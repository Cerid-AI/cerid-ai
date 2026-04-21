# Cerid AI

**Self-Hosted Personal AI Knowledge Companion**

A privacy-first, local-first workspace that unifies your code, finance, projects, and personal artifacts into a context-aware LLM interface with RAG-powered retrieval, intelligent agents, and built-in hallucination detection.

[![Status](https://img.shields.io/badge/Status-Active-green)]()
[![License](https://img.shields.io/badge/License-Apache%202.0-blue)](LICENSE)
[![CI](https://github.com/Cerid-AI/cerid-ai/actions/workflows/ci.yml/badge.svg)](https://github.com/Cerid-AI/cerid-ai/actions/workflows/ci.yml)

---

## 5-minute quickstart

If you have Docker and an OpenRouter API key (or Ollama), you can have a running private AI knowledge base in under 5 minutes.

```bash
git clone https://github.com/Cerid-AI/cerid-ai.git && cd cerid-ai
cp .env.example .env
# Add your OPENROUTER_API_KEY (or set up Ollama)
./scripts/setup-archive.sh
./scripts/start-cerid.sh
```

Open http://localhost:3000 — the React GUI is ready. Drop files into `~/cerid-archive/` and watch them get ingested with automatic verification.

**It's working when** the status bar shows green dots for ChromaDB, Neo4j, and Redis.

---

## Why Cerid?

Most self-hosted AI tools are either basic RAG wrappers or bloated agent frameworks. Cerid is different:

| Feature                        | Cerid AI                          | AnythingLLM          | Mem0              | PrivateGPT       |
|--------------------------------|-----------------------------------|----------------------|-------------------|------------------|
| **Hallucination detection**    | ✅ Built-in claim verification + NLI | ❌                    | ❌                 | ❌                |
| **Memory extraction**          | ✅ Auto-extract facts/decisions from chat | Basic              | Core feature     | ❌                |
| **9 specialized agents**       | ✅ Query, Triage, Rectify, Audit, Hallucination, Memory, etc. | Limited           | None             | None             |
| **Tiered local inference**     | ✅ Ollama + GPU sidecar + auto-fallback | Basic             | None             | Basic            |
| **Graph + vector + BM25**      | ✅ Full hybrid with Neo4j relationships | Vector only       | Vector only      | Vector only      |
| **Clean architecture (v0.90)** | ✅ 35 integration tests + canonical models | Growing           | Growing          | Older            |
| **5-min Docker start**         | ✅ One-command                    | ✅                   | ✅                | ✅                |
| **Multi-domain KB**            | ✅ coding / finance / projects / personal | ✅                | Limited          | ✅                |

**Built for people who want their AI to be trustworthy, not just fast.**

---

## Key Capabilities

- **React GUI** at :3000 — streaming chat, knowledge browser, monitoring dashboards
- **9 Intelligent Agents** — Query (LLM reranking), Triage (LangGraph), Hallucination Detection, Memory Extraction, Maintenance, Audit, and more
- **21 MCP Tools** — Full control via MCP protocol (`pkb_*` namespace)
- **Hallucination Detection** — Extracts claims from responses and verifies them against your KB using NLI + source attribution
- **Memory System** — Automatically extracts facts, decisions, and preferences from conversations
- **Tiered Inference** — Auto-detects Ollama (GPU/CPU), FastEmbed sidecar, or Docker CPU fallback
- **Hybrid Search** — BM25 + vector + knowledge graph traversal
- **File Ingestion** — 30+ formats (PDF with tables, DOCX, code, Obsidian vaults, etc.)
- **Multi-Machine Sync** — Optional Dropbox JSONL sync (encrypted)
- **Full Observability** — Health checks, cost tracking, queue depth, swallowed error counters

All data stays local. Only LLM API calls leave your machine.

---

## Architecture (high level)

```
User → React GUI (:3000)
         ↓
MCP Server (:8888) — FastAPI + 9 agents + hybrid retrieval
         ↓
ChromaDB (vectors) + Neo4j (graph) + Redis (cache + audit)
```

Core is cleanly separated from app layer (Phase C architecture). 35 integration tests guard every capability on every commit.

---

## Quick Start

Just run the commands in the [5-minute quickstart](#5-minute-quickstart) above.

**Requirements**
- Docker + Docker Compose v2+
- OpenRouter API key (recommended) **or** Ollama running locally
- macOS or Linux (Windows via WSL2 works)

**After starting**
- GUI: http://localhost:3000
- API docs: http://localhost:8888/docs
- Health: `curl http://localhost:8888/health`

---

## REST API & MCP Tools

Full list in [API_REFERENCE.md](docs/API_REFERENCE.md). Highlights:

- `POST /agent/query` — Multi-domain RAG with reranking + optional Self-RAG
- `POST /agent/hallucination` — Verify any LLM response against your KB
- `POST /agent/verify-stream` — Same verification, streamed as SSE with auto-persisted reports
- `POST /agent/memory/extract` — Pull facts from conversation history
- 21 MCP tools (`pkb_*`) for programmatic access

---

## Recent Highlights (v0.90 — April 2026)

- **Nine-sprint consolidation.** Zero shape-contract drift. Canonical `ClaimVerification` Pydantic model. Bridge modules retired. `src/mcp/services/` and `src/mcp/agents/` directories deleted.
- **One canonical data-layer path.** Neo4j code lives at `app/db/neo4j/` only; the legacy `db/neo4j/` shim tree is gone, guarded by a CI path-existence check (`lint / no-legacy-neo4j-tree`).
- **35 preservation invariants as a merge gate.** Integration tests boot a live stack and run on every PR. `preservation`, `sync-manifest-drift`, `router-registry-drift`, `sdk-openapi-drift`, `env-example-drift`, and `silent-catch` are all blocking — no soft-warning CI gates remain.
- **Observability contract, enforced.** Silent-catch allowlist shrunk 127 → 64 across 63 call-site rewrites to `log_swallowed_error`; every broad-catch surfaces at `/health.swallowed_errors_last_hour`.
- **`/sdk/v1/*` under contract.** Committed OpenAPI baseline + drift check; a single source of truth for `SDK_VERSION`.
- **Streaming verification auto-persist.** `verify_response_streaming` now saves reports after the retry-sweep + consistency checks settle; new `persisted:{success}` SSE event.
- **Clean inference routing.** Full Bifrost retirement; Ollama + sidecar path is the only supported shape.
- **Agent-friendly context.** `CLAUDE.md` trimmed to just architectural directives — the long form lives in `docs/ARCHITECTURE.md` / `docs/CONVENTIONS.md` / `docs/PRESERVATION.md`.

---

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [API Reference](docs/API_REFERENCE.md)
- [Tiered Inference Architecture](docs/TIERED_INFERENCE_ARCHITECTURE.md)
- [Changelog](CHANGELOG.md)
- [Contributing](CONTRIBUTING.md)

---

## License

Apache 2.0 (core + app). Plugins use BSL-1.1 (convert to Apache after 3 years).

**Star the repo** if this is useful — it helps more people discover private, trustworthy AI tools.

Built with ❤️ in Fairfax, VA.
