# Cerid AI - Project Plan & Technical Reference

**Document Version:** 2.0  
**Date:** February 4, 2026  
**Status:** Phase 0 Complete - All Systems Operational  
**Repository:** https://github.com/sunrunnerfire/cerid-ai (private)  
**Owner:** Justin (@sunrunnerfire)

---

## Document Purpose

This document serves as the single source of truth for the Cerid AI project. It provides:
1. Complete project context and vision
2. Current system state and configuration details
3. Technical specifications for all components
4. Development roadmap with implementation guidance
5. Troubleshooting reference

**For LLM Sessions:** This document contains all context needed to continue development. Start by reading the Executive Summary and Current State sections, then reference specific sections as needed.

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Current State](#2-current-state)
3. [Architecture](#3-architecture)
4. [Directory Structure](#4-directory-structure)
5. [Component Specifications](#5-component-specifications)
6. [Configuration Reference](#6-configuration-reference)
7. [Operations Guide](#7-operations-guide)
8. [Troubleshooting](#8-troubleshooting)
9. [Development Roadmap](#9-development-roadmap)
10. [Implementation Specifications](#10-implementation-specifications)
11. [Success Metrics](#11-success-metrics)

---

## 1. Executive Summary

### Project Vision

Cerid AI is a **self-hosted Personal AI Knowledge Companion** - a privacy-first, local-first workspace that unifies multi-domain knowledge bases (coding projects, taxes/finance, home projects, personal artifacts) into an efficient, context-aware LLM interface.

### Key Capabilities

- **Multi-Provider LLM Access** via Bifrost gateway (Claude, GPT, Grok, Gemini, DeepSeek)
- **RAG-Powered Context Injection** for token-efficient knowledge retrieval (2-4k tokens/query)
- **Local Vector & Graph Storage** (ChromaDB, Neo4j, Redis, PostgreSQL/pgvector)
- **Intelligent Agents** (planned) for triage, query, rectification, audit, and maintenance
- **Privacy-First Architecture** - all data local, only LLM API calls external

### Core Principles

| Principle | Implementation |
|-----------|----------------|
| Self-Hosted/Local-First | Docker containers, no cloud storage, LUKS encryption |
| Token Efficiency | RAG limits context to 2-4k tokens, diff-indexing, caching |
| Automation-Focused | LangGraph agents handle 85-90% of tasks |
| Privacy & Security | Encrypted local storage, isolated containers, no third-party data access |
| Low Maintenance | One-command deploys, cron-scheduled agents, 1-2 hours/week upkeep |
| Extensibility | Modular design, YAML configs, MCP tool framework |

### Domains

- **cerid.ai** (primary)
- cerid.net
- getcerid.com

---

## 2. Current State

### Phase Status: Phase 0 Complete ✅

| Component | Status | Notes |
|-----------|--------|-------|
| Docker Infrastructure | ✅ Healthy | All 10 containers running |
| LibreChat UI | ✅ Healthy | Port 3080, login working |
| Bifrost Gateway | ✅ Healthy | Port 8080, OpenRouter connected |
| MCP Server | ✅ Healthy | Port 8888, REST API functional |
| ChromaDB | ✅ Healthy | Port 8001, ready for embeddings |
| Neo4j | ✅ Running | Port 7474/7687, schema ready |
| Redis | ✅ Running | Port 6380, caching ready |
| RAG API | ✅ Healthy | Port 8000, document processing |
| MongoDB | ✅ Healthy | LibreChat data |
| Meilisearch | ✅ Healthy | Search indexing |
| Git Repository | ✅ Pushed | github.com/sunrunnerfire/cerid-ai |

### Verified Functionality

- [x] LibreChat UI accessible and login working
- [x] Model selection from Bifrost/OpenRouter working
- [x] Plain text LLM query successful
- [x] Document upload + RAG query successful
- [x] MCP REST API endpoints responding (/health, /query, /ingest)
- [x] Inter-container network connectivity verified
- [x] All healthchecks passing

### Known Gaps (Phase 1 Tasks)

| Gap | Impact | Resolution |
|-----|--------|------------|
| MCP SSE endpoint not implemented | LibreChat MCP tools don't connect | Implement `/mcp/sse` endpoint |
| Triage Agent not built | No automated ingestion | Build LangGraph agent |
| ChromaDB empty | No knowledge base content | Begin ingestion pipeline |
| Neo4j has schema only | No relationship data | Integrate with agents |

### Host System

| Spec | Value |
|------|-------|
| Hardware | Mac Pro (MacPro7,1) - Tower |
| CPU | 16-Core Intel Xeon W @ 3.2 GHz |
| RAM | 160 GB |
| OS | macOS |
| Docker | 29.1.5 |
| Docker Compose | v5.0.1 |
| Location | Fairfax, Virginia |

---

## 3. Architecture

### System Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                         USER BROWSER                                 │
│                     http://localhost:3080                            │
└─────────────────────────────┬───────────────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────────────┐
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
│  Routes to OpenRouter    │    │   REST: /health /query /ingest      │
└──────────┬───────────────┘    └──────────┬──────────────────────────┘
           │                               │
           ▼                    ┌──────────┼──────────┐
┌──────────────────────────┐    │          │          │
│      OpenRouter API      │    ▼          ▼          ▼
│ (Claude, GPT, Gemini,    │  ChromaDB   Neo4j     Redis
│  Grok, DeepSeek, etc.)   │  :8001     :7474     :6380
└──────────────────────────┘

Supporting Services:
├── MongoDB (chat-mongodb)         - LibreChat data storage (27017)
├── Meilisearch (chat-meilisearch) - Search indexing (7700)
├── VectorDB (vectordb)            - PostgreSQL + pgvector for RAG (5432)
└── RAG API (rag_api)              - Document processing (8000)
```

### Network Configuration

All containers communicate via `llm-network` Docker bridge network (172.18.0.0/16).

**Service Discovery:** Containers reference each other by container name:
- LibreChat → `http://bifrost:8080/v1`
- LibreChat → `http://ai-companion-mcp:8888/mcp/sse`
- LibreChat → `http://rag_api:8000`
- MCP → `http://ai-companion-chroma:8000`
- MCP → `bolt://ai-companion-neo4j:7687`
- MCP → `redis://ai-companion-redis:6379`

### Data Flow

```
CURRENT (Phase 0):
User → LibreChat → Bifrost → OpenRouter → LLM Response
User → LibreChat → RAG API → VectorDB → Context → LLM

PLANNED (Phase 1+):
INGESTION:
  Upload → MCP /triage → Triage Agent → Parse → Chunk → Embed → Store (Chroma/Neo4j)

QUERY:
  Prompt → MCP /query → Query Agent → Retrieve (Chroma + Neo4j) → Assemble Context → LLM

FEEDBACK:
  LLM Response → Audit Agent → Capture Artifacts → Re-ingest
```

---

## 4. Directory Structure

### Consolidated Repository

```
~/cerid-ai/                          # Main repository
├── README.md                        # Project documentation
├── .gitignore                       # Git ignore rules
├── librechat.yaml                   # LibreChat configuration (root copy)
├── artifacts -> ~/Dropbox/AI-Artifacts  # Symlink to artifacts storage
├── data -> src/mcp/data             # Symlink to persistent data
│
├── docs/                            # Documentation
│
├── scripts/
│   └── start-cerid.sh               # Stack startup script
│
├── src/
│   └── mcp/                         # MCP Server
│       ├── main.py                  # FastAPI server (216 lines)
│       ├── requirements.txt         # Python dependencies
│       ├── Dockerfile               # Container build (includes curl)
│       ├── docker-compose.yml       # MCP + ChromaDB + Neo4j + Redis
│       ├── docker-compose.override.yml
│       └── data/                    # Persistent storage
│           ├── chroma/              # Vector embeddings
│           ├── neo4j/               # Graph database
│           ├── neo4j-logs/          # Neo4j logs
│           ├── redis/               # Cache data
│           └── uploads/             # Uploaded files
│
└── stacks/
    ├── bifrost/                     # LLM Gateway
    │   ├── docker-compose.yml
    │   ├── docker-compose.override.yml
    │   └── data/
    │       └── config.json          # Bifrost configuration
    │
    ├── librechat/                   # Chat UI + RAG
    │   ├── docker-compose.yml
    │   ├── docker-compose.override.yml  # Healthcheck fixes
    │   ├── librechat.yaml           # LibreChat configuration
    │   ├── .env                     # Environment variables
    │   ├── uploads/                 # User uploads
    │   ├── images/                  # Generated images
    │   └── logs/                    # Application logs
    │
    └── librechat-runtime/           # LibreChat source (reference)
```

### Legacy Archive (Safe to Delete After 30 Days)

```
~/cerid-archive/
├── legacy-bifrost-stack/
├── legacy-librechat-stack/
└── legacy-ai-companion/
```

---

## 5. Component Specifications

### 5.1 LibreChat

**Purpose:** Web-based chat interface with RAG and MCP support

| Property | Value |
|----------|-------|
| Image | `ghcr.io/danny-avila/librechat-dev:latest` |
| Container | `LibreChat` |
| Port | 3080 |
| Config | `stacks/librechat/librechat.yaml` |
| Config Version | 1.2.1 (latest: 1.3.3) |

**Key Features:**
- Multi-model selection via Bifrost
- RAG document upload and query
- MCP client for tools (SSE connection)
- Conversation history in MongoDB

### 5.2 Bifrost Gateway

**Purpose:** LLM request router to OpenRouter

| Property | Value |
|----------|-------|
| Image | `maximhq/bifrost:latest` |
| Container | `bifrost` |
| Port | 8080 |
| Config | `stacks/bifrost/data/config.json` |

**Endpoints:**
- Dashboard: http://localhost:8080
- Health: http://localhost:8080/health
- Providers: http://localhost:8080/api/providers
- Chat: http://localhost:8080/v1/chat/completions

**Key Learnings:**
- Uses `config.json` (NOT YAML)
- Field is `logs_store` (plural)
- Delete `config.db*` to force config reload
- Base URL: `https://openrouter.ai/api` (Bifrost appends `/v1`)

### 5.3 MCP Server (AI Companion)

**Purpose:** Personal Knowledge Base with REST API

| Property | Value |
|----------|-------|
| Image | `mcp-mcp-server` (custom build) |
| Container | `ai-companion-mcp` |
| Port | 8888 |
| Source | `src/mcp/main.py` |

**Current REST Endpoints:**

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Service info |
| GET | `/health` | Health check with DB status |
| GET | `/collections` | List ChromaDB collections |
| GET | `/stats` | Database statistics |
| POST | `/query` | Query knowledge base |
| POST | `/ingest` | Ingest content |

**Planned Endpoints (Phase 1):**

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/mcp/sse` | SSE connection for LibreChat |
| POST | `/mcp/messages` | JSON-RPC for MCP tools |
| POST | `/triage` | Invoke triage agent |

**MCP Tools (Planned):**

| Tool | Description | Input |
|------|-------------|-------|
| `pkb_query` | Query knowledge base | `query`, `domain`, `top_k` |
| `pkb_ingest` | Ingest content | `content`, `domain` |
| `pkb_health` | Check service health | (none) |
| `pkb_collections` | List collections | (none) |

### 5.4 Storage Services

#### ChromaDB (Vector Store)

| Property | Value |
|----------|-------|
| Image | `chromadb/chroma:latest` |
| Container | `ai-companion-chroma` |
| Port | 8001 (internal 8000) |
| Data | `src/mcp/data/chroma/` |

**Environment:**
```
ANONYMIZED_TELEMETRY=false
ALLOW_RESET=true
IS_PERSISTENT=true
```

#### Neo4j (Graph Database)

| Property | Value |
|----------|-------|
| Image | `neo4j:latest` |
| Container | `ai-companion-neo4j` |
| Ports | 7474 (HTTP), 7687 (Bolt) |
| Data | `src/mcp/data/neo4j/` |
| Credentials | neo4j / REDACTED_PASSWORD |

**Environment:**
```
NEO4J_AUTH=neo4j/REDACTED_PASSWORD
NEO4J_PLUGINS=["apoc"]
NEO4J_dbms_memory_heap_max__size=1G
NEO4J_dbms_memory_pagecache_size=512M
```

#### Redis (Cache)

| Property | Value |
|----------|-------|
| Image | `redis:alpine` |
| Container | `ai-companion-redis` |
| Port | 6380 (internal 6379) |
| Data | `src/mcp/data/redis/` |

**Command:** `redis-server --appendonly yes --maxmemory 256mb --maxmemory-policy allkeys-lru`

#### PostgreSQL/pgvector (RAG Vector Store)

| Property | Value |
|----------|-------|
| Image | `pgvector/pgvector:0.8.0-pg15-trixie` |
| Container | `vectordb` |
| Port | 5432 (internal) |
| Credentials | myuser / mypassword |

#### MongoDB (LibreChat Data)

| Property | Value |
|----------|-------|
| Image | `mongo:8.0.17` |
| Container | `chat-mongodb` |
| Port | 27017 (internal) |
| Database | `LibreChat` (case-sensitive!) |

#### Meilisearch (Search Index)

| Property | Value |
|----------|-------|
| Image | `getmeili/meilisearch:v1.12.3` |
| Container | `chat-meilisearch` |
| Port | 7700 (internal) |

### 5.5 RAG API

| Property | Value |
|----------|-------|
| Image | `ghcr.io/danny-avila/librechat-rag-api-dev-lite:latest` |
| Container | `rag_api` |
| Port | 8000 |

**Note:** Uses OpenRouter API key for embeddings (text-embedding-3-small)

---

## 6. Configuration Reference

### 6.1 Environment Variables

**stacks/librechat/.env:**
```bash
OPENROUTER_API_KEY=sk-or-v1-xxxxx    # Required - OpenRouter API key
OPENAI_API_KEY=sk-or-v1-xxxxx        # Same key - used by RAG API for embeddings
```

### 6.2 LibreChat Configuration

**stacks/librechat/librechat.yaml:**
```yaml
version: "1.2.1"

mcpServers:
  ai-companion:
    type: sse
    url: "http://ai-companion-mcp:8888/mcp/sse"

endpoints:
  custom:
    - name: "Bifrost Gateway"
      apiKey: "not-needed"
      baseURL: "http://bifrost:8080/v1"
      models:
        default:
          # Claude (Coding & Complex Reasoning)
          - "openrouter/anthropic/claude-opus-4.5"
          - "openrouter/anthropic/claude-sonnet-4.5"
          - "openrouter/anthropic/claude-haiku-4.5"
          - "openrouter/anthropic/claude-sonnet-4"
          - "openrouter/anthropic/claude-opus-4"
          
          # Grok (Research & Current Events)
          - "openrouter/x-ai/grok-4-fast"
          - "openrouter/x-ai/grok-4.1-fast"
          
          # GPT (General Purpose)
          - "openrouter/openai/gpt-5.2"
          - "openrouter/openai/gpt-4o"
          - "openrouter/openai/gpt-4o-mini"
          
          # Gemini (Long Context)
          - "openrouter/google/gemini-3-pro"
          - "openrouter/google/gemini-3-flash"
          
          # DeepSeek (Best Value)
          - "openrouter/deepseek/deepseek-chat-v3.2"
          - "openrouter/deepseek/deepseek-r1"
          
          # Free Tier
          - "openrouter/x-ai/grok-4-fast:free"
          - "openrouter/google/gemini-2.0-flash-exp:free"
```

### 6.3 Bifrost Configuration

**stacks/bifrost/data/config.json:**
```json
{
  "providers": [
    {
      "name": "openrouter",
      "base_url": "https://openrouter.ai/api",
      "api_keys": [
        {
          "name": "openrouter-key",
          "value": {
            "from_env": "OPENROUTER_API_KEY"
          }
        }
      ],
      "custom_provider_config": {
        "base_provider_type": "openai"
      }
    }
  ],
  "client": {
    "enable_logging": true
  },
  "logs_store": {
    "type": "sqlite",
    "path": "/app/data/logs.db"
  }
}
```

### 6.4 Healthcheck Configurations

**stacks/librechat/docker-compose.override.yml:**
```yaml
services:
  api:
    healthcheck:
      test: ["CMD", "wget", "--no-verbose", "--tries=1", "--spider", "http://127.0.0.1:3080"]
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 90s
    depends_on:
      rag_api:
        condition: service_started
      mongodb:
        condition: service_started

  rag_api:
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"]
      interval: 30s
      timeout: 10s
      retries: 8
      start_period: 120s

  meilisearch:
    healthcheck:
      test: ["CMD-SHELL", "wget -q --spider http://127.0.0.1:7700/health || exit 1"]
      interval: 30s
      timeout: 10s
      retries: 5
```

**src/mcp/docker-compose.yml (healthcheck section):**
```yaml
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:8888/health"]
  interval: 30s
  timeout: 10s
  retries: 5
  start_period: 60s
```

### 6.5 MCP Dockerfile

**src/mcp/Dockerfile:**
```dockerfile
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8888", "--reload"]
```

### 6.6 MCP Requirements

**src/mcp/requirements.txt:**
```
fastapi
uvicorn[standard]
chromadb
neo4j
redis
requests
python-multipart
numpy<2
pydantic
mcp
```

---

## 7. Operations Guide

### 7.1 Start All Services

```bash
# Using start script (recommended)
~/cerid-ai/scripts/start-cerid.sh

# Or manually:
docker network create llm-network 2>/dev/null || true
cd ~/cerid-ai/stacks/bifrost && docker compose up -d
cd ~/cerid-ai/src/mcp && docker compose up -d
cd ~/cerid-ai/stacks/librechat && docker compose up -d
```

### 7.2 Stop All Services

```bash
cd ~/cerid-ai/stacks/librechat && docker compose down
cd ~/cerid-ai/src/mcp && docker compose down
cd ~/cerid-ai/stacks/bifrost && docker compose down
```

### 7.3 Check Status

```bash
# All containers
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

# Health checks
curl -s http://localhost:8888/health | jq
curl -s http://localhost:3080 | head -3
curl -s http://localhost:8080/health
```

### 7.4 View Logs

```bash
docker logs LibreChat --tail 50 -f
docker logs ai-companion-mcp --tail 50 -f
docker logs bifrost --tail 50 -f
```

### 7.5 Rebuild MCP After Code Changes

```bash
cd ~/cerid-ai/src/mcp
docker compose build
docker compose up -d --force-recreate
```

### 7.6 Backup

```bash
tar czf cerid-backup-$(date +%Y%m%d).tar.gz \
  ~/cerid-ai/src/mcp/data \
  ~/cerid-ai/stacks/librechat/.env \
  ~/cerid-ai/stacks/bifrost/data
```

### 7.7 Connectivity Tests

```bash
# LibreChat → MCP
docker exec LibreChat wget -q -O - http://ai-companion-mcp:8888/health

# LibreChat → Bifrost
docker exec LibreChat wget -q -O - http://bifrost:8080/api/providers | head -20

# MCP → ChromaDB
curl -s http://localhost:8888/collections

# Direct LLM test via Bifrost
curl -X POST http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $OPENROUTER_API_KEY" \
  -d '{"model": "openrouter/anthropic/claude-sonnet-4", "messages": [{"role": "user", "content": "Hello!"}]}'
```

### 7.8 API Usage

**Query Knowledge Base:**
```bash
curl -X POST http://localhost:8888/query \
  -H "Content-Type: application/json" \
  -d '{"query": "search terms", "domain": "general", "top_k": 3}'
```

**Ingest Content:**
```bash
curl -X POST http://localhost:8888/ingest \
  -H "Content-Type: application/json" \
  -d '{"content": "Your knowledge content here", "domain": "general"}'
```

---

## 8. Troubleshooting

### 8.1 Container Shows "Unhealthy"

**Diagnosis:**
```bash
# Check if service actually responds
curl -s http://localhost:8888/health  # MCP
curl -s http://localhost:3080         # LibreChat
curl -s http://localhost:8000/health  # RAG API

# Check healthcheck config
docker inspect <container> | grep -A12 "Healthcheck"
```

**Common Causes:**
- Missing `start_period` in healthcheck
- Using `localhost` instead of `127.0.0.1` (IPv6 issue)
- Missing curl/wget in container
- Wrong healthcheck endpoint

### 8.2 MCP SSE 404 Error

**Error:** `SSE error: Non-200 status code (404)`

**Cause:** `/mcp/sse` endpoint not implemented in current `main.py`

**Status:** Expected - this is a Phase 1 task. REST API works, MCP tools via LibreChat do not.

### 8.3 MCP 421 Misdirected Request

**Error:** `SSE error: Non-200 status code (421)`

**Cause:** MCP library's `TransportSecuritySettings` rejects Docker hostnames

**Solution:** Implement custom SSE handler that bypasses transport security (Phase 1)

### 8.4 MongoDB Case Sensitivity

**Error:** `db already exists with different case`

**Solution:** Ensure `MONGO_URI` uses exact case: `mongodb://chat-mongodb:27017/LibreChat`

### 8.5 VectorDB Database Missing

**Error:** `FATAL: database "mydatabase" does not exist`

**Solution:**
```bash
docker exec vectordb psql -U myuser -d postgres -c "CREATE DATABASE mydatabase;"
```

### 8.6 RAG API NumPy Error

**Error:** `AttributeError: np.float_ was removed in NumPy 2.0`

**Solution:** Pin in requirements.txt: `numpy<2`

### 8.7 Bifrost Config Not Loading

**Cause:** `config.db` caches configuration

**Solution:**
```bash
cd ~/cerid-ai/stacks/bifrost/data
rm -f config.db*
cd .. && docker compose restart bifrost
```

### 8.8 Healthcheck IPv6 Issue

**Error:** `wget: can't connect to remote host: Connection refused` (connecting to `[::1]`)

**Solution:** Use `127.0.0.1` instead of `localhost` in healthcheck commands

### 8.9 Missing curl/wget in Container

**Diagnosis:**
```bash
docker exec <container> which curl wget python
```

**Solutions:**
- Add to Dockerfile: `RUN apt-get update && apt-get install -y curl`
- Use Python: `["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://...')"]`

---

## 9. Development Roadmap

### Phase Overview

| Phase | Focus | Duration | Status |
|-------|-------|----------|--------|
| Phase 0 | Infrastructure & Baseline | 1 week | ✅ Complete |
| Phase 1 | Core Storage & Ingestion | 1 week | 🔄 Next |
| Phase 2 | RAG, Agents & Chat Integration | 1.5 weeks | Planned |
| Phase 3 | GUI & Advanced Features | 1 week | Planned |
| Phase 4 | Optimization & Documentation | 1 week | Planned |

### Phase 0: Infrastructure (Complete ✅)

**Deliverables:**
- [x] Docker stacks deployed and healthy
- [x] LibreChat + Bifrost + MCP integration
- [x] Network connectivity verified
- [x] Git repo consolidated and pushed
- [x] Healthchecks fixed
- [x] Documentation (README, this document)

### Phase 1: Core Storage & Ingestion (Next 🔄)

**Goals:** Automated file processing pipeline, MCP SSE for LibreChat

**Tasks:**

1. **Implement MCP SSE Endpoint**
   - Add `/mcp/sse` endpoint to `main.py`
   - Add `/mcp/messages` for JSON-RPC
   - Bypass MCP library transport security
   - Test LibreChat tool connection

2. **Implement Triage Agent**
   - Create `src/mcp/agents/triage.py`
   - LangGraph graph with nodes:
     - `parse_node`: PyPDF2 (PDF), Pandas (CSV/Excel), tree-sitter (code)
     - `categorize_node`: spaCy NER, domain classification
     - `chunk_embed_node`: Sentence-Transformers, 512 tokens max, 20% overlap
     - `store_node`: ChromaDB add, Neo4j create
   - Add `/triage` route to `main.py`

3. **Add Dependencies**
   ```
   # Add to requirements.txt
   langgraph
   spacy
   pandas
   pypdf2
   sentence-transformers
   tiktoken
   ```

4. **Test Ingestion**
   - Ingest 50 test artifacts (mixed formats)
   - Verify ChromaDB collections populated
   - Verify Neo4j relationships created

**Milestone:** 95% parse success; MCP tools visible in LibreChat

### Phase 2: RAG, Agents & Chat Integration

**Goals:** Context-aware chat with accuracy controls

**Tasks:**

1. **Query Agent** (`src/mcp/agents/query.py`)
   - Retrieve from ChromaDB (similarity search)
   - Traverse Neo4j relationships
   - Assemble context under 4k tokens
   - Return ranked suggestions

2. **Rectification Agent** (`src/mcp/agents/rectify.py`)
   - Neo4j queries for outdated data
   - spaCy conflict detection
   - Claude API fallback if confidence <0.7
   - Auto-update graph relationships

3. **Audit Agent** (`src/mcp/agents/audit.py`)
   - Post-response similarity check (hallucination detection)
   - RSS monitoring (feedparser) for external events
   - Flag generation with confidence scores

4. **Maintenance Agent** (`src/mcp/agents/maintenance.py`)
   - Cron-scheduled execution
   - Git repo sync and diff-indexing
   - Rectification triggers

5. **Enhance MCP Endpoints**
   - `/query` with modes: `rag`, `audit`
   - Tiktoken token projections
   - Suggestion ranking and previews

6. **LibreChat Integration**
   - YAML prompts with `{{mcp_context}}` placeholder
   - Parameters presets (factual: temp=0.1)
   - Memory integration via MCP

**Milestone:** Full chat flow with audits; <4k tokens avg; 90% RAG accuracy

### Phase 3: GUI & Advanced Features

**Goals:** Unified dashboard for oversight

**Tasks:**

1. **Streamlit Dashboard** (`src/gui/app.py`)
   - 5-column layout:
     - Ingestion Pane (file upload, triage preview)
     - Context Pane (suggestions, checkboxes)
     - Chat Pane (LibreChat iframe)
     - Token/Cost Pane (metrics, sliders)
     - Audit Pane (flags, resolve buttons)
   - Session state for selections
   - MCP API integration

2. **Obsidian Integration**
   - File watcher script
   - Webhook to `/triage` on changes
   - Vault sync configuration

3. **Feedback Loop**
   - LLM output capture
   - Artifact extraction (code blocks, tables)
   - Auto-ingest pipeline

**Milestone:** Intuitive GUI; 95% interaction success

### Phase 4: Optimization & Documentation

**Goals:** Production-ready system

**Tasks:**

1. **Performance Optimization**
   - Redis caching for embeddings
   - Query result caching
   - Benchmark with locust.io (target <200ms)

2. **Cron Automation**
   - Weekly maintenance agent runs
   - Daily audit checks
   - RSS feed updates

3. **Security Hardening**
   - LUKS encryption for data directory
   - Docker secrets for API keys
   - Network isolation review

4. **Documentation**
   - API documentation (FastAPI /docs)
   - User guide
   - Deployment guide

**Milestone:** Production deployment; 24h uptime validated; handoff complete

---

## 10. Implementation Specifications

### 10.1 Triage Agent (Phase 1)

```python
# src/mcp/agents/triage.py
from langgraph.graph import Graph, END
import spacy
import pypdf2
import pandas as pd

def parse_node(state):
    """Parse uploaded file based on type"""
    if state['type'] == 'pdf':
        with open(state['path'], 'rb') as f:
            reader = pypdf2.PdfReader(f)
            state['content'] = ''.join(page.extract_text() for page in reader.pages)
    elif state['type'] in ['csv', 'xlsx']:
        df = pd.read_csv(state['path']) if state['type'] == 'csv' else pd.read_excel(state['path'])
        state['content'] = df.to_string()
    # Add more parsers: tree-sitter for code, etc.
    return state

def categorize_node(state):
    """Extract entities and classify domain"""
    nlp = spacy.load('en_core_web_sm')
    doc = nlp(state['content'][:10000])  # Limit for performance
    state['entities'] = [(ent.text, ent.label_) for ent in doc.ents]
    # Rule-based domain classification
    keywords = {
        'finance': ['tax', 'income', 'expense', 'investment', 'account'],
        'coding': ['function', 'class', 'import', 'def', 'return'],
        'projects': ['project', 'timeline', 'milestone', 'budget', 'contractor']
    }
    state['domain'] = 'general'
    for domain, words in keywords.items():
        if any(w in state['content'].lower() for w in words):
            state['domain'] = domain
            break
    return state

def chunk_embed_node(state):
    """Chunk content and generate embeddings"""
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer('all-MiniLM-L6-v2')
    
    # Chunk with overlap
    chunk_size = 512
    overlap = 102  # 20%
    text = state['content']
    chunks = []
    for i in range(0, len(text), chunk_size - overlap):
        chunks.append(text[i:i + chunk_size])
    
    state['chunks'] = chunks
    state['embeddings'] = model.encode(chunks).tolist()
    return state

def store_node(state):
    """Store in ChromaDB and Neo4j"""
    # ChromaDB storage
    import chromadb
    client = chromadb.HttpClient(host='ai-companion-chroma', port=8000)
    collection = client.get_or_create_collection(state['domain'])
    
    for i, (chunk, embedding) in enumerate(zip(state['chunks'], state['embeddings'])):
        collection.add(
            ids=[f"{state['id']}_{i}"],
            documents=[chunk],
            embeddings=[embedding],
            metadatas=[{'source': state['path'], 'domain': state['domain']}]
        )
    
    # Neo4j storage (artifact node)
    from neo4j import GraphDatabase
    driver = GraphDatabase.driver('bolt://ai-companion-neo4j:7687', auth=('neo4j', 'REDACTED_PASSWORD'))
    with driver.session() as session:
        session.run("""
            CREATE (a:Artifact {id: $id, domain: $domain, path: $path, chunk_count: $chunks})
        """, id=state['id'], domain=state['domain'], path=state['path'], chunks=len(state['chunks']))
    
    return state

# Build graph
triage_graph = Graph()
triage_graph.add_node("parse", parse_node)
triage_graph.add_node("categorize", categorize_node)
triage_graph.add_node("chunk_embed", chunk_embed_node)
triage_graph.add_node("store", store_node)
triage_graph.add_edge("parse", "categorize")
triage_graph.add_edge("categorize", "chunk_embed")
triage_graph.add_edge("chunk_embed", "store")
triage_graph.add_edge("store", END)
triage_graph.set_entry_point("parse")

triage_agent = triage_graph.compile()
```

### 10.2 MCP SSE Endpoint (Phase 1)

```python
# Add to src/mcp/main.py
from fastapi import Request
from fastapi.responses import StreamingResponse
import json
import asyncio

# MCP Tools definition
MCP_TOOLS = {
    "pkb_query": {
        "description": "Query the personal knowledge base",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "domain": {"type": "string", "default": "general"},
                "top_k": {"type": "integer", "default": 3}
            },
            "required": ["query"]
        }
    },
    "pkb_ingest": {
        "description": "Ingest content into knowledge base",
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "domain": {"type": "string", "default": "general"}
            },
            "required": ["content"]
        }
    },
    "pkb_health": {
        "description": "Check knowledge base health",
        "inputSchema": {"type": "object", "properties": {}}
    },
    "pkb_collections": {
        "description": "List available collections",
        "inputSchema": {"type": "object", "properties": {}}
    }
}

@app.get("/mcp/sse")
async def mcp_sse(request: Request):
    """SSE endpoint for MCP protocol"""
    async def event_stream():
        # Send initial connection event
        yield f"data: {json.dumps({'type': 'connection', 'status': 'connected'})}\n\n"
        
        # Keep connection alive
        while True:
            if await request.is_disconnected():
                break
            yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
            await asyncio.sleep(30)
    
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )

@app.post("/mcp/messages")
async def mcp_messages(request: Request):
    """JSON-RPC endpoint for MCP messages"""
    body = await request.json()
    method = body.get("method")
    params = body.get("params", {})
    msg_id = body.get("id")
    
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"tools": [{"name": k, **v} for k, v in MCP_TOOLS.items()]}
        }
    
    elif method == "tools/call":
        tool_name = params.get("name")
        tool_args = params.get("arguments", {})
        
        if tool_name == "pkb_query":
            result = _query_knowledge(**tool_args)
        elif tool_name == "pkb_ingest":
            result = _ingest_content(**tool_args)
        elif tool_name == "pkb_health":
            result = health_check()
        elif tool_name == "pkb_collections":
            result = list_collections()
        else:
            return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": "Unknown tool"}}
        
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}
    
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": "Method not found"}}
```

### 10.3 Neo4j Schema

```cypher
// Run on Neo4j initialization
CREATE CONSTRAINT artifact_id IF NOT EXISTS FOR (a:Artifact) REQUIRE a.id IS UNIQUE;
CREATE CONSTRAINT domain_name IF NOT EXISTS FOR (d:Domain) REQUIRE d.name IS UNIQUE;

// Domain nodes
CREATE (d:Domain {name: 'finance'});
CREATE (d:Domain {name: 'coding'});
CREATE (d:Domain {name: 'projects'});
CREATE (d:Domain {name: 'personal'});
CREATE (d:Domain {name: 'general'});

// Relationship types:
// (a1:Artifact)-[:SUPERSEDES]->(a2:Artifact)  // Version history
// (a:Artifact)-[:BELONGS_TO]->(d:Domain)      // Domain membership
// (a:Artifact)-[:REFERENCES]->(a2:Artifact)   // Cross-references
// (a:Artifact)-[:DERIVED_FROM]->(a2:Artifact) // LLM outputs
```

---

## 11. Success Metrics

### Functional Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| RAG Retrieval Accuracy | 90% | Cosine similarity >0.8 |
| Parse Success Rate | 95% | Successful triage / total uploads |
| End-to-End Latency | <5s | Ingest → suggest → inject → audit |
| Query Latency | <2s | Query submission to response |

### Usability Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| Context Addition | <3 clicks | Steps to add suggested context |
| Opt-in Compliance | 100% | No unwanted context injections |
| GUI Interaction Success | 95% | Successful button/control actions |

### Performance Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| Monthly Tokens | <20k | Tracked via OpenRouter dashboard |
| Monthly Cost | <$20 | Token cost calculation |
| Memory Usage | <4GB | Docker stats monitoring |

### Quality Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| Audit Flag Accuracy | 80% | Manual verification of flags |
| Auto-Rectification Success | 90% | Conflicts resolved without manual intervention |
| Data Leaks | Zero | Local audit log verification |

---

## Appendix A: Port Reference

| Port | Service | Container | Purpose |
|------|---------|-----------|---------|
| 3080 | LibreChat | LibreChat | Chat UI |
| 8080 | Bifrost | bifrost | LLM Gateway |
| 8888 | MCP Server | ai-companion-mcp | Knowledge Base API |
| 8000 | RAG API | rag_api | Document Processing |
| 8001 | ChromaDB | ai-companion-chroma | Vector Store |
| 7474 | Neo4j HTTP | ai-companion-neo4j | Graph DB Browser |
| 7687 | Neo4j Bolt | ai-companion-neo4j | Graph DB Protocol |
| 6380 | Redis | ai-companion-redis | Cache |
| 5432 | PostgreSQL | vectordb | RAG Vector Store |
| 27017 | MongoDB | chat-mongodb | LibreChat Data |
| 7700 | Meilisearch | chat-meilisearch | Search Index |

## Appendix B: Credentials

| Service | Username | Password |
|---------|----------|----------|
| Neo4j | neo4j | REDACTED_PASSWORD |
| VectorDB | myuser | mypassword |
| LibreChat | (user-created) | (user-created) |

## Appendix C: Key File Paths

| Purpose | Path |
|---------|------|
| Repository Root | `~/cerid-ai/` |
| MCP Server Code | `~/cerid-ai/src/mcp/main.py` |
| MCP Docker Compose | `~/cerid-ai/src/mcp/docker-compose.yml` |
| LibreChat Config | `~/cerid-ai/stacks/librechat/librechat.yaml` |
| LibreChat Env | `~/cerid-ai/stacks/librechat/.env` |
| Bifrost Config | `~/cerid-ai/stacks/bifrost/data/config.json` |
| Start Script | `~/cerid-ai/scripts/start-cerid.sh` |
| Persistent Data | `~/cerid-ai/src/mcp/data/` |
| Legacy Archive | `~/cerid-archive/` |

---

*Document generated: February 4, 2026*
*For questions or updates, refer to the GitHub repository issues.*
