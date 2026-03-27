# Phase C: Core Orchestrator Extraction — Design Spec

> **Date:** 2026-03-26
> **Status:** Draft
> **Scope:** Restructure `src/mcp/` into `core/`, `app/`, and interface boundaries that enable a future `enterprise/` overlay — without breaking any existing functionality.

---

## 1. Objective

Extract the retrieval orchestration, agent framework, verification pipeline, memory engine, and model routing into a `core/` package with explicit contracts. The `app/` layer provides HTTP bindings, file parsing, and concrete store implementations. The boundary is enforced by import linting — core never imports from app.

This enables:
- A future commercial `enterprise/` overlay that wraps core contracts with ABAC, classification metadata, and immutable audit
- Independent testing of core logic without FastAPI/HTTP dependencies
- Clear IP documentation: core = Apache-2.0 foundation, enterprise = commercial overlay

**Non-goal:** We are NOT creating separate Git repos, separate PyPI packages, or pip-installable distributions. This is a monorepo restructure with enforced dependency direction.

---

## 2. Current State (Verified)

### 2.1 Packaging

- `src/mcp/` is a flat module directory (no top-level `__init__.py`)
- No `[build-system]` in `pyproject.toml` — tool config only (ruff, mypy, pytest)
- Docker copies entire `src/mcp/` to `/app`, runs `uvicorn main:app`
- Tests use `sys.path.insert(0, src/mcp/)` in `conftest.py`
- All imports are absolute from `src/mcp/` root: `from config import X`, `from utils.Y import Z`

### 2.2 Import Graph (Critical Dependencies)

**Isolated modules** (depend only on `config/`):
- `utils/smart_router.py` ← `config`
- `utils/reranker.py` ← `config`
- `utils/query_decomposer.py` ← `config.features`
- `utils/retrieval_gate.py` ← `config.features`
- `utils/context_assembler.py` ← `utils.text`
- `middleware/tenant_context.py` ← `config.features`

**Sparse utility dependencies** (config + a few utils):
- `agents/query_agent.py` ← `config`, `deps`, `utils.{cache, circuit_breaker, llm_parsing, text}`, `httpx`
- `agents/self_rag.py` ← `config`
- `agents/memory.py` ← `config`, `utils.{cache, circuit_breaker, internal_llm, llm_parsing, time}`
- `agents/rectify.py` ← `config`, `utils.{cache, circuit_breaker, llm_client, time}`
- `agents/audit.py` ← `config`, `utils.cache` (Redis-specific constants: `REDIS_CONV_METRICS_PREFIX`, `REDIS_VERIFICATION_METRICS_KEY`, `get_log`)
- `agents/maintenance.py` ← `config`, `agents.rectify` (cross-agent: `find_stale_artifacts`)
- `agents/trading_agent.py` ← `config.taxonomy` (takes Neo4j driver as parameter — already injectable)
- `agents/trading_scheduler_jobs.py` ← `config`, `httpx`, `deps` (HTTP client to external trading agent)

**Ingestion agents** (depend on parsers/chunker — stay in app):
- `agents/triage.py` ← `config`, `parsers.{PARSER_REGISTRY, parse_file}`, `utils.{chunker, metadata}`, `langgraph`

**Complex subsystem** (internal cross-imports):
- `agents/hallucination/` — 6 files with circular extraction↔verification↔streaming. Depends on `config`, `utils.{circuit_breaker, internal_llm, llm_client, llm_parsing, claim_cache, time}`

**DB-coupled agents** (depend on `db.neo4j.*`):
- `agents/curator.py` ← `config`, `db.neo4j.artifacts.{list_artifacts, update_artifact_summary}`, `utils.{circuit_breaker, llm_client, time}`

**Hub modules** (depend on everything — stay in app):
- `tools.py` ← `deps`, `db`, `routers.*`, `services.*`
- `main.py` ← all middleware, all routers, scheduler

**Root module** (no internal imports):
- `config/settings.py` — pure constants

