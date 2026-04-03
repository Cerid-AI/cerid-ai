# Cerid AI

**Self-hosted, privacy-first AI Knowledge Companion**

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python 3.11](https://img.shields.io/badge/Python-3.11-3776AB.svg)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED.svg)](https://www.docker.com/)

---

## What is Cerid AI?

Cerid AI is a self-hosted knowledge base that combines RAG-powered retrieval, intelligent agents, and an extensible SDK into a unified workspace for managing multi-domain knowledge (code, finance, projects, personal artifacts). It is privacy-first by design: your data stays on your machine, stored in local vector and graph databases. LLM calls go to whichever provider you configure -- OpenRouter for cloud models, or Ollama for a fully local, air-gapped setup.

---

## Features

- **Hybrid BM25 + vector search** with cross-encoder reranking and knowledge graph traversal
- **10 intelligent agents** -- query, curator, triage, rectify, audit, maintenance, hallucination detection, memory extraction, self-RAG, and decomposer
- **21 MCP tools** for knowledge base management, search, ingestion, and automation
- **Extensible SDK** at `/sdk/v1/` for building custom clients and integrations
- **Plugin system** for community extensions (BSL-1.1 licensed plugins ship separately)
- **React GUI** with streaming chat, knowledge browser, monitoring dashboards, and audit views
- **Electron desktop app** for macOS and Windows with Docker lifecycle management
- **Ollama support** for fully local LLM inference -- no external API calls required
- **Streaming verification** with real-time claim extraction and hallucination detection against your knowledge base
- **Multi-domain knowledge organization** with hierarchical taxonomy, auto-categorization, and cross-domain connections
- **A2A (Agent-to-Agent) protocol** for remote agent discovery and task invocation
- **Dropbox sync** for cross-machine settings, conversations, and knowledge base snapshots
- **File ingestion pipeline** supporting 30+ formats (PDF, DOCX, XLSX, CSV, code files, config, markup)
- **Smart Auto-RAG** with query intent classification and automatic retrieval strategy selection
- **Graceful degradation** across 5 tiers (FULL, LITE, DIRECT, CACHED, OFFLINE) with per-service circuit breakers

---

## Quick Start

### Prerequisites

- Docker and Docker Compose v2+
- An [OpenRouter API key](https://openrouter.ai/keys) (or Ollama for local-only mode)

### Setup

```bash
git clone https://github.com/Cerid-AI/cerid-ai.git
cd cerid-ai
cp .env.example .env   # Edit with your OPENROUTER_API_KEY
./scripts/start-cerid.sh
```

Open **http://localhost:3000** in your browser.

### Verify

```bash
curl -s http://localhost:8888/health | python3 -m json.tool
```

---

## Architecture

Cerid AI runs as a set of Docker Compose services on a shared bridge network.

```
User --> React GUI (3000) --> Bifrost (8080) --> OpenRouter / Ollama
                          --> MCP Server (8888) --> ChromaDB + Neo4j + Redis
```

### Services

| Service | Port | Purpose |
|---------|------|---------|
| React GUI | 3000 | Primary web interface |
| MCP Server | 8888 | Knowledge base API (FastAPI) |
| Bifrost | 8080 | LLM gateway with intent-based routing |
| ChromaDB | 8001 | Vector store |
| Neo4j | 7474 | Graph database |
| Redis | 6379 | Cache, audit log, metrics |

The MCP server exposes Swagger/OpenAPI docs at **http://localhost:8888/docs**.

For the full API reference, see [docs/API_REFERENCE.md](docs/API_REFERENCE.md).

---

## SDK and Extensibility

### SDK Endpoints

The stable SDK lives at `/sdk/v1/` and provides a versioned contract for external consumers:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/sdk/v1/query` | POST | Query the knowledge base with RAG retrieval |
| `/sdk/v1/hallucination` | POST | Check a response for hallucinations against KB |
| `/sdk/v1/memory/extract` | POST | Extract facts and decisions from text |
| `/sdk/v1/health` | GET | Service health and readiness |

### Example

```bash
curl -X POST http://localhost:8888/sdk/v1/query \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $CERID_API_KEY" \
  -d '{"query": "How does the ingestion pipeline work?", "domains": ["coding"]}'
```

For the full SDK guide, see [docs/SDK_GUIDE.md](docs/SDK_GUIDE.md).

### Plugins

Cerid AI supports manifest-based plugins for extending functionality. Each plugin declares its capabilities and tier requirements in a `manifest.json`. See [plugins/README.md](plugins/README.md) for the plugin development guide.

---

## Configuration

All configuration lives in a single `.env` file at the repo root. Copy from the included template to get started:

```bash
cp .env.example .env
```

### Key Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENROUTER_API_KEY` | Yes (unless using Ollama) | API key for cloud LLM access |
| `CERID_TIER` | No | `community` (default), `pro`, or `enterprise` |
| `OLLAMA_ENABLED` | No | Set `true` for local-only LLM via Ollama |
| `CERID_API_KEY` | No | Enable API key auth (requests require `X-API-Key` header) |

For the full list of environment variables, see [.env.example](.env.example) and [docs/ENV_CONVENTIONS.md](docs/ENV_CONVENTIONS.md).

### Secrets Management

Secrets are encrypted at rest using `age`. Decrypt on a new machine with:

```bash
./scripts/env-unlock.sh    # Requires age key at ~/.config/cerid/age-key.txt
```

---

## Development

```bash
# Start with rebuild after code changes
./scripts/start-cerid.sh --build

# Validate environment (14 checks)
./scripts/validate-env.sh

# Run Python tests (in Docker)
docker run --rm -v "$(pwd)/src/mcp:/work" -w /work python:3.11-slim \
  bash -c "pip install -q -r requirements.txt -r requirements-dev.txt && python -m pytest tests/ -v"

# Run frontend tests
cd src/web && npx vitest run
```

See [DEVELOPMENT.md](DEVELOPMENT.md) for the full development guide and [CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidelines.

---

## Product Tiers

| Tier | License | Description |
|------|---------|-------------|
| **Cerid Core** | Apache-2.0 | This repository. Full knowledge base, all 10 agents, SDK, React GUI. |
| **Cerid Pro** | BSL-1.1 | Adds streaming verification, visual workflow builder, advanced plugins. |
| **Cerid Enterprise** | Commercial | Multi-tenant, SSO, dedicated support. |

Core is fully functional on its own. Pro and Enterprise are available separately for teams that need advanced features.

See [docs/TIER_MATRIX.md](docs/TIER_MATRIX.md) for the complete feature matrix.

---

## License

Licensed under the [Apache License 2.0](LICENSE).

Copyright 2024-2026 Cerid AI -- [cerid.ai](https://cerid.ai)
