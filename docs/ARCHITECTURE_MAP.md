# Cerid AI — Architecture Map

> One-page reference for module responsibilities and dependencies.
> AI agents: read this after compaction to reconstruct the architecture.
> Last updated: 2026-03-28

## Core Pipeline

```
User Query → routers/query.py → agents/query_agent.py (orchestrator)
  ├── agents/decomposer.py        (sub-query generation)
  ├── agents/assembler.py         (context assembly, reranking)
  ├── utils/retrieval_cache.py    (Redis L1 semantic cache)
  ├── utils/hyde.py               (HyDE fallback for low-confidence)
  ├── utils/chunker.py            (parent-child chunking)
  ├── deps.py → ChromaDB          (vector search)
  ├── db/neo4j/                   (graph relationships)
  └── utils/smart_router.py       → LLM Provider (response generation)
```

## Verification Pipeline

```
routers/agents.py → agents/hallucination/streaming.py (SSE orchestrator)
  ├── hallucination/extraction.py      (claim extraction)
  ├── hallucination/patterns.py        (13 pattern arrays)
  ├── hallucination/verification.py    (verify_claim, 4 fallback levels)
  ├── hallucination/verdict_parsing.py (verdict + inversion)
  ├── hallucination/confidence.py      (calibration + numeric alignment)
  ├── hallucination/persistence.py     (result storage)
  └── hallucination/metamorphic.py     (perturbation-based, Pro tier)
```

## Agents (10)

| Agent | File | Responsibility |
|-------|------|----------------|
| Query | `query_agent.py` | Orchestrates retrieval + generation pipeline |
| Decomposer | `decomposer.py` | Splits complex queries into sub-queries |
| Assembler | `assembler.py` | Assembles and reranks retrieved context |
| Curator | `curator.py` | KB quality control, deduplication |
| Triage | `triage.py` | Routes queries to appropriate handler |
| Rectify | `rectify.py` | Corrects/improves KB entries |
| Audit | `audit.py` | KB audit trail and compliance |
| Maintenance | `maintenance.py` | Scheduled KB cleanup tasks |
| Memory | `memory.py` | Conversation memory extraction/recall |
| Self-RAG | `self_rag.py` | Self-reflective retrieval-augmented generation |
| Trading | `trading_agent.py` | Trading signal integration (gated) |

## Routers (29)

| Router | Prefix | Responsibility |
|--------|--------|----------------|
| `query.py` | `/query` | Main query endpoint |
| `agents.py` | `/agents` | Verification, hallucination check |
| `chat.py` | `/chat` | Conversational interface |
| `sdk.py` | `/sdk/v1` | Stable versioned SDK for external consumers |
| `health.py` | `/health` | live/ready/status probes |
| `ingestion.py` | `/ingestion` | Document ingestion pipeline |
| `upload.py` | `/upload` | File upload handling |
| `kb_admin.py` | `/kb` | Knowledge base CRUD |
| `memories.py` | `/memories` | Memory store endpoints |
| `models.py` | `/models` | Model listing/config |
| `providers.py` | `/providers` | LLM provider management |
| `settings.py` | `/settings` | User/system settings |
| `auth.py` | `/auth` | Authentication |
| `taxonomy.py` | `/taxonomy` | Category/tag management |
| `artifacts.py` | `/artifacts` | Generated artifact storage |
| `sync.py` | `/sync` | Cross-device sync |
| `digest.py` | `/digest` | Digest/summary generation |
| `eval.py` | `/eval` | Evaluation harness |
| `scanner.py` | `/scanner` | Folder scanner |
| `setup.py` | `/setup` | Onboarding/setup wizard |
| `user_state.py` | `/user-state` | User preferences/state |
| `plugins.py` | `/plugins` | Plugin management |
| `workflows.py` | `/workflows` | Workflow engine |
| `automations.py` | `/automations` | Automation rules |
| `a2a.py` | `/a2a` | Agent-to-Agent protocol |
| `trading_proxy.py` | `/trading` | Trading agent proxy (gated) |
| `ollama_proxy.py` | `/ollama` | Local Ollama proxy |
| `mcp_sse.py` | `/mcp` | MCP SSE transport |
| `observability.py` | `/observability` | Metrics/tracing |

## Middleware (6)

| File | Order | Purpose |
|------|-------|---------|
| `request_id.py` | 1 | Attach unique request ID |
| `auth.py` | 2 | API key validation |
| `jwt_auth.py` | 3 | JWT token verification |
| `tenant_context.py` | 4 | Multi-tenant context injection |
| `rate_limit.py` | 5 | Per-client rate limiting |
| `metrics.py` | 6 | Request timing + Prometheus metrics |

## Config

| File | Purpose |
|------|---------|
| `settings.py` | Pydantic settings (env-driven) |
| `constants.py` | Magic numbers, limits, defaults |
| `features.py` | Feature flags + tier gating |
| `providers.py` | PIPELINE_PROVIDERS (8-stage Ollama routing) |
| `model_providers.py` | Model registry + capability scoring |
| `taxonomy.py` | Category/tag definitions |

## Cross-Cutting

| Module | Role |
|--------|------|
| `errors.py` | Exception hierarchy (FeatureGateError, etc.) |
| `utils/error_handler.py` | Centralized error formatting + logging |
| `utils/degradation.py` | Graceful degradation (circuit open fallbacks) |
| `utils/circuit_breaker.py` | Per-service circuit breakers |
| `utils/features.py` | Runtime feature checks (wraps config/features.py) |
| `utils/smart_router.py` | LLM routing (direct proxy, capability scoring, 3-way) |
| `utils/semantic_cache.py` | Arctic Embed M v1.5 (768-dim) vector cache |
| `deps.py` | FastAPI dependency injection (ChromaDB, Redis, Neo4j) |

## Data Stores

```
ChromaDB (8001)  ── vector embeddings, semantic search
Neo4j (7474)     ── entity relationships, graph queries
Redis (6379)     ── cache (L1 retrieval, semantic, sessions)
```

## Services

| File | Purpose |
|------|---------|
| `services/ingestion.py` | Document processing pipeline |
| `services/multimodal.py` | Vision/audio/OCR plugin dispatch |
| `services/folder_scanner.py` | Filesystem monitoring + auto-ingest |