### 2.3 Problem: `deps.py`

`deps.py` is the database connection singleton factory (ChromaDB, Neo4j, Redis). It imports `config` and `utils.embeddings`. Many core agents import `deps.get_chroma()` directly. This couples core logic to concrete database clients.

**Solution:** Core agents must NOT import `deps.py`. Instead, database clients are passed as parameters or injected via the contract interfaces.

### 2.4 Problem: Cross-Layer Dependencies (utils → middleware)

Three modules have utils→middleware imports that block extraction:

1. `utils/llm_client.py` imports `middleware.request_id.tracing_headers`
2. `utils/bifrost.py` imports `middleware.request_id.tracing_headers`
3. `utils/cache.py` imports `middleware.request_id.get_request_id`

**Solution:** Extract `tracing_headers()`, `get_request_id()`, and `get_client_id()` from `middleware/request_id.py` into a standalone `core/utils/tracing.py`. These are pure `contextvars` accessors with zero HTTP/FastAPI dependency — they just read values set by the middleware. Both `middleware/request_id.py` and the core utils import from the extracted module.

### 2.5 Problem: `agents/triage.py` Depends on Parsers

`triage.py` imports `parsers.{PARSER_REGISTRY, parse_file}` and `utils.{chunker, metadata}`. Parsers and chunking are file-format-specific application-layer concerns. Triage is an ingestion workflow router, not a core orchestration agent.

**Decision:** `triage.py` stays in `app/agents/`. It is not part of the core orchestrator.

### 2.6 Problem: `agents/curator.py` Depends on `db.neo4j`

`curator.py` imports `db.neo4j.artifacts.{list_artifacts, update_artifact_summary}`. The GraphStore contract must be expanded to support curator's needs, or curator stays in app.

**Decision:** Expand `GraphStore` ABC to include `list_artifacts()` and `update_artifact()`. Curator moves to core with contract injection.

---

## 3. Target Structure

