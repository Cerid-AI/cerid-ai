# Cerid AI

**Self-Hosted Personal AI Knowledge Companion**

A privacy-first, local-first workspace that unifies your code, finance, projects, and personal artifacts into a context-aware LLM interface with RAG-powered retrieval, intelligent agents, and built-in hallucination detection.

[![Release](https://img.shields.io/github/v/release/Cerid-AI/cerid-ai)](https://github.com/Cerid-AI/cerid-ai/releases/latest)
[![License](https://img.shields.io/badge/License-FSL--1.1--ALv2-blue)](LICENSE)
[![CI](https://github.com/Cerid-AI/cerid-ai/actions/workflows/ci.yml/badge.svg)](https://github.com/Cerid-AI/cerid-ai/actions/workflows/ci.yml)
[![npm @cerid-ai/sdk](https://img.shields.io/npm/v/@cerid-ai/sdk?label=npm%20%40cerid-ai%2Fsdk)](https://www.npmjs.com/package/@cerid-ai/sdk)
[![PyPI cerid-sdk](https://img.shields.io/pypi/v/cerid-sdk?label=pypi%20cerid-sdk)](https://pypi.org/project/cerid-sdk/)

[Docs](docs/) · [Website](https://cerid.ai) · [**Product demo**](https://cerid.ai/#demo) · [Pricing](https://cerid.ai/pricing) · [Releases](https://github.com/Cerid-AI/cerid-ai/releases)

**[90s product tour](https://cerid.ai/#demo)** · [30s sizzle](https://cerid.ai/cerid-30s-demo.mp4) — live UI: claim verification, Constellation explore, wiki, TrustScore.

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

Prefer an app? Signed builds for macOS and Windows: [desktop release v1.0.2](https://github.com/Cerid-AI/cerid-ai/releases/tag/v1.0.2-desktop).

---

## Why Cerid?

Most self-hosted AI tools are either basic RAG wrappers or bloated agent frameworks. Cerid is different:

| Feature                        | Cerid AI                          | AnythingLLM          | Mem0              | PrivateGPT       |
|--------------------------------|-----------------------------------|----------------------|-------------------|------------------|
| **Hallucination detection**    | ✅ Built-in claim verification + NLI | ❌                    | ❌                 | ❌                |
| **Memory extraction**          | ✅ Auto-extract facts/decisions from chat | Basic              | Core feature     | ❌                |
| **Specialist agents**          | ✅ Query, Triage, Rectify, Audit, Hallucination, Memory, etc. | Limited           | None             | None             |
| **Tiered local inference**     | ✅ Ollama + GPU sidecar + auto-fallback | Basic             | None             | Basic            |
| **Graph + vector + BM25**      | ✅ Full hybrid with Neo4j relationships | Vector only       | Vector only      | Vector only      |
| **Clean architecture (v1.0.2)** | ✅ Preservation harness of integration invariants + canonical models | Growing           | Growing          | Older            |
| **5-min Docker start**         | ✅ One-command                    | ✅                   | ✅                | ✅                |
| **Multi-domain KB**            | ✅ coding / finance / projects / personal | ✅                | Limited          | ✅                |

**Built for people who want their AI to be trustworthy, not just fast.**

---

## Key Capabilities

- **React GUI** at :3000 — streaming chat, Constellation map, wiki, sources, monitoring
- **Claim verification** — verified / partial / **refuted** with NLI contradiction gating and inline sources
- **Constellation + Atlas** — zoom, cluster, drill hubs; domain communities; Open-in-Atlas from wiki
- **Accumulating wiki** — entity pages that grow from your sources (not one-shot chat dumps)
- **Disclosed TrustScore** — straight mean of measured components; methodology open in-product
- **Specialist Agents** — Query, Triage, Hallucination Detection, Memory Extraction, Maintenance, Audit, and more
- **55 MCP Tools** (60 with the optional trading module) — Full control via MCP protocol (`pkb_*` namespace)
- **Memory System** — Automatically extracts facts, decisions, and preferences from conversations
- **Tiered Inference** — Auto-detects Ollama (GPU/CPU), FastEmbed sidecar, or Docker CPU fallback
- **Quenchforge AMD-GPU Routing** — Intel Mac + AMD discrete GPU operators get GPU acceleration for LLM chat, dense embeddings, cross-encoder reranking, AND ingest-time enrichment via per-workload env-driven routing (`INTERNAL_LLM_PROVIDER` / `EMBEDDINGS_PROVIDER` / `RERANK_PROVIDER`). See [`docs/AMD_GPU_MODEL_RECOMMENDATIONS.md`](docs/AMD_GPU_MODEL_RECOMMENDATIONS.md) for vetted GGUF picks by VRAM tier.
- **`/health.inference_routing`** — Five-key introspection of the active inference provider per workload (LLM / embed / rerank / sparse / NLI). Operators verify their env vars actually reached the container.
- **Hybrid Retrieval** — dense bi-encoder + BM25 + SPLADE-v3 learned-sparse, RRF-fused across all three retrievers
- **Adaptive Configuration Recommender** — Settings pane surfaces gated retrieval features (sparse, HyPE, parent-child, RRF) once your corpus crosses a feature-specific threshold; three-action dismissal matches GitHub's notification model
- **File Ingestion** — 30+ formats (PDF with tables, DOCX, code, Obsidian vaults, etc.)
- **Multi-Machine Sync** — Optional Dropbox JSONL sync (encrypted)
- **Full Observability** — Health checks, cost tracking, queue depth, swallowed error counters

Knowledge stores stay on your machine. Only the LLM calls you choose leave (or nothing, with full local Ollama).

---

## Core vs Pro

Everything above is **Cerid Core** — free, self-hosted, no account, no telemetry,
no seat limits, nothing that expires. It is a complete product on its own.

**Cerid Pro** ($15/mo · $144/yr) adds the surfaces that reach outside your own
disk: the connectors that pull your mail, calendars and meetings in, plus the
retrieval and analysis layers built on top of them.

| | Core | Pro |
|---|---|---|
| Knowledge surfaces (wiki / vector / graph / memory), 12 agents, 55 MCP tools | ✓ | ✓ |
| Local + web sources: folders, RSS, bookmarks, webhooks, browser extension | ✓ | ✓ |
| Local LLM pipeline (Ollama / quenchforge), hallucination detection, SDKs | ✓ | ✓ |
| Cloud connectors — Gmail, Outlook, Google + Microsoft Calendar | — | ✓ |
| Apple connectors — Mail, Notes, Photos, Reminders, Calendar, iMessage | — | ✓ |
| Meeting Capture — transcription with calendar-aware stitching | — | ✓ |
| Custom Smart RAG (per-source weighting), advanced analytics, daily digest | — | ✓ |
| AI inbox triage, metamorphic verification | — | ✓ |

Full flag-by-flag breakdown: [`docs/TIER_MATRIX.md`](docs/TIER_MATRIX.md).
**Cerid Vault** (enterprise: multi-user, SSO, audit logging, SLA) —
[vault@cerid.ai](mailto:vault@cerid.ai).

**Try Pro free for 14 days — no credit card.** Settings → Plan & Billing →
*Start 14-day free trial*. To buy, see [cerid.ai/pricing](https://cerid.ai/pricing)
and paste the key you receive into that same pane. Upgrading never needs a
reinstall, and if you stop paying, Core keeps working — nothing you built is
locked away.

---

## Architecture (high level)

```
User → React GUI (:3000)
         ↓
MCP Server (:8888) — FastAPI + specialist agents + hybrid retrieval
         ↓
ChromaDB (vectors) + Neo4j (graph) + Redis (cache + audit)
```

Core is cleanly separated from app layer (Phase C architecture). A preservation harness of integration invariants guards every capability at merge time (push to main + merge queue).

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
- 55 MCP tools (`pkb_*`) for programmatic access

### Install the SDKs

Typed client libraries for the stable `/sdk/v1/` API. SDKs version **independently**
of the product release (product **v1.0.2** · clients **0.1.1** on both registries):

| Client | Install | Registry |
|--------|---------|----------|
| Python | `pip install cerid-sdk` | [PyPI](https://pypi.org/project/cerid-sdk/) |
| TypeScript | `npm install @cerid-ai/sdk` | [npm](https://www.npmjs.com/package/@cerid-ai/sdk) |

See [docs/SDK_GUIDE.md](docs/SDK_GUIDE.md) for quickstarts and [docs/SDK_PUBLISHING.md](docs/SDK_PUBLISHING.md) for cutting a client release.

---

## What's new in v1.0

- **Verified answers by default** — claim extraction + NLI entailment gating verifies LLM responses against your knowledge base, with source attribution and streaming verification reports.
- **55 MCP tools** (60 with the optional trading module) plus a full MCP server, so Claude and other MCP-native clients drive Cerid directly.
- **Four retrieval surfaces, one query path** — vector, BM25/sparse hybrid, knowledge graph, and a self-refreshing wiki, routed per query intent.
- **Signed & notarized desktop apps** — macOS universal DMG and Windows installer on the [v1.0.2 desktop release](https://github.com/Cerid-AI/cerid-ai/releases/tag/v1.0.2-desktop).
- **Python + TypeScript SDKs live** — [`cerid-sdk` on PyPI](https://pypi.org/project/cerid-sdk/) and [`@cerid-ai/sdk` on npm](https://www.npmjs.com/package/@cerid-ai/sdk) against the stable `/sdk/v1/` contract.
- **FSL-1.1-ALv2 source-available licensing** — the core converts to Apache-2.0 on each version's second anniversary; SDKs and client integrations stay Apache-2.0.

Full history: [CHANGELOG.md](CHANGELOG.md) and the [GitHub releases](https://github.com/Cerid-AI/cerid-ai/releases) page.

---

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [API Reference](docs/API_REFERENCE.md)
- [Tiered Inference Architecture](docs/TIERED_INFERENCE_ARCHITECTURE.md)
- [Changelog](CHANGELOG.md)
- [Contributing](CONTRIBUTING.md)

---

## License

Cerid AI is **source-available, not open source**. The core is licensed under the
[Functional Source License 1.1 with an Apache-2.0 future license](LICENSE)
(`FSL-1.1-ALv2`): every version becomes Apache-2.0 on its second anniversary.

The repository is not uniformly licensed — which license applies depends on where a
file lives:

| Path | License |
|---|---|
| Repository root, `src/mcp/`, `src/web/` | FSL-1.1-ALv2 |
| `packages/sdk/python`, `packages/sdk/typescript` | Apache-2.0 |
| `packages/cli`, `packages/widget`, `packages/extension` | Apache-2.0 |
| `plugins/`, `src/mcp/plugins/` | BUSL-1.1 (converts to Apache-2.0 after three years) |

The SDKs and client integrations stay permissive on purpose: they are the surfaces
you build against, so depending on them should never pull FSL terms into your code.

**Releases published before the August 2026 license transition were, and remain,
Apache-2.0** — relicensing was version-forward only and cannot be applied retroactively.

**Star the repo** if this is useful — it helps more people discover private, trustworthy AI tools.

Built with ❤️ in Fairfax, VA.
