## Cross-Service Version Coupling

Constraints that span multiple files. When updating one side, check the other.

## ChromaDB Client / Server

| Component | File | Current |
|-----------|------|---------|
| Server image | `stacks/infrastructure/docker-compose.yml` | `chromadb/chroma:0.5.23` |
| Client library | `src/mcp/requirements.txt` | `chromadb>=0.5,<0.6` |

**Rule:** Client major.minor must match server. A server upgrade to 0.6.x requires updating the client range.

## spaCy Library / Model

| Component | File | Current |
|-----------|------|---------|
| Library | `src/mcp/requirements.txt` | `spacy>=3.5,<3.9` |
| Model | `src/mcp/Dockerfile` | `en_core_web_sm-3.7.1` |

**Rule:** The model version must be compatible with the installed spaCy major version. Check the [spaCy compatibility table](https://spacy.io/usage/models) before upgrading either.

## Node Version

| Component | File | Current |
|-----------|------|---------|
| Source of truth | `src/web/.nvmrc` | `22` |
| Docker build | `src/web/Dockerfile` | `node:22-alpine3.21` |
| CI | `.github/workflows/ci.yml` | `node-version-file: src/web/.nvmrc` |
| Engine constraint | `src/web/package.json` | `"node": ">=22"` |

**Rule:** `.nvmrc` is the single source of truth. CI reads it via `node-version-file`. Dockerfile and `package.json` must agree.

## Python Version

| Component | File | Current |
|-----------|------|---------|
| Docker build | `src/mcp/Dockerfile` | `python:3.11.14-slim` |
| CI | `.github/workflows/ci.yml` | `python-version: "3.11"` |

**Rule:** Both must use the same Python 3.11.x line. Lock files are generated against 3.11.

## pip-tools Version

| Component | File | Current |
|-----------|------|---------|
| CI lock-sync job | `.github/workflows/ci.yml` | `pip-tools==7.5.3` |
| Local generation | Developer machine | Must match CI version |

**Rule:** The `pip-compile` version used locally must match the CI `lock-sync` job. Version mismatches cause non-deterministic lock files and CI failures.

## Neo4j ↔ APOC Compatibility

| Neo4j Version | APOC Version | Notes |
|---------------|-------------|-------|
| 5.x | 5.x (matching major) | Auto-installed via NEO4J_PLUGINS=["apoc"] |
| 4.4.x | 4.4.x | Legacy, not supported |

APOC is installed automatically by the Neo4j Docker image when `NEO4J_PLUGINS=["apoc"]` is set.
The APOC version must match the Neo4j major version. When upgrading Neo4j, verify APOC compatibility
at https://neo4j.com/labs/apoc/current/.

## Ollama (Optional — Local LLM)

| Component | File | Current |
|-----------|------|---------|
| Feature gate | `src/mcp/config/settings.py` | `OLLAMA_ENABLED` (default: `false`) |
| Proxy router | `src/mcp/routers/ollama_proxy.py` | `/ollama/chat`, `/ollama/models`, `/ollama/pull` |
| Default URL | `.env` / `config/settings.py` | `OLLAMA_URL=http://localhost:11434` |

**Rule:** Ollama is entirely optional. When `OLLAMA_ENABLED=false` (default), all `/ollama/*` endpoints return 503. No Ollama dependency is required in the Docker image or Python requirements. The proxy uses httpx with circuit breaker to communicate with a separately-installed Ollama server.

## Bifrost LLM Gateway

| Component | File | Current |
|-----------|------|---------|
| Docker image | `stacks/bifrost/docker-compose.yml` | `maximhq/bifrost:latest` |
| Config | `stacks/bifrost/config.yaml` | Intent routing, model list |

**Rule:** Bifrost uses `latest` tag. When pinning to a specific version, update both the image tag and verify `config.yaml` compatibility with that release.

## Lock File Workflow

When editing `requirements.txt`:
```bash
cd src/mcp
pip-compile requirements.txt -o requirements.lock --generate-hashes --no-header --allow-unsafe
```

When editing `requirements-dev.txt`:
```bash
cd src/mcp
pip-compile requirements-dev.txt -o requirements-dev.lock --generate-hashes --no-header --allow-unsafe
```

The pre-commit hook and CI `lock-sync` job enforce that lock files stay in sync.

## Cross-Project: cerid-trading-agent

The [cerid-trading-agent](https://github.com/Cerid-AI/cerid-trading-agent) depends on cerid-ai's MCP API for knowledge base enrichment. Changes to cerid-ai must not break this integration.

### Coupled Interfaces

| Interface | cerid-ai Side | cerid-trading-agent Side | Notes |
|-----------|---------------|--------------------------|-------|
| KB query | `POST /sdk/v1/query` (`src/mcp/routers/sdk.py`) | `CeridClient.agent_query()` | Body: `{"query": "...", "top_k": 5, "domains": [...]}` |
| Hallucination check | `POST /sdk/v1/hallucination` (`src/mcp/routers/sdk.py`) | `CeridClient.hallucination_check()` | Body: `{"response_text": "...", "conversation_id": "..."}` |
| Memory extraction | `POST /sdk/v1/memory/extract` (`src/mcp/routers/sdk.py`) | `CeridClient.memory_extract()` | Body: `{"response_text": "...", "conversation_id": "..."}` |
| Health check | `GET /sdk/v1/health` (`src/mcp/routers/sdk.py`) | `CeridClient.health_check()` | Returns `{"status": "ok", "version": "...", "features": {...}}` |
| Trading signal enrich | `POST /sdk/v1/trading/signal` (`src/mcp/routers/sdk.py`) | `CeridClient.trading_signal()` | Body: `{"query": "...", "signal_data": {...}, "domains": [...], "top_k": 5}` |
| Herd detection | `POST /sdk/v1/trading/herd-detect` (`src/mcp/routers/sdk.py`) | `CeridClient.herd_detect()` | Body: `{"asset": "ETH", "sentiment_data": {...}}` |
| Kelly sizing | `POST /sdk/v1/trading/kelly-size` (`src/mcp/routers/sdk.py`) | `CeridClient.kelly_size()` | Body: `{"strategy": "...", "confidence": 0.75, "win_loss_ratio": 1.5}` |
| Cascade confirm | `POST /sdk/v1/trading/cascade-confirm` (`src/mcp/routers/sdk.py`) | `CeridClient.cascade_confirm()` | Body: `{"asset": "ETH", "liquidation_events": [...]}` |
| Longshot surface | `POST /sdk/v1/trading/longshot-surface` (`src/mcp/routers/sdk.py`) | `CeridClient.longshot_surface()` | Body: `{"asset": "ETH", "date_range": "30d"}` |
| Client ID | `X-Client-ID` header (middleware) | `X-Client-ID: trading-agent` (httpx default header) | Per-client rate limiting; default `"gui"` if absent |
| Default URL | Listens on `127.0.0.1:8888` | Connects to `http://localhost:8888` | Compatible — both resolve to loopback |
| Docker network | `llm-network` bridge | Joins same `llm-network`, uses container name `cerid-mcp` | Bypasses host port binding entirely |
| Encryption key | `~/.config/cerid/age-key.txt` | Same key path (shared `.env.age` pattern) | Same `age` key decrypts both projects' secrets |

**SDK Router:** The `/sdk/v1/` prefix is a stable contract for external consumers. The `/agent/` paths remain available for backward compatibility but may change during internal refactoring. New consumers should prefer `/sdk/v1/`.

**Per-client rate limiting:** Each `X-Client-ID` gets an independent rate budget configured in `config/settings.py:CLIENT_RATE_LIMITS`. GUI gets 20 req/min on `/agent/`, trading-agent gets 80 req/min. Unrecognized clients default to 10 req/min.

### Safe to Change (No Impact)

- **CORS origins** — `CeridClient` uses httpx (not a browser), so CORS headers are irrelevant
- **Port binding address** (`CERID_BIND_ADDR`) — Docker containers communicate via `llm-network` container names, not host ports
- **Sync encryption** — Trading agent does not use Dropbox sync
- **Email anonymization** — Trading agent does not ingest email
- **JWT/multi-user auth** — Trading agent does not send auth headers (would need `X-API-Key` if `CERID_API_KEY` is set)
- **Redis, Neo4j, ChromaDB internals** — Trading agent only touches the HTTP API layer

### Breaking Changes (Require Coordination)

| Change | Impact | Mitigation |
|--------|--------|------------|
| Rename `/agent/query` endpoint | `CeridClient.agent_query()` 404s | Update `cerid_client.py` endpoint path |
| Change `/agent/query` request schema | Query silently fails or 422s | Update `CeridClient.agent_query()` payload |
| Change `/agent/hallucination` request schema | Hallucination check 422s | Update `CeridClient.hallucination_check()` payload (fields: `response_text`, `conversation_id`) |
| Change `/agent/memory/extract` request schema | Memory extraction 422s | Update `CeridClient.memory_extract()` payload (fields: `response_text`, `conversation_id`) |
| Change `/health` response shape | Health check may false-fail | Trading agent checks `status == "ok"` — keep that key. New probes `/health/live` (liveness) and `/health/ready` (readiness, 503 when deps down) are available for orchestrator integration |
| Enable `CERID_API_KEY` | All unauthenticated requests rejected (401) | Set same key in trading agent's `.env` as `CERID_API_KEY` and add `X-API-Key` header to `CeridClient` |
| Remove `llm-network` Docker network | Container-name routing breaks | Trading agent falls back to `localhost:8888` but only if port is exposed |
| Change MCP container name from `cerid-mcp` | Docker DNS resolution fails | Update trading agent's `docker-compose.yml` `cerid_mcp_url` |

### Graceful Degradation

The trading agent handles cerid-ai unavailability via `AsyncCircuitBreaker` (5 failures → 60s open → half-open probe). When the circuit is open, the agent skips KB enrichment and operates on its own context alone. No crash, no retry storm.

### Future Considerations

- **Separate machine deployment**: If cerid-ai runs on a different host, the trading agent needs `CERID_MCP_URL` pointed to the LAN IP and cerid-ai needs `CERID_BIND_ADDR=0.0.0.0`
- **API key auth**: When `CERID_API_KEY` is enabled on cerid-ai, add the key to trading agent's `.env` and wire it into `CeridClient` headers
- **Rate limiting**: Trading agent has a dedicated 80 req/min budget via `X-Client-ID: trading-agent`. Adjust in `CONSUMER_REGISTRY` (or legacy `CLIENT_RATE_LIMITS`) in `config/settings.py`
- **New cerid-series consumers**: Send `X-Client-ID: <project-name>` header and add an entry to `CONSUMER_REGISTRY`. Use `/sdk/v1/` endpoints for stability

---

## Cross-Project: cerid-boardroom

The [cerid-boardroom](https://github.com/Cerid-AI/cerid-boardroom) agent depends on cerid-ai's SDK API for competitive intelligence and strategy brief generation.

### Coupled Interfaces

| Interface | cerid-ai Side | cerid-boardroom Side | Notes |
|-----------|---------------|----------------------|-------|
| KB query | `POST /sdk/v1/query` | `CeridClient.query()` | Shared with trading agent |
| Health check | `GET /sdk/v1/ops/health` | `CeridClient.health_check()` | Boardroom-specific health |
| Competitive scan | `POST /sdk/v1/ops/competitive-scan` | `CeridClient.competitive_scan()` | Body: `{"query": "...", "domains": [...]}` |
| Strategy brief | `POST /sdk/v1/ops/strategy-brief` | `CeridClient.strategy_brief()` | Body: `{"topic": "...", "context": {...}}` |
| Client ID | `X-Client-ID` header | `X-Client-ID: boardroom-agent` | 60 req/min budget |

---

## New Consumer Integration

For a complete 13-step checklist on adding a new cerid-series agent integration (feature flag, domain, consumer registration, endpoints, MCP tools, tests, and documentation), see [`docs/INTEGRATION_GUIDE.md`](INTEGRATION_GUIDE.md).

## CONSUMER_REGISTRY Structure

`CONSUMER_REGISTRY` in `config/settings.py` defines per-consumer access control and rate limiting. Each entry is keyed by the `X-Client-ID` header value:

```python
CONSUMER_REGISTRY = {
    "trading-agent": {
        "rate_limits": {"/agent/": (80, 60), "/sdk/": (80, 60)},  # (max_requests, window_seconds)
        "allowed_domains": ["trading", "finance"],     # KB domains this consumer can query
        "strict_domains": True,                        # disables cross-domain affinity bleed
        "description": "Cerid trading agent",
    },
    "boardroom-agent": {
        "rate_limits": {"/agent/": (60, 60), "/sdk/": (60, 60)},
        "allowed_domains": None,                       # None = all domains
        "strict_domains": False,
        "description": "Cerid boardroom agent",
        "repo": "https://github.com/Cerid-AI/cerid-boardroom",
    },
    "cli-ingest": {
        "rate_limits": {"/ingest": (30, 60)},
        "allowed_domains": None,
        "strict_domains": False,
        "description": "CLI batch ingestion tool",
    },
    "a2a-agent": {
        "rate_limits": {"/agent/": (20, 60), "/sdk/": (20, 60)},
        "allowed_domains": None,
        "strict_domains": False,
        "description": "A2A protocol remote agents",
    },
    "folder_scanner": {
        "rate_limits": {"/ingest": (20, 60)},
        "allowed_domains": None,
        "strict_domains": False,
        "description": "Folder watcher auto-ingestion",
    },
    "gui": {
        "rate_limits": {"/agent/": (20, 60), "/sdk/": (20, 60), "/ingest": (10, 60)},
        "allowed_domains": None,                       # None = all domains (GUI has full access)
        "strict_domains": False,
        "description": "React GUI",
    },
}
```

- **`rate_limit`:** Requests per minute for this consumer.
- **`allowed_domains`:** List of KB domains the consumer can query. `None` means unrestricted.
- **`strict_domains`:** When `True`, disables `DOMAIN_AFFINITY` bleed into domains not in `allowed_domains`.
- **Default behavior:** Unrecognized `X-Client-ID` values get 10 req/min and full domain access (for backward compatibility).