```
src/mcp/
├── core/                          # Apache-2.0 — Orchestration logic
│   ├── __init__.py
│   ├── contracts/                 # Abstract interfaces (the extraction boundary)
│   │   ├── __init__.py
│   │   ├── stores.py              # VectorStore, GraphStore ABCs
│   │   ├── llm.py                 # LLMClient, InternalLLMClient ABCs
│   │   ├── cache.py               # CacheStore ABC
│   │   ├── embedding.py           # EmbeddingFunction ABC
│   │   └── audit.py               # AuditLog ABC (interface only — impl is app or enterprise)
│   ├── agents/                    # Agent implementations
│   │   ├── query_agent.py         # Retrieval orchestration
│   │   ├── self_rag.py            # Self-RAG validation loop
│   │   ├── memory.py              # Memory extraction + salience scoring
│   │   ├── curator.py             # Quality scoring (uses GraphStore contract)
│   │   ├── rectify.py             # Correction agent
│   │   ├── audit.py               # Verification metrics (uses CacheStore contract)
│   │   ├── maintenance.py         # Scheduled maintenance (cross-imports rectify)
│   │   ├── trading_agent.py       # Trading KB enrichment (takes driver as param)
│   │   └── hallucination/         # Entire subsystem (6 files, self-contained)
│   ├── retrieval/                 # RAG pipeline components
│   │   ├── reranker.py
│   │   ├── retrieval_gate.py
│   │   ├── query_decomposer.py
│   │   ├── context_assembler.py
│   │   ├── late_interaction.py
│   │   ├── semantic_cache.py
│   │   └── bm25.py
│   ├── routing/                   # Model selection + provider routing
│   │   ├── smart_router.py
│   │   └── model_providers.py
│   ├── protocol/                  # MCP + A2A protocol logic
│   │   ├── tool_registry.py       # Tool schema definitions (NOT dispatch — that's in app)
│   │   └── a2a_protocol.py        # A2A client + agent card schema
│   └── utils/                     # Core-only shared utilities
│       ├── circuit_breaker.py
│       ├── llm_parsing.py
│       ├── llm_client.py          # Unified LLM caller (OpenRouter direct)
│       ├── internal_llm.py        # Internal LLM (Ollama/OpenRouter for pipeline tasks)
│       ├── bifrost.py             # Shared Bifrost call utility
│       ├── cache.py               # Redis audit logging + event tracking
│       ├── text.py
│       ├── time.py
│       ├── tracing.py             # Extracted from middleware/request_id.py (contextvars accessors)
│       ├── claim_cache.py
│       ├── embeddings.py          # Embedding function wrapper
│       └── contextual.py
│
├── app/                           # Apache-2.0 — Application layer
│   ├── __init__.py
│   ├── main.py                    # FastAPI app assembly
│   ├── deps.py                    # Concrete DB singletons (ChromaDB, Neo4j, Redis)
│   ├── tools.py                   # MCP tool dispatch (calls core agents, passes stores)
│   ├── scheduler.py               # APScheduler integration
│   ├── agents/                    # App-layer agents (depend on parsers/services)
│   │   ├── triage.py              # LangGraph ingestion router (imports parsers, chunker)
│   │   └── trading_scheduler_jobs.py  # HTTP client to external trading agent
│   ├── routers/                   # All FastAPI routers (unchanged internally)
│   ├── middleware/                 # Auth, rate limiting, request tracing
│   ├── services/                  # Ingestion, folder scanner, multimodal
│   ├── parsers/                   # File format support
│   ├── db/                        # Neo4j CRUD, ChromaDB operations
│   ├── sync/                      # Multi-machine export/import
│   ├── models/                    # Pydantic request/response schemas
│   ├── stores/                    # Concrete implementations of core contracts
│   │   ├── chroma_store.py        # VectorStore implementation using ChromaDB
│   │   ├── neo4j_store.py         # GraphStore implementation using Neo4j
│   │   ├── redis_cache.py         # CacheStore implementation using Redis
│   │   ├── redis_audit.py         # AuditLog implementation (append to Redis)
│   │   └── llm_clients.py        # LLMClient impl (OpenRouter, Ollama, Bifrost)
│   └── eval/                      # Evaluation harness
│
├── plugins/                       # BSL-1.1 (existing, unchanged)
│
├── config/                        # Shared config (both core and app import this)
│   ├── __init__.py                # Re-exports from settings, features, taxonomy (unchanged)
│   ├── settings.py                # Chunking, URLs, scheduling, search tuning, CONSUMER_REGISTRY
│   ├── features.py                # Feature flags, toggles, plugin system
│   ├── taxonomy.py                # Domains, extensions, cross-domain affinity
│   └── providers.py               # LLM provider registry
│   # NOTE: model_providers.py moves to core/routing/model_providers.py (Phase 5)
│
├── tests/                         # All tests (unchanged location)
│
├── requirements.txt
├── requirements.lock
└── Dockerfile
```

### 3.1 Key Design Decisions

**`config/` stays at the root level** — shared by both core and app. It contains only pure constants (no imports from core or app). This avoids the circular dependency problem.

**`core/contracts/` defines thin ABCs** — 5-6 methods per interface, matching exactly what core agents actually call. No over-abstraction.

**Hub modules stay in app** — `tools.py`, `main.py`, `deps.py` depend on everything and belong in the application layer.

**`db/` stays in app** — Neo4j/ChromaDB CRUD operations are concrete implementations. Core agents access data through contract interfaces, not direct DB calls.

**Import direction enforced**: `core/` → `config/` only. `app/` → `core/`, `config/`. `config/` → nothing (pure constants). Never `core/` → `app/`. Never `config/` → `core/` or `app/`.

**`triage.py` stays in app** — it imports `parsers` and `utils.chunker`, which are file-format-specific application-layer concerns. Triage is an ingestion workflow router, not core orchestration.

