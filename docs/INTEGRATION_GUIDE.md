# Cerid AI — Integration Guide for New Cerid-Series Agents

> **Last updated:** 2026-03-21
> **Applies to:** Phase 41+
> **Reference implementation:** cerid-trading-agent (`docs/DEPENDENCY_COUPLING.md`)

---

## 1. Overview

Cerid AI is a self-hosted personal AI knowledge companion that exposes a stable SDK API for external agent integrations. As a core library, it provides:

- **Knowledge Base (KB):** Multi-domain vector + graph store (ChromaDB + Neo4j) with BM25s hybrid search, semantic chunking, and cross-encoder reranking.
- **LLM Orchestration:** Bifrost gateway with intent classification, capability-based model routing, and streaming responses.
- **Verification:** Hallucination detection with 4 claim types, cross-model verification, and web search escalation.
- **Memory:** Conversation memory extraction, contextual recall, and per-domain tag vocabulary.
- **SDK API:** Stable `/sdk/v1/` endpoints for external consumers with per-client rate limiting, domain access control, and typed Pydantic response models.

New cerid-series agents (e.g., trading, compliance, research) integrate by registering as consumers, defining their KB domain, and calling SDK endpoints.

---

## 2. Prerequisites

Before integrating a new agent:

- [ ] Running cerid-ai stack (`./scripts/start-cerid.sh`) with healthy MCP server at `http://localhost:8888`
- [ ] Understanding of the agent's knowledge domain and sub-categories
- [ ] Familiarity with `config/settings.py`, `config/taxonomy.py`, and `routers/sdk.py`
- [ ] Read `docs/DEPENDENCY_COUPLING.md` for the existing trading-agent integration pattern

---

## 3. Integration Checklist (13 Steps)

### Step 1: Feature Flag

Add a feature flag to `config/settings.py`:

```python
CERID_{AGENT}_ENABLED: bool = os.getenv("CERID_{AGENT}_ENABLED", "false").lower() == "true"
```

All new agent features must be gated behind this flag. Default is `false` (backward-compatible, opt-in).

### Step 2: Domain

Add a dedicated domain to `config/taxonomy.py`:

- Add entry to `TAXONOMY` dict with sub-categories specific to the agent's knowledge area.
- Add corresponding entries to `TAG_VOCABULARY` for typeahead suggestions in the GUI.

Example:
```python
"agent_name": {
    "sub-category-1": ["tag1", "tag2"],
    "sub-category-2": ["tag3", "tag4"],
}
```

### Step 3: Consumer Registration

Add an entry to `CONSUMER_REGISTRY` in `config/settings.py`:

```python
CONSUMER_REGISTRY = {
    "agent-name": {
        "rate_limit": 80,           # req/min
        "allowed_domains": ["agent_domain", "related_domain"],
        "strict_domains": True,     # disables cross-domain affinity bleed
        "description": "Brief description of the agent",
    },
    # ... existing consumers
}
```

Also add the client to `CLIENT_RATE_LIMITS` for backward compatibility with per-client rate limiting.

### Step 4: Agent Module

Create `agents/{agent_name}.py` with the agent's core logic:

- Query orchestration functions that call KB retrieval scoped to `allowed_domains`
- Any LLM reasoning chains specific to the agent
- Follow existing patterns in `agents/query.py` or `agents/hallucination/`

### Step 5: Request Models

Create `models/{agent_name}.py` with Pydantic models for request validation:

```python
from pydantic import BaseModel

class AgentSignalRequest(BaseModel):
    query: str
    signal_data: dict | None = None
    domains: list[str] = ["agent_domain"]
    top_k: int = 5
```

### Step 6: Response Models

Add typed response models to `models/sdk.py`:

```python
class AgentSignalResponse(BaseModel):
    status: str
    results: list[dict]
    # ... agent-specific fields
```

All SDK endpoints must return typed Pydantic models (not raw dicts).

### Step 7: Agent Endpoints (Internal)

Add internal endpoints to `routers/agents.py`, gated by the feature flag:

```python
if settings.CERID_{AGENT}_ENABLED:
    @router.post("/agent/{agent_name}/action")
    async def agent_action(...):
        ...
```

These `/agent/` paths are internal and may change between versions.

### Step 8: SDK Endpoints (Stable)

Add stable endpoints to `routers/sdk.py` under the `/sdk/v1/{agent}/` prefix:

```python
@router.post("/sdk/v1/{agent_name}/action", response_model=AgentActionResponse)
async def sdk_agent_action(...):
    ...
```

The `/sdk/v1/` prefix is the stable contract for external consumers. All new consumers should use these endpoints exclusively.

### Step 9: MCP Tools

Add `pkb_{agent}_*` tools to `tools.py`:

- Each tool must have both `inputSchema` and `outputSchema` defined.
- Tool names follow the `pkb_{agent}_{action}` convention.
- Tools should be gated by the feature flag (only registered when enabled).

Example:
```python
{
    "name": "pkb_agent_action",
    "description": "Description of what this tool does",
    "inputSchema": { ... },
    "outputSchema": { ... },
}
```

### Step 10: Proxy Routes (Optional)

If the agent has its own HTTP API, create `routers/{agent_name}_proxy.py`:

- Proxy routes allow the React GUI to communicate with the external agent through the MCP server.
- Use `httpx.AsyncClient` with circuit breaker patterns (see existing Bifrost call utility).

### Step 11: Scheduler Jobs (Optional)

If the agent needs periodic background tasks:

