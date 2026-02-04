# Cerid AI

**Self-Hosted Personal AI Knowledge Companion**

A privacy-first, local-first workspace that unifies multi-domain knowledge bases (code, finance, projects, personal artifacts) into a context-aware LLM interface with RAG-powered retrieval and intelligent agents.

[![Status](https://img.shields.io/badge/Status-Phase%200%20Complete-green)]()
[![License](https://img.shields.io/badge/License-Private-red)]()

---

## Overview

Cerid AI provides a unified interface for interacting with multiple LLM providers while maintaining complete control over your personal knowledge. Key capabilities:

- **Multi-Provider LLM Access** via Bifrost gateway (Claude, GPT, Grok, Gemini, DeepSeek, Llama)
- **RAG-Powered Context Injection** for token-efficient knowledge retrieval
- **Local Vector & Graph Storage** (ChromaDB, Neo4j, Redis, PostgreSQL/pgvector)
- **Privacy-First Architecture** - all data stays local, only LLM API calls go external
- **MCP Server Integration** for extensible tool capabilities

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         USER BROWSER                                 │
│                     http://localhost:3080                            │
└─────────────────────────────────┬───────────────────────────────────┘
                                  │
┌─────────────────────────────────▼───────────────────────────────────┐
│                        LibreChat (UI)                                │
│                 Container: LibreChat | Port: 3080                    │
│           Chat Interface + MCP Client + RAG Integration              │
└──────────┬──────────────────────────────────────┬───────────────────┘
           │                                      │
           │ LLM Requests                         │ MCP Tools / RAG
           ▼                                      ▼
┌──────────────────────────┐    ┌─────────────────────────────────────┐
│    Bifrost Gateway       │    │      AI Companion MCP Server        │
│  Container: bifrost      │    │   Container: ai-companion-mcp       │
│  Port: 8080              │    │   Port: 8888                        │
│  Routes to OpenRouter    │    │   REST API: /health /query /ingest  │
└──────────┬───────────────┘    └──────────┬──────────────────────────┘
           │                               │
           ▼                    ┌──────────┼──────────┐
┌──────────────────────────┐    │          │          │
│      OpenRouter API      │    ▼          ▼          ▼
│ (Claude, GPT, Gemini,    │  ChromaDB   Neo4j     Redis
│  Grok, DeepSeek, etc.)   │  :8001     :7474     :6380
└──────────────────────────┘

Supporting Services:
├── MongoDB (chat-mongodb)      - LibreChat data storage
├── Meilisearch (chat-meilisearch) - Search indexing  
├── VectorDB (vectordb)         - PostgreSQL + pgvector for RAG
└── RAG API (rag_api)           - Document processing
```

---

## Quick Start

### Prerequisites

- Docker & Docker Compose v2+
- OpenRouter API key ([get one here](https://openrouter.ai/keys))
- macOS, Linux, or Windows with WSL2

### 1. Clone & Configure

```bash
git clone git@github.com:sunrunnerfire/cerid-ai.git
cd cerid-ai

# Create environment file
cp stacks/librechat/.env.example stacks/librechat/.env
# Edit .env and add your OPENROUTER_API_KEY
```

### 2. Start Services

```bash
# Create shared network (first time only)
docker network create llm-network

# Start all stacks
./scripts/start-cerid.sh

# Or manually:
cd stacks/bifrost && docker compose up -d
cd ../librechat && docker compose up -d
cd ../../src/mcp && docker compose up -d
```

### 3. Access

| Service | URL | Purpose |
|---------|-----|---------|
| LibreChat | http://localhost:3080 | Main chat interface |
| MCP API | http://localhost:8888 | Knowledge base API |
| MCP Docs | http://localhost:8888/docs | Swagger API docs |
| Bifrost | http://localhost:8080 | LLM gateway dashboard |
| Neo4j Browser | http://localhost:7474 | Graph database UI |

---

## Directory Structure

```
cerid-ai/
├── README.md
├── .gitignore
├── librechat.yaml              # LibreChat configuration
├── artifacts -> ~/Dropbox/AI-Artifacts  # Symlink to artifacts
├── data -> src/mcp/data        # Symlink to persistent data
│
├── docs/                       # Documentation
├── scripts/                    # Utility scripts
│   └── start-cerid.sh          # Stack startup script
│
├── src/
│   └── mcp/                    # MCP Server
│       ├── main.py             # FastAPI server (REST endpoints)
│       ├── requirements.txt
│       ├── Dockerfile
│       ├── docker-compose.yml
│       └── data/               # Persistent storage
│           ├── chroma/         # Vector embeddings
│           ├── neo4j/          # Graph database
│           ├── redis/          # Cache
│           └── uploads/        # Uploaded files
│
└── stacks/
    ├── bifrost/                # LLM Gateway
    │   ├── docker-compose.yml
    │   └── data/config.json
    │
    ├── librechat/              # Chat UI + RAG
    │   ├── docker-compose.yml
    │   ├── docker-compose.override.yml
    │   ├── librechat.yaml
    │   └── .env
    │
    └── librechat-runtime/      # LibreChat source (for reference)
```

---

## Configuration

### Environment Variables

**stacks/librechat/.env:**
```bash
OPENROUTER_API_KEY=sk-or-v1-xxxxx    # Required
OPENAI_API_KEY=sk-or-v1-xxxxx        # Same key, used by RAG API
```

### LibreChat Models

Edit `stacks/librechat/librechat.yaml` to customize available models:

```yaml
endpoints:
  custom:
    - name: "Bifrost Gateway"
      baseURL: "http://bifrost:8080/v1"
      models:
        default:
          - "openrouter/anthropic/claude-sonnet-4"
          - "openrouter/openai/gpt-4o"
          # Add more models as needed
```

### MCP Server

Edit `librechat.yaml` MCP section:

```yaml
mcpServers:
  ai-companion:
    type: sse
    url: "http://ai-companion-mcp:8888/mcp/sse"
```

> **Note:** MCP SSE endpoint is planned for Phase 1. Currently, use REST API directly.

---

## MCP REST API

### Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Service health check |
| GET | `/collections` | List ChromaDB collections |
| GET | `/stats` | Database statistics |
| POST | `/query` | Query knowledge base |
| POST | `/ingest` | Ingest content |

### Query Knowledge Base

```bash
curl -X POST http://localhost:8888/query \
  -H "Content-Type: application/json" \
  -d '{
    "query": "search terms",
    "domain": "general",
    "top_k": 3
  }'
```

### Ingest Content

```bash
curl -X POST http://localhost:8888/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "content": "Your knowledge content here",
    "domain": "general"
  }'
```

---

## Service Ports

| Port | Service | Purpose |
|------|---------|---------|
| 3080 | LibreChat | Chat UI |
| 8080 | Bifrost | LLM Gateway |
| 8888 | MCP Server | Knowledge Base API |
| 8000 | RAG API | Document Processing |
| 8001 | ChromaDB | Vector Store |
| 7474 | Neo4j HTTP | Graph DB Browser |
| 7687 | Neo4j Bolt | Graph DB Protocol |
| 6380 | Redis | Cache |

---

## Operations

### Start All Services

```bash
./scripts/start-cerid.sh
```

### Stop All Services

```bash
cd ~/cerid-ai/stacks/librechat && docker compose down
cd ~/cerid-ai/src/mcp && docker compose down
cd ~/cerid-ai/stacks/bifrost && docker compose down
```

### View Logs

```bash
docker logs LibreChat --tail 50 -f
docker logs ai-companion-mcp --tail 50 -f
docker logs bifrost --tail 50 -f
```

### Check Health

```bash
docker ps --format "table {{.Names}}\t{{.Status}}"
curl -s http://localhost:8888/health | jq
```

### Backup

```bash
tar czf cerid-backup-$(date +%Y%m%d).tar.gz \
  ~/cerid-ai/src/mcp/data \
  ~/cerid-ai/stacks/librechat/.env
```

---

## Credentials

| Service | Username | Password |
|---------|----------|----------|
| Neo4j | neo4j | REDACTED_PASSWORD |
| VectorDB | myuser | mypassword |
| LibreChat | (your account) | (your password) |

---

## Troubleshooting

### Container shows "unhealthy"

Check if the service actually responds:
```bash
curl -s http://localhost:8888/health  # MCP
curl -s http://localhost:3080         # LibreChat
curl -s http://localhost:8000/health  # RAG API
```

If it responds, the healthcheck config may need adjustment.

### LibreChat can't reach MCP/Bifrost

Verify network connectivity:
```bash
docker exec LibreChat wget -q -O - http://ai-companion-mcp:8888/health
docker exec LibreChat wget -q -O - http://bifrost:8080/api/providers
```

### MCP SSE 404 Error

Expected - SSE endpoint not yet implemented. REST API works:
```bash
curl -s http://localhost:8888/query -X POST \
  -H "Content-Type: application/json" \
  -d '{"query": "test", "domain": "general"}'
```

### MongoDB Case Sensitivity

Ensure `MONGO_URI` uses exact case: `mongodb://chat-mongodb:27017/LibreChat`

---

## Development Roadmap

### ✅ Phase 0: Infrastructure (Complete)
- [x] Docker stacks deployed and healthy
- [x] LibreChat + Bifrost + MCP integration
- [x] Network connectivity verified
- [x] Git repo consolidated

### 🔄 Phase 1: Core Ingestion (Next)
- [ ] Implement Triage Agent (LangGraph)
- [ ] Add `/triage` endpoint
- [ ] Multi-format parsing (PDF, XLSX, code)
- [ ] MCP SSE endpoint for LibreChat tools

### 📋 Phase 2: RAG & Agents
- [ ] Query Agent with ChromaDB + Neo4j
- [ ] Rectification Agent for conflict resolution
- [ ] Audit Agent for hallucination detection
- [ ] Maintenance Agent for scheduled syncs

### 📋 Phase 3: GUI & Features
- [ ] Streamlit dashboard
- [ ] Obsidian integration
- [ ] Token/cost projections

### 📋 Phase 4: Production
- [ ] Redis caching optimization
- [ ] LUKS encryption
- [ ] Comprehensive documentation

---

## Host System

- **Hardware:** Mac Pro (16-Core Intel Xeon W, 160 GB RAM)
- **OS:** macOS
- **Docker:** 29.1.5 / Compose v5.0.1
- **Domains:** cerid.ai, cerid.net, getcerid.com

---

## License

Private repository. All rights reserved.

---

## Contact

**Owner:** Justin (@sunrunnerfire)