**`trading_agent.py` moves to core** — it already takes the Neo4j driver as a parameter (injectable). `trading_scheduler_jobs.py` stays in app — it's an HTTP client to the external trading agent plus scheduler integration.

---

## 4. Contract Interfaces

### 4.1 VectorStore

```python
# core/contracts/stores.py
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

@dataclass
class SearchResult:
    """A single vector search result."""
    artifact_id: str
    chunk_id: str
    content: str
    metadata: dict[str, Any]
    distance: float

class VectorStore(ABC):
    """Abstract vector store — ChromaDB, Pinecone, Weaviate, etc."""

    @abstractmethod
    async def search(
        self,
        query_embedding: list[float],
        *,
        top_k: int = 10,
        where: dict[str, Any] | None = None,
        where_document: dict[str, Any] | None = None,
    ) -> list[SearchResult]: ...

    @abstractmethod
    async def get_by_ids(self, ids: list[str]) -> list[SearchResult]: ...

    @abstractmethod
    async def count(self) -> int: ...
```

### 4.2 GraphStore

```python
@dataclass
class ArtifactNode:
    """Core artifact metadata from the knowledge graph."""
    id: str
    filename: str
    domain: str
    sub_category: str
    tags: list[str]
    summary: str
    quality_score: float

class GraphStore(ABC):
    """Abstract knowledge graph — Neo4j, ArangoDB, etc."""

    @abstractmethod
    async def get_artifact(self, artifact_id: str) -> ArtifactNode | None: ...

    @abstractmethod
    async def get_related(
        self, artifact_ids: list[str], *, depth: int = 1, limit: int = 20,
    ) -> list[ArtifactNode]: ...

    @abstractmethod
    async def list_artifacts(
        self, *, domain: str | None = None, offset: int = 0, limit: int = 100,
    ) -> list[ArtifactNode]: ...

    @abstractmethod
    async def update_artifact(self, artifact_id: str, updates: dict[str, Any]) -> None: ...

    @abstractmethod
    async def list_domains(self) -> list[str]: ...
```

### 4.3 EmbeddingFunction

```python
# core/contracts/embedding.py

class EmbeddingFunction(ABC):
    """Abstract embedding — ONNX, OpenAI, Cohere, etc."""

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]: ...

    @abstractmethod
    def embed_query(self, text: str) -> list[float]: ...
```

### 4.4 LLMClient

```python
@dataclass
class LLMResponse:
    """Normalized LLM response."""
    content: str
    model: str
    usage: dict[str, int] | None = None

class LLMClient(ABC):
    """Abstract LLM caller — OpenRouter, Ollama, Bifrost, etc."""

    @abstractmethod
    async def call(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2000,
        breaker_name: str = "default",
    ) -> LLMResponse: ...
```

### 4.5 CacheStore

```python
class CacheStore(ABC):
    """Abstract cache — Redis, memcached, in-memory, etc."""

    @abstractmethod
    async def get(self, key: str) -> str | None: ...

    @abstractmethod
    async def set(self, key: str, value: str, *, ttl_seconds: int = 300) -> None: ...

    @abstractmethod
    async def delete(self, key: str) -> None: ...

    @abstractmethod
    async def append(self, key: str, value: str, *, max_len: int = 1000) -> None:
        """Append to an ordered list (Redis LPUSH + LTRIM pattern)."""
        ...

    @abstractmethod
    async def get_list(self, key: str, *, start: int = 0, end: int = -1) -> list[str]:
        """Read from an ordered list (Redis LRANGE pattern)."""
        ...
```

### 4.6 AuditLog

```python
@dataclass
class AuditEvent:
    """An auditable action."""
    action: str
    actor: str  # user_id or client_id
    resource: str  # artifact_id, query text, etc.
    detail: dict[str, Any] | None = None
    timestamp: str | None = None  # ISO 8601, auto-filled if None

class AuditLog(ABC):
    """Abstract audit log — Redis, Postgres, append-only file, etc."""

    @abstractmethod
    async def record(self, event: AuditEvent) -> None: ...

    @abstractmethod
    async def query(
        self, *, actor: str | None = None, action: str | None = None,
        since: str | None = None, limit: int = 100,
    ) -> list[AuditEvent]: ...
```