- Add cron entries to `config/settings.py` (e.g., `AGENT_AUTORESEARCH_CRON`).
- Register job functions in `scheduler.py`.
- Gate registration behind the feature flag.

### Step 12: Tests

Create `tests/test_router_{agent_name}.py`:

- Test all SDK endpoints with mocked agent dependencies.
- Test feature flag gating (endpoints return 404 when disabled).
- Test domain access control (consumer can only access `allowed_domains`).
- Test rate limiting with the agent's `X-Client-ID`.

### Step 13: Documentation

Update the following files:

- `CLAUDE.md` — Update tool count, agent count, and add a conventions bullet for the new agent.
- `docs/DEPENDENCY_COUPLING.md` — Add coupled interfaces table, safe-to-change list, and breaking changes.
- `docs/ISSUES.md` — Add a phase section documenting what was resolved.

---

## 4. Domain Segregation Rules

Domain segregation ensures agents only access knowledge relevant to their function:

- **Dedicated domain:** Each agent gets its own domain in `config/taxonomy.py`. The trading agent has `trading`, a compliance agent might have `compliance`.
- **`allowed_domains`:** Restricts which KB domains a consumer can query. Defined in `CONSUMER_REGISTRY`.
- **`strict_domains: True`:** Disables cross-domain affinity bleed. Without this, the retrieval pipeline may return results from related domains via `DOMAIN_AFFINITY` weights.
- **Personal data protection:** The `personal` and `conversations` domains are never accessible to non-GUI consumers unless explicitly added to `allowed_domains`. This is a hard rule.
- **Shared data:** If two agents need to share a domain, add explicit affinity weight in `DOMAIN_AFFINITY` (e.g., `trading` to `finance` at 0.3 weight). Both agents must list the shared domain in `allowed_domains`.
- **Example:** The trading agent has `allowed_domains: ["trading", "finance"]` and `strict_domains: True`. It can query trading signals and financial data, but never personal notes, conversations, or code artifacts.

---

## 5. Client Authentication

### Headers

| Header | Purpose | Required |
|--------|---------|----------|
| `X-Client-ID` | Per-client rate limiting and consumer identification | Recommended |
| `X-API-Key` | API key authentication (only if `CERID_API_KEY` is set) | Conditional |

### Endpoint Selection

- **Use:** `/sdk/v1/` endpoints — stable contract, typed responses, versioned.
- **Avoid:** `/agent/` endpoints — internal, may change between versions without notice.

### Rate Limits

Each `X-Client-ID` gets an independent rate budget configured in `CONSUMER_REGISTRY` (or legacy `CLIENT_RATE_LIMITS`). Unrecognized clients default to 10 req/min.

### Circuit Breaker Pattern

External agents should implement a circuit breaker for cerid-ai calls (e.g., 5 failures, 60s open, half-open probe). When the circuit is open, the agent should degrade gracefully by operating without KB enrichment.

---

## 6. Example: Trading Agent Integration

The cerid-trading-agent is the reference implementation for this integration pattern.

### What was added to cerid-ai:

| Step | File(s) | What |
|------|---------|------|
| Feature flag | `config/settings.py` | `CERID_TRADING_ENABLED` |
| Domain | `config/taxonomy.py` | `trading` domain with 6 sub-categories |
| Consumer | `config/settings.py` | `CONSUMER_REGISTRY["trading-agent"]` with 80 req/min, `strict_domains: True` |
| Request models | `models/trading.py` | `TradingSignalRequest`, `HerdDetectRequest`, etc. |
| Response models | `models/sdk.py` | `TradingSignalResponse`, `HerdDetectResponse`, etc. |
| SDK endpoints | `routers/sdk.py` | 5 endpoints under `/sdk/v1/trading/` |
| MCP tools | `tools.py` | 5 tools: `pkb_trading_signal`, `pkb_herd_detect`, `pkb_kelly_size`, `pkb_cascade_confirm`, `pkb_longshot_surface` |
| Proxy routes | `routers/trading_proxy.py` | GUI proxy to trading agent at `TRADING_AGENT_URL` |
| Scheduler | `scheduler.py` | 3 cron jobs (autoresearch, Platt mirror, longshot surface) |
| Tests | `tests/test_router_sdk.py` | SDK endpoint tests with domain access control |
| Docs | `CLAUDE.md`, `DEPENDENCY_COUPLING.md` | Full contract documentation |

### How the trading agent calls cerid-ai:

```python
# In cerid-trading-agent: src/cerid_client.py
class CeridClient:
    def __init__(self):
        self.base_url = os.getenv("CERID_MCP_URL", "http://localhost:8888")
        self.client = httpx.AsyncClient(
            headers={"X-Client-ID": "trading-agent"},
            timeout=30.0,
        )

    async def trading_signal(self, query: str, signal_data: dict) -> dict:
        resp = await self.client.post(
            f"{self.base_url}/sdk/v1/trading/signal",
            json={"query": query, "signal_data": signal_data, "domains": ["trading"], "top_k": 5},
        )
        resp.raise_for_status()
        return resp.json()
```

### Graceful degradation:

The trading agent uses `AsyncCircuitBreaker` (5 failures, 60s open, half-open probe). When cerid-ai is unavailable, the agent skips KB enrichment and operates on its own context alone.

---

## See Also

- [`docs/DEPENDENCY_COUPLING.md`](DEPENDENCY_COUPLING.md) — Full contract for the trading agent integration
- [`docs/API_REFERENCE.md`](API_REFERENCE.md) — Complete API endpoint documentation
- [`CLAUDE.md`](../CLAUDE.md) — Project overview and conventions
