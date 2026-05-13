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
| **Clean architecture (v0.91)** | ✅ 35 integration tests + canonical models | Growing           | Growing          | Older            |
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
- **Quenchforge AMD-GPU Routing** (v0.93.8) — Intel Mac + AMD discrete GPU operators get GPU acceleration for LLM chat, dense embeddings, cross-encoder reranking, AND ingest-time enrichment via per-workload env-driven routing (`INTERNAL_LLM_PROVIDER` / `EMBEDDINGS_PROVIDER` / `RERANK_PROVIDER`). See [`docs/AMD_GPU_MODEL_RECOMMENDATIONS.md`](docs/AMD_GPU_MODEL_RECOMMENDATIONS.md) for vetted GGUF picks by VRAM tier.
- **`/health.inference_routing`** — Five-key introspection of the active inference provider per workload (LLM / embed / rerank / sparse / NLI). Operators verify their env vars actually reached the container.
- **Hybrid Retrieval** — dense bi-encoder + BM25 + SPLADE-v3 learned-sparse, RRF-fused across all three retrievers
- **Adaptive Configuration Recommender** — Settings pane surfaces gated retrieval features (sparse, HyPE, parent-child, RRF) once your corpus crosses a feature-specific threshold; three-action dismissal matches GitHub's notification model
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

## Recent Highlights (v0.93.x — May 2026)

- **v0.93.8 — The GPU release.** End-to-end Quenchforge routing on Intel Mac + AMD discrete GPU. Per-workload env-driven dispatch (`EMBEDDINGS_PROVIDER`, `RERANK_PROVIDER`) + ingest enrichment migration (per-chunk contextual summaries, categorization, curator synopsis) + Settings UI surface + `/health.inference_routing` introspection. AMD GPU model recommendation matrix at [`docs/AMD_GPU_MODEL_RECOMMENDATIONS.md`](docs/AMD_GPU_MODEL_RECOMMENDATIONS.md). 4411 Python + 1116 frontend tests.
- **v0.93.6 — Quenchforge integration merge.** Hardware-aware backend recommendation (Mac IOKit GPU detection) + Quenchforge as a routable LLM provider + setup-wizard surfaces (BackendRecommendationStep, QuenchforgeInstallStep, TelemetryConsentStep) + cascade rerank + sentence-window chunker + four advanced inference flags.
- **v0.93.5 — Chat virtualization + L4 backend + Dependabot batch.** `@tanstack/react-virtual` exact-pinned to a pre-supply-chain-attack version, three-tier rendering with shared MessageRow component, recommender surfaces toggle at 200-message conversations. L4 ("Full ephemeral") Private Mode contract closed end-to-end. 11 Dependabot bumps absorbed.
- **v0.93.3 — SPLADE-v3 sparse retrieval + adaptive recommender.** Third retriever alongside dense + BM25, RRF-fused via `tri_rrf`. General adaptive-recommendation engine surfaces gated features at corpus-size thresholds (sparse / HyPE / parent-child @ 100 docs, RRF @ 500). Pivoted from BGE-M3 per literature evidence (smaller, faster, better quality on BEIR).
- **v0.93.0–v0.93.2 — RAG Cycle 1-3.** HyPE wiring fixes, Obsidian-style wikilink + frontmatter + vault profile ingestion, bidirectional vault writeback with `cerid-synthesis` loop-breaker.
- **`benchmark-slo` is a PR-blocking merge gate.** Real-OpenRouter latency drift now fails CI alongside the deterministic budget-plumbing tests.
- **`/sdk/v1/memory/extract` SLO bounded.** Per-stage `asyncio.wait_for` budgets on the three internal LLM calls + a server-side `MEMORY_QUEUE_MODE=async` path that returns 202 + `Location` header; callers poll `GET /sdk/v1/memory/extract/jobs/{job_id}`. The sync `?wait=true` escape hatch preserves binary compatibility.
- **Pro-tier Stripe checkout end-to-end.** Hosted Checkout flow shipped; webhook coverage extends to `customer.subscription.updated` (deactivates on `past_due` / `unpaid` / `canceled` / `incomplete_expired`).
- **`mode=fast | thorough` on `/agent/hallucination`.** Fast mode skips cross-model NLI entirely, returns claims marked `status='uncertain'` with `nli_skipped=true` — useful for post-fact annotations that don't want to wait 60-100s.
- **`slo_budget_ms` on `/sdk/v1/llm/complete`.** Smart-router filters tiers by their empirical p95 latency profile; if no tier fits, returns `503` + `Retry-After`. Never silently downgrades.
- **Schema contracts hardened.** Object envelope on `/agent/memory/recall`; `min_length=1` on required `conversation_id` fields. Drift gate keeps every constraint stable across releases.
- **Python 3.12 runtime.** Dockerfile `python:3.12.13-slim-trixie`, pyproject `requires-python = ">=3.12"`, full CI matrix on 3.12.
- **Layout-aware retrieval default ON.** `+0.05 MRR / +0.024 NDCG@10 / faster latency` against the live eval-corpus; nightly `eval-exploratory.yml` workflow + BEIR seed plumbing for ongoing drift detection.

### Previously (v0.91 — May 2026)

- **`benchmark-slo` is a PR-blocking merge gate.** Real-OpenRouter latency drift fails CI alongside the deterministic budget-plumbing tests.
- **`/sdk/v1/memory/extract` SLO bounded** via per-stage `asyncio.wait_for` budgets + async queue mode.
- **Pro-tier Stripe checkout end-to-end.** Hosted Checkout flow shipped; webhook coverage extends to `customer.subscription.updated`.
- **Layout-aware retrieval default ON.** `+0.05 MRR / +0.024 NDCG@10` against the live eval-corpus.
- **Python 3.12 runtime.** Dockerfile `python:3.12.13-slim-trixie`.

### Previously (v0.90 — April 2026)

- Nine-sprint consolidation: canonical `ClaimVerification` Pydantic model, bridge modules retired, `src/mcp/services/` + `src/mcp/agents/` directories deleted.
- 35 preservation invariants as a merge gate; `preservation` + every drift gate are blocking.
- `/sdk/v1/*` OpenAPI contract baseline + drift check.
- Silent-catch observability contract enforced.
- Streaming verification auto-persist.

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