### 4.7 Design Notes

- Contracts use `async` methods — the codebase is already fully async
- `SearchResult`, `ArtifactNode`, `LLMResponse`, `AuditEvent` are plain dataclasses — no Pydantic dependency in core
- The `where` parameter on `VectorStore.search()` accepts generic filter dicts — extensible for any filtering needs
- `AuditLog` is a clean interface — app provides Redis-based impl, alternative implementations can provide immutable append-only behavior
- `EmbeddingFunction` is synchronous (ONNX inference is CPU-bound, not I/O-bound) — matches current usage
- `GraphStore` includes `list_artifacts` and `update_artifact` for curator agent support
- `CacheStore` includes `append`/`get_list` for audit event tracking (Redis LPUSH/LRANGE pattern)
- 6 contracts total: `VectorStore`, `GraphStore`, `EmbeddingFunction`, `LLMClient`, `CacheStore`, `AuditLog`

---

## 5. Migration Strategy: Incremental, Not Big-Bang

### Phase 1: Contracts + Import Linter (No File Moves)

1. Create `src/mcp/core/contracts/` with 5 ABC files (6 contracts — `stores.py` holds both `VectorStore` and `GraphStore`)
2. Create `src/mcp/core/__init__.py` (empty)
3. Add `import-linter` to `requirements-dev.txt`
4. Configure import-linter in `pyproject.toml`:
   ```toml
   [tool.importlinter]
   root_packages = ["core", "app", "config"]

   [[tool.importlinter.contracts]]
   name = "core must not import app"
   type = "forbidden"
   source_modules = ["core"]
   forbidden_modules = ["app", "routers", "services", "middleware", "parsers", "sync", "models", "deps", "tools", "main", "scheduler", "db", "eval", "stores", "agents"]

   [[tool.importlinter.contracts]]
   name = "config must not import core or app"
   type = "forbidden"
   source_modules = ["config"]
   forbidden_modules = ["core", "app", "routers", "services", "middleware", "parsers", "sync", "models", "deps", "tools", "main", "scheduler", "agents", "utils"]
   ```
5. Add import-linter check to CI (`lint` job)

**Audit gate:** `import-linter` passes. All 1566 tests pass. No functional changes.

### Phase 2: Extract Core Utilities (Low Risk)

Move isolated utilities that only depend on `config/`:

1. `utils/circuit_breaker.py` → `core/utils/circuit_breaker.py`
2. `utils/llm_parsing.py` → `core/utils/llm_parsing.py`
3. `utils/text.py` → `core/utils/text.py`
4. `utils/time.py` → `core/utils/time.py`
5. `utils/claim_cache.py` → `core/utils/claim_cache.py`
6. `utils/contextual.py` → `core/utils/contextual.py`

For each moved file:
- Add a **re-export bridge** at the old location: `from core.utils.circuit_breaker import *`
- Run `python -m pytest tests/ -x` after each move
- Run `import-linter` after each move

**Audit gate:** All tests pass. Import-linter passes. Re-export bridges ensure zero breakage for code that hasn't been updated yet.

### Phase 3: Fix Cross-Layer Dependencies (Critical Prerequisite)

Three modules have utils→middleware imports that must be broken before extraction:

