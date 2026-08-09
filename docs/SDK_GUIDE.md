# Cerid AI SDK Guide

Stable, versioned API for external consumers at `/sdk/v1/`. This contract
survives internal refactoring of core paths. Current wire-protocol version:
**1.1.0**. Client packages are published on
[PyPI (`cerid-sdk`)](https://pypi.org/project/cerid-sdk/) and
[npm (`@cerid-ai/sdk`)](https://www.npmjs.com/package/@cerid-ai/sdk) —
both **0.1.1**, targeting the 17-endpoint `/sdk/v1/` surface. The SDK versions independently
of the product; current release 0.1.x on PyPI/npm (per
`docs/SDK_PUBLISHING.md`).

## Overview

The SDK exposes 17 endpoints covering knowledge-base operations, health
monitoring, content ingestion (text / file / adapter-shaped), taxonomy,
search, plugin discovery, smart-routed LLM completion, async memory
extraction with job polling, and server configuration. All endpoints
return typed JSON responses defined by Pydantic models in `models/sdk.py`.

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
# The distribution is `cerid-sdk`; the import name is `cerid`
# (verified against the published 0.1.1 wheel).
from cerid import CeridClient

client = CeridClient(
    base_url="http://localhost:8888",
    client_id="my-app",
    api_key="sk-cerid-...",  # optional  # pragma: allowlist secret
)

# Query the knowledge base (domains is a list; mix your own + built-ins)
result = client.kb.query("How does the circuit breaker work?", domains=["coding"])
print(result.results[0].content)

# Check service health
health = client.system.health()
print(health.version, health.services)

# Ingest content — any domain name works; attach provenance metadata
resp = client.kb.ingest(
    "PostgreSQL uses MVCC for concurrency.",
    domain="databases",
    metadata={"title": "MVCC note", "provenance": "design_review"},
)
print(resp.artifact_id, resp.chunks)

# Verify claims
check = client.verify.check(
    "Redis defaults to port 6380.",
    context="What port does Redis use?",
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

// Query the knowledge base (domains is a list)
const result = await client.kb.query({ query: "circuit breaker pattern", domains: ["coding"], topK: 5 });
console.log(result.results[0].content);

// Check health
const health = await client.system.health();
console.log(health.version, health.services);

// Ingest content with provenance metadata (any domain name works)
const resp = await client.kb.ingest({
  content: "PostgreSQL uses MVCC for concurrency.",
  domain: "databases",
  metadata: { title: "MVCC note", provenance: "design_review" },
});
console.log(resp.artifact_id, resp.chunks);
```

## Using Cerid as a backend for external agents / clients

Cerid works as a shared **knowledge + LLM + memory backend** for other
applications (agent teams, internal tools, vertical products). Clients use
their own domains, attach provenance, and route custom LLM tasks with **no
server-side configuration and no compatibility shims**.

### Custom knowledge domains

Ingest to and query **any domain name** — not just the built-in set. A custom
domain needs no pre-registration: ingest creates its collection on first use,
and queries against it return your content. An unknown domain with no data
degrades to empty results (never a 400). List your domain explicitly so your
private context is searched first.

```python
client.kb.ingest("Q3 launch plan: target accounts and sequence.", domain="my_gtm")
result = client.kb.query("Q3 launch sequence", domains=["my_gtm", "general"])
```

> Operators: built-in domains can carry descriptions/icons via the
> `CERID_CUSTOM_DOMAINS` env var, but ad-hoc client domains work without it.

### Rich provenance metadata

Attach arbitrary metadata to any ingest — stored with the artifact and returned
at retrieval, so client outputs keep their attribution. The legacy `tags`
field is preserved alongside it.

```python
client.kb.ingest(
    "Decision: adopt MVCC for the ledger store.",
    domain="my_decisions",
    metadata={"title": "ADR-014", "provenance": "design_review", "source_file": "adr-014.md"},
)
```

### Flexible LLM task types

`client.llm.complete` accepts your own `task_type` labels (e.g. `"gtm_creative"`,
`"agent_phase_2"`). Built-in types (`chat`, `internal`, `verification`,
`classification`) route to tuned tiers; **unknown values map to safe internal
routing** rather than failing.

```python
out = client.llm.complete(
    messages=[{"role": "user", "content": "Draft a one-line value prop."}],
    task_type="gtm_creative",   # custom — routed as internal
)
```

### Operator visibility

`GET /health` reports `invariants.custom_collections` — the client-created
collections — so operators can see external-client activity. Built-in
"empty collection" alerts are scoped to built-in domains and won't fire on a
freshly-created client domain.

## Endpoint Reference

| # | Method | Path | Description |
|---|--------|------|-------------|
| 1 | POST | `/sdk/v1/query` | Multi-domain KB search with hybrid BM25+vector retrieval |
| 2 | POST | `/sdk/v1/hallucination` | Verify factual claims against the KB |
| 3 | POST | `/sdk/v1/memory/extract` | Extract facts from conversation text and store as artifacts |
| 4 | GET | `/sdk/v1/memory/extract/jobs/{job_id}` | Poll an async memory_extract job (when `MEMORY_QUEUE_MODE=async`) |
| 5 | POST | `/sdk/v1/llm/complete` | Smart-routed LLM completion across FREE / CHEAP / CAPABLE / RESEARCH / EXPERT tiers |
| 6 | GET | `/sdk/v1/health` | Service connectivity, version, and feature flags |
| 7 | POST | `/sdk/v1/ingest` | Ingest raw text content into the KB |
| 8 | POST | `/sdk/v1/ingest/file` | Ingest a file (PDF, DOCX, code, 30+ formats) |
| 9 | POST | `/sdk/v1/ingest/external` | Adapter-shaped ingest for external services (Readwise / Pocket / Telegram-bot / Raindrop / Instapaper, with arbitrary `field_mappings` config) |
| 10 | GET | `/sdk/v1/collections` | List all KB collections (one per domain) |
| 11 | GET | `/sdk/v1/taxonomy` | Domain taxonomy tree with sub-categories and tags |
| 12 | GET | `/sdk/v1/health/detailed` | Extended health with circuit breakers and degradation tier |
| 13 | GET | `/sdk/v1/settings` | Read-only server config: version, tier, feature flags |
| 14 | POST | `/sdk/v1/search` | Direct vector search without agent orchestration |
| 15 | GET | `/sdk/v1/plugins` | List loaded plugins with status and tier |
| 16 | POST | `/sdk/v1/ingest/webhook/{token}` | Token-gated webhook receiver (provider payloads normalized via adapter recipes; returns 202) |
| 17 | POST | `/sdk/v1/ingest/voice-note` | Voice-note transcribe + ingest |

### Request/Response Examples

**POST /sdk/v1/query**

```json
// Request — domains is a list; any name (built-in or custom client domain) is accepted
{"query": "circuit breaker pattern", "domains": ["coding"], "top_k": 5}

// Response
{"results": [{"content": "...", "relevance": 0.92, "domain": "coding"}], "domains_searched": ["coding"], "total_results": 1}
```

**POST /sdk/v1/ingest**

```json
// Request — `metadata` is arbitrary provenance, stored + retrievable; `tags` is preserved alongside it
{"content": "PostgreSQL uses MVCC.", "domain": "databases", "metadata": {"title": "MVCC note", "provenance": "design_review"}, "tags": "postgres"}

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
