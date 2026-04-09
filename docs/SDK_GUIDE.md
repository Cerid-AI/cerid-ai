# Cerid AI SDK Guide

Stable, versioned API for external consumers at `/sdk/v1/`. This contract
survives internal refactoring of core paths. Current version: **1.1.0**.

## Overview

The SDK exposes 12 endpoints covering knowledge base operations, health
monitoring, content ingestion, taxonomy, search, plugin discovery, and
server configuration. All endpoints return typed JSON responses defined
by Pydantic models in `models/sdk.py`.

## Authentication

**X-Client-ID** (required): Identifies your consumer for per-client rate
limiting and domain scoping. Every request must include this header.

**X-API-Key** (conditional): Required only when the server sets
`CERID_API_KEY`. Pass via the `X-API-Key` header.

```
X-Client-ID: my-app
X-API-Key: sk-cerid-...
```

## OpenAPI Spec

The full OpenAPI 3.x specification is available at:

```
GET /sdk/v1/openapi.json
```

Use this to generate client SDKs or import into API tools (Postman, Insomnia).

## Python SDK Quickstart

```bash
pip install cerid-sdk
```

```python
from cerid_sdk import CeridClient

client = CeridClient(
    base_url="http://localhost:8888",
    client_id="my-app",
    api_key="sk-cerid-...",  # optional  # pragma: allowlist secret
)

# Query the knowledge base
result = client.query("How does the circuit breaker work?", domain="coding")
print(result.results[0].content)

# Check service health
health = client.health()
print(health.version, health.services)

# Ingest content
resp = client.ingest("PostgreSQL uses MVCC for concurrency.", domain="databases")
print(resp.artifact_id, resp.chunks)

# Verify claims
check = client.hallucination_check(
    response_text="Redis defaults to port 6380.",
    query="What port does Redis use?",
)
for claim in check.claims:
    print(claim.status, claim.confidence)
```

## TypeScript SDK Quickstart

```bash
npm install @cerid-ai/sdk
```

```typescript
import { CeridClient } from "@cerid-ai/sdk";

const client = new CeridClient({
  baseUrl: "http://localhost:8888",
  clientId: "my-app",
  apiKey: "sk-cerid-...", // optional  // pragma: allowlist secret
});

// Query the knowledge base
const result = await client.query("circuit breaker pattern", {
  domain: "coding",
  topK: 5,
});
console.log(result.results[0].content);

// Check health
const health = await client.health();
console.log(health.version, health.services);

// Ingest a file
const resp = await client.ingestFile("/data/report.pdf", {
  domain: "finance",
  tags: ["quarterly"],
});
console.log(resp.artifactId, resp.chunks);
```

## Endpoint Reference

| # | Method | Path | Description |
|---|--------|------|-------------|
| 1 | POST | `/sdk/v1/query` | Multi-domain KB search with hybrid BM25+vector retrieval |
| 2 | POST | `/sdk/v1/hallucination` | Verify factual claims against the KB |
| 3 | POST | `/sdk/v1/memory/extract` | Extract facts from conversation text and store as artifacts |
| 4 | GET | `/sdk/v1/health` | Service connectivity, version, and feature flags |
| 5 | POST | `/sdk/v1/ingest` | Ingest raw text content into the KB |
| 6 | POST | `/sdk/v1/ingest/file` | Ingest a file (PDF, DOCX, code, 30+ formats) |
| 7 | GET | `/sdk/v1/collections` | List all KB collections (one per domain) |
| 8 | GET | `/sdk/v1/taxonomy` | Domain taxonomy tree with sub-categories and tags |
| 9 | GET | `/sdk/v1/health/detailed` | Extended health with circuit breakers and degradation tier |
| 10 | GET | `/sdk/v1/settings` | Read-only server config: version, tier, feature flags |
| 11 | POST | `/sdk/v1/search` | Direct vector search without agent orchestration |
| 12 | GET | `/sdk/v1/plugins` | List loaded plugins with status and tier |

### Request/Response Examples

**POST /sdk/v1/query**

```json
// Request
{"query": "circuit breaker pattern", "domain": "coding", "top_k": 5}

// Response
{"results": [{"content": "...", "relevance": 0.92, "domain": "coding"}], "domains_searched": ["coding"], "total_results": 1}
```

**POST /sdk/v1/ingest**

```json
// Request
{"content": "PostgreSQL uses MVCC.", "domain": "databases", "tags": ["postgres"]}

// Response
{"status": "success", "artifact_id": "art-200", "chunks": 1, "domain": "databases"}
```

**POST /sdk/v1/search**

```json
// Request
{"query": "JWT authentication", "domain": "coding", "top_k": 10}

// Response
{"results": [{"title": "auth.py", "similarity": 0.88}], "total_results": 1, "confidence": 0.88}
```

**GET /sdk/v1/settings**

```json
{"version": "1.1.0", "tier": "community", "features": {"hallucination_check": true, "workflow_engine": false}}
```

## Rate Limiting

Per-client sliding window keyed by `X-Client-ID`. Each consumer has an
independent counter configured in `CONSUMER_REGISTRY`. Exceeding the limit
returns HTTP 429 with a `Retry-After` header. Requests without
`X-Client-ID` share a global bucket with a lower limit.

Default limits:

| Consumer | Requests/min |
|----------|-------------|
| trading-agent | 80 |
| finance-dashboard | 40 |
| gui (internal) | 200 |
| Default (unregistered) | 30 |

## Error Handling

All errors follow the `CeridError` JSON format:

```json
{
  "error": {
    "type": "ValidationError",
    "message": "Field 'query' is required",
    "code": "VALIDATION_ERROR"
  }
}
```

| Status | Meaning |
|--------|---------|
| 200 | Success |
| 422 | Invalid request parameters |
| 429 | Rate limit exceeded (check `Retry-After` header) |
| 503 | Backend service unavailable |

On 503, call `GET /sdk/v1/health` or `GET /sdk/v1/health/detailed` to
inspect which services are down and the current degradation tier.

## Consumer Registration

Consumers are registered in `config/settings.py` via `CONSUMER_REGISTRY`.
Each entry defines:

- **rate_limit**: Maximum requests per minute
- **allowed_domains**: KB domains the consumer can access (results are
  automatically scoped)
- **description**: Human-readable purpose

```python
CONSUMER_REGISTRY = {
    "trading-agent": {
        "rate_limit": 80,
        "allowed_domains": ["trading", "finance", "general"],
        "description": "DeFi trading agent",
    },
    "finance-dashboard": {
        "rate_limit": 40,
        "allowed_domains": ["finance", "general"],
        "description": "Personal finance dashboard",
    },
}
```

To add a new consumer, append an entry and redeploy the MCP server.

## MCP Tool Access

For tool-based integration, Cerid AI also exposes an MCP server over SSE
transport at the same host. Tools are prefixed with `pkb_` (e.g.,
`pkb_query`, `pkb_ingest_content`). This is useful for LLM agents that
natively support the Model Context Protocol. See
[API_REFERENCE.md](API_REFERENCE.md) for the full tool list.

The REST SDK endpoints and MCP tools share the same backend services and
middleware stack. Choose REST for traditional HTTP clients, MCP for
agent-to-agent communication.