1. Extract `tracing_headers()`, `get_request_id()`, `get_client_id()` from `middleware/request_id.py` → `core/utils/tracing.py` (these are pure `contextvars` accessors — no HTTP dependency)
2. Update `middleware/request_id.py` to import from `core.utils.tracing`
3. Move `utils/llm_client.py` → `core/utils/llm_client.py` (now imports `core.utils.tracing` instead of `middleware.request_id`)
4. Move `utils/internal_llm.py` → `core/utils/internal_llm.py`
5. Move `utils/bifrost.py` → `core/utils/bifrost.py` (same `tracing_headers` fix)
6. Move `utils/cache.py` → `core/utils/cache.py` (now imports `core.utils.tracing.get_request_id` instead of `middleware.request_id`)
7. Move `utils/embeddings.py` → `core/utils/embeddings.py`
8. Add re-export bridges at all old locations

**Audit gate:** Tests pass. Import-linter passes. `grep -r "from middleware" core/` returns 0 hits.

### Phase 4: Extract Retrieval Pipeline

Move RAG components (all isolated, depend only on config):

1. `utils/reranker.py` → `core/retrieval/reranker.py`
2. `utils/retrieval_gate.py` → `core/retrieval/retrieval_gate.py`
3. `utils/query_decomposer.py` → `core/retrieval/query_decomposer.py`
4. `utils/context_assembler.py` → `core/retrieval/context_assembler.py`
5. `utils/late_interaction.py` → `core/retrieval/late_interaction.py`
6. `utils/semantic_cache.py` → `core/retrieval/semantic_cache.py`
7. `utils/bm25.py` → `core/retrieval/bm25.py`

Re-export bridges at old locations. Tests after each.

**Audit gate:** Tests pass. Import-linter passes.

### Phase 5: Extract Model Routing

1. `utils/smart_router.py` → `core/routing/smart_router.py`
2. `config/model_providers.py` → `core/routing/model_providers.py`

Re-export bridges. Tests.

**Audit gate:** Tests pass. Import-linter passes.

### Phase 6: Extract Agents (Requires Contract Wiring)

This is the critical phase. Agents currently import `deps.py` for database access. We must refactor them to accept store instances as parameters.

**6a. Refactor `agents/query_agent.py`:**
- Remove `from deps import get_chroma`
- Add `vector_store: VectorStore` and `embedding_fn: EmbeddingFunction` parameters to `agent_query()`
- **Callers to update:** `routers/agents.py` (POST /agent/query), `tools.py` (pkb_query tool), `eval/harness.py` (evaluate function)

**6b. Refactor `agents/curator.py`:**
- Remove `from db.neo4j.artifacts import list_artifacts, update_artifact_summary`
- Add `graph_store: GraphStore` parameter to curation functions
- **Callers to update:** `routers/agents.py` (POST /agent/curate), `tools.py` (pkb_curate tool)

**6c. Refactor `agents/memory.py`:**
- Accept `graph_store: GraphStore`, `cache_store: CacheStore`, `llm: LLMClient` parameters
- **Callers to update:** `routers/memories.py`, `tools.py` (pkb_memory_recall tool)

**6d. Refactor `agents/audit.py`:**
- Replace Redis-specific constant imports from `utils.cache` with `CacheStore` contract methods
- **Callers to update:** `routers/observability.py`

**6e. Move refactored agents to core:**
- `agents/query_agent.py` → `core/agents/query_agent.py`
- `agents/self_rag.py` → `core/agents/self_rag.py`
- `agents/memory.py` → `core/agents/memory.py`
- `agents/hallucination/` → `core/agents/hallucination/` (entire directory as unit)
- `agents/curator.py` → `core/agents/curator.py`
- `agents/rectify.py` → `core/agents/rectify.py`
- `agents/audit.py` → `core/agents/audit.py`
- `agents/maintenance.py` → `core/agents/maintenance.py` (cross-imports rectify — move together)
- `agents/trading_agent.py` → `core/agents/trading_agent.py` (already takes driver param)

**Stays in app (not moved):**
- `agents/triage.py` → `app/agents/triage.py` (depends on parsers, chunker, metadata)
- `agents/trading_scheduler_jobs.py` → `app/agents/trading_scheduler_jobs.py` (HTTP client + scheduler)

Re-export bridges at all old `agents/` locations.

