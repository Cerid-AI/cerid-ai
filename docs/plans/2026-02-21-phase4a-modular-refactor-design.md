# Phase 4A: Modular Refactor — Design Document

**Date:** 2026-02-21
**Status:** Approved
**Goal:** Split `src/mcp/main.py` (1,321 lines) into FastAPI `APIRouter` modules. Zero behavior change.

---

## Approach

Approach A: `deps.py` + service functions co-located in their routers.

- `deps.py` holds lazy-initialized DB singletons (same logic as current `main.py`)
- Core service functions move into the router file that owns their domain
- `agents.py` and `mcp_sse.py` import public service functions from sibling routers
- Clean one-way import graph, no circular dependencies

---

## File Structure

```
src/mcp/
├── main.py          (~100 lines) — app, lifespan, middleware, router includes
├── deps.py          (~60 lines)  — get_chroma/get_redis/get_neo4j lazy singletons
└── routers/
    ├── __init__.py
    ├── health.py    (~60 lines)  — /health, /collections, /stats
    ├── query.py     (~80 lines)  — /query
    ├── ingestion.py (~220 lines) — /ingest, /ingest_file, /ingest_log
    ├── artifacts.py (~80 lines)  — /artifacts, /recategorize
    ├── agents.py    (~200 lines) — /agent/*
    └── mcp_sse.py   (~360 lines) — /mcp/sse, /mcp/messages, MCP_TOOLS, execute_tool
```

---

## Component Responsibilities

### `deps.py`
Module-level lazy singletons, identical logic to current `main.py`:
- `get_chroma() -> chromadb.HttpClient`
- `get_redis() -> redis.Redis`
- `get_neo4j() -> neo4j.Driver`

Used directly in service functions and optionally via `Depends()` in endpoint signatures.

### `main.py`
- FastAPI app instantiation and CORS middleware
- `lifespan` context manager (replaces deprecated `@app.on_event`)
  - startup: `graph.init_schema(get_neo4j())`
  - shutdown: close Neo4j driver, clear `_sessions`
- Six `app.include_router(...)` calls
- `GET /` root endpoint

### `routers/health.py`
Public service functions (called by `mcp_sse.py`):
- `health_check() -> Dict`
- `list_collections() -> Dict`

Endpoints: `GET /health`, `GET /collections`, `GET /stats`

### `routers/query.py`
Public service functions:
- `query_knowledge(query, domain, top_k) -> Dict`

Endpoints: `POST /query`
Models: `QueryRequest`

### `routers/ingestion.py`
Private helpers: `_content_hash()`, `_check_duplicate()`
Public service functions (imported by `agents.py` and `mcp_sse.py`):
- `ingest_content(content, domain, metadata) -> Dict`
- `ingest_file(file_path, domain, tags, categorize_mode) -> Dict` (async)

Endpoints: `POST /ingest`, `POST /ingest_file`, `GET /ingest_log`
Models: `IngestRequest`, `IngestFileRequest`

### `routers/artifacts.py`
Public service functions:
- `recategorize(artifact_id, new_domain, tags) -> Dict`

Endpoints: `GET /artifacts`, `POST /recategorize`
Models: `RecategorizeRequest`

### `routers/agents.py`
No new service functions — thin endpoint wrappers over agent modules.
Imports `ingest_content` from `routers.ingestion` for triage DB writes.

Endpoints: `POST /agent/query`, `/agent/triage`, `/agent/triage/batch`, `/agent/rectify`, `/agent/audit`, `/agent/maintain`
Models: `AgentQueryRequest`, `TriageFileRequest`, `TriageBatchRequest`, `RectifyRequest`, `AuditRequest`, `MaintenanceRequest`

### `routers/mcp_sse.py`
- `MCP_TOOLS` list (all 12 tool definitions)
- `_sessions: Dict[str, asyncio.Queue]` module-level dict
- `execute_tool(name, args) -> Any` (async)
- `build_response(msg_id, method, params) -> dict` (async)

Imports service functions: `health_check`, `list_collections`, `query_knowledge`, `ingest_content`, `ingest_file`, `recategorize`

Endpoints: `HEAD /mcp/sse`, `GET /mcp/sse`, `POST /mcp/sse`, `POST /mcp/messages`

---

## Import Graph

```
main.py       → deps.py
              → routers/health, query, ingestion, artifacts, agents, mcp_sse

health.py     → deps.py
query.py      → deps.py
ingestion.py  → deps.py
artifacts.py  → deps.py
              → routers.ingestion (ingest_content — not needed currently, recategorize is self-contained)
agents.py     → deps.py
              → routers.ingestion (ingest_content)
mcp_sse.py    → deps.py
              → routers.health (health_check, list_collections)
              → routers.query (query_knowledge)
              → routers.ingestion (ingest_content, ingest_file)
              → routers.artifacts (recategorize)
```

No circular imports. All arrows point in one direction.

---

## Key Decisions

1. **`@app.on_event` → `lifespan`:** Deprecated decorator replaced with `contextlib.asynccontextmanager` lifespan. Same startup/shutdown behavior, no deprecation warnings. Within 4A scope.

2. **`_` prefix:** Dropped from service functions that must be imported cross-module (`ingest_content`, `ingest_file`, `query_knowledge`, `recategorize`, `health_check`, `list_collections`). Private helpers (`_content_hash`, `_check_duplicate`) stay `_` prefixed within `ingestion.py`.

3. **`_sessions` dict:** Stays module-level in `mcp_sse.py`. Both the SSE stream producer (GET) and consumer (POST /mcp/messages) are in the same file — no sharing needed.

4. **Zero behavior change:** All endpoint paths, request/response schemas, HTTP status codes, and error handling are preserved exactly. No new features, no logic changes.

---

## Verification Steps

Per `docs/PHASE4_PLAN.md`:

```bash
# All REST endpoints
curl http://localhost:8888/health
curl http://localhost:8888/collections
curl -X POST http://localhost:8888/query -H "Content-Type: application/json" -d '{"query":"test"}'
curl -X POST http://localhost:8888/ingest -H "Content-Type: application/json" -d '{"content":"test","domain":"general"}'
curl http://localhost:8888/artifacts
curl http://localhost:8888/ingest_log

# Agent endpoints
curl -X POST http://localhost:8888/agent/query -H "Content-Type: application/json" -d '{"query":"test"}'
curl -X POST http://localhost:8888/agent/rectify -H "Content-Type: application/json" -d '{}'
curl -X POST http://localhost:8888/agent/audit -H "Content-Type: application/json" -d '{}'
curl -X POST http://localhost:8888/agent/maintain -H "Content-Type: application/json" -d '{}'

# MCP SSE (verify LibreChat can connect and tools appear)
curl -N http://localhost:8888/mcp/sse
```

Docker container must build cleanly: `cd src/mcp && docker compose up -d --build`