**Audit gate:** Tests pass. Import-linter passes. `grep -r "from deps\|from db\.\|from routers\|from middleware\|from services\|from parsers" core/agents/` returns 0 hits.

### Phase 7: Create Concrete Store Implementations

1. Create `app/stores/chroma_store.py` implementing `VectorStore` — wraps existing ChromaDB operations from `deps.py`
2. Create `app/stores/neo4j_store.py` implementing `GraphStore` — wraps existing `db/neo4j/*.py` CRUD
3. Create `app/stores/redis_cache.py` implementing `CacheStore` — wraps existing Redis operations
4. Create `app/stores/redis_audit.py` implementing `AuditLog` — wraps existing audit logging
5. Create `app/stores/llm_clients.py` implementing `LLMClient` — wraps existing `utils/llm_client.py` and `utils/internal_llm.py`

**Audit gate:** Tests pass. Store implementations verified against contracts.

### Phase 8: Move Application Layer Into `app/`

Formal directory restructure:

1. Move `main.py`, `deps.py`, `tools.py`, `scheduler.py` → `app/`
2. Move `routers/`, `middleware/`, `services/`, `parsers/`, `db/`, `sync/`, `models/` → `app/`
3. Move `eval/` → `app/eval/`
4. Update Docker `CMD` from `main:app` to `app.main:app`
5. Update `conftest.py` sys.path to include `src/mcp/` (unchanged — already does this)
6. Update `pyproject.toml` mypy/pytest paths
7. Update CI workflow paths

**This is the highest-risk phase.** Every import in the codebase may need updating. Mitigation:
- Use `libcst` (Python AST-aware code modification) for import rewriting — NOT `sed`, which cannot parse Python and will match inside strings/comments
- Script: for each moved module, `libcst` finds all `from routers.X import Y` and rewrites to `from app.routers.X import Y`
- Re-export bridges at ALL old locations as safety net (these catch any imports the script misses)
- Run `ruff check`, `mypy`, AND full test suite after the script — all three must pass before committing
- This phase is a SINGLE commit covering all sub-steps — if any sub-step fails, revert the entire phase

**Audit gate:** All 1566 tests pass. CI lint, typecheck, and test jobs pass. Docker build succeeds. Import-linter passes (core has zero app imports).

### Phase 9: Licensing + Headers

1. Add `core/LICENSE` (Apache-2.0, already exists at root)
2. Verify all `core/` files have `# SPDX-License-Identifier: Apache-2.0` header (most already do)
3. Verify `plugins/LICENSE` (BSL-1.1, already exists)
4. Add multi-license table to `README.md`:

```markdown
## Licensing

| Directory | License | Description |
|-----------|---------|-------------|
| `core/` | [Apache-2.0](core/LICENSE) | Orchestration engine, agents, retrieval, verification |
| `app/` | [Apache-2.0](LICENSE) | Application layer, routers, parsers, GUI |
| `plugins/` | [BSL-1.1](plugins/LICENSE) | Pro-tier extensions (converts to Apache-2.0 after 3 years) |
```

No mention of enterprise in committed documentation — that's a future overlay.

**Audit gate:** `reuse lint` passes (SPDX compliance). License files present.

### Phase 10: Remove Re-Export Bridges (Optional, Future)

Once all imports are updated and stable, the re-export bridges at old locations can be removed. This is low-priority and can be done over time as a cleanup.

---

## 6. Defensive Audit Checkpoints

Each phase has an explicit gate. Here's the full audit protocol:

### Per-Phase Gates

| Phase | Gate | Command |
|-------|------|---------|
| 1 | Import-linter configured + passes | `python -m importlinter` |
| 2-5 | Tests pass + import-linter passes | `pytest tests/ -x && python -m importlinter` |
| 6 | Agents have zero `deps`/`routers`/`middleware` imports | `grep -r "from deps\|from routers\|from middleware" core/agents/` (expect 0 hits) |
| 7 | Store implementations satisfy contract interfaces | Unit tests with mock stores |
| 8 | Full CI pipeline green | `ruff check . && mypy . && pytest tests/ -v && docker build` |
| 9 | SPDX compliance | `reuse lint` (or manual header check) |

### Rollback Strategy

Every phase creates a git commit. If any phase fails its gate:
1. `git revert HEAD` to undo the phase
2. Diagnose the issue
3. Retry with the fix

No phase should be merged to main until its gate passes.

### CI Integration

Add to `.github/workflows/ci.yml` lint job:
```yaml
- name: Import boundary check
  run: pip install import-linter && python -m importlinter
```

This prevents future regressions — any code that violates the core→app boundary fails CI.

---

## 7. Docker Changes

### Dockerfile

Minimal changes — the `COPY . .` pattern already copies the entire `src/mcp/` tree:

```dockerfile
# Only change: CMD path
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8888"]
```

### docker-compose.yml

No mount changes needed — `src/mcp/` is still mounted as `/app`. The internal directory restructure is transparent to Docker.

### Future: Enterprise Overlay

When enterprise is built, it will be a separate Docker layer:
```dockerfile
# Enterprise Dockerfile (future, NOT in this spec)
FROM cerid-mcp:latest
COPY enterprise/ /app/enterprise/
# Enterprise middleware wraps core contracts
```

---

## 8. What This Does NOT Change

- **No new Python packages.** No `pyproject.toml` per package, no `pip install -e`.
- **No separate repos.** Monorepo stays.
- **No new dependencies.** Only `import-linter` added to dev deps.
- **No API changes.** All HTTP endpoints remain at same paths.
- **No Docker Compose changes** (except CMD path).
- **No test restructuring.** Tests stay in `tests/` and continue to work.
- **No enterprise code committed.** Only the contract interfaces exist — the enterprise implementations are a future session.

---

## 9. Effort Estimate

| Phase | Files Affected | Risk | Est. Commits |
|-------|---------------|------|-------------|
| 1. Contracts + linter | 7 new files + pyproject.toml | Low | 1 |
| 2. Core utilities | 6 moves + 6 bridges | Low | 1-2 |
| 3. Cross-layer fix | 8 moves + fixes + 8 bridges | Medium | 2 |
| 4. Retrieval pipeline | 7 moves + 7 bridges | Low | 1-2 |
| 5. Model routing | 2 moves + 2 bridges | Low | 1 |
| 6. Agent extraction | ~11 moves + 4 caller refactors | **High** | 4-5 |
| 7. Store implementations | 6 new files | Medium | 1-2 |
| 8. App directory move | ~30 moves + libcst rewrite | **High** | 2-3 |
| 9. Licensing | 2-3 files | Low | 1 |
| **Total** | ~90 files | | ~17 commits |

Phase 6 and 8 are the highest-risk phases. Phase 6 should enumerate all callers before starting (listed in the spec). Phase 8 should be done in a single focused session as one atomic commit.

---

## 10. Success Criteria

1. `import-linter` passes: `core/` has zero imports from `app/`, `routers/`, `middleware/`, `services/`, `parsers/`, `deps`, `tools`, `main`. `config/` has zero imports from `core/` or `app/`.
2. All 1566+ Python tests pass
3. All 545+ frontend tests pass (no backend API changes)
4. Docker build succeeds, health check passes
5. CI pipeline (9 jobs) all green
6. `core/contracts/` defines 6 ABCs (`VectorStore`, `GraphStore`, `EmbeddingFunction`, `LLMClient`, `CacheStore`, `AuditLog`) that match actual agent usage
7. `core/` can be understood and tested without reading `app/` internals
8. No enterprise-specific concepts in committed code or documentation — only generic contract interfaces that any implementation could satisfy
9. `triage.py` correctly placed in `app/agents/` (not core) — verified by its parser imports
10. All agent callers (routers, tools.py, eval harness) pass concrete store instances — no agent directly instantiates database connections
