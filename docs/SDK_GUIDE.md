# Cerid AI SDK Guide

Stable, versioned API for external consumers at `/sdk/v1/`. This contract
survives internal refactoring of core paths. Current wire-protocol version:
**1.2.0**. Client packages are `cerid-sdk`
([PyPI](https://pypi.org/project/cerid-sdk/)) and `@cerid-ai/sdk`
([npm](https://www.npmjs.com/package/@cerid-ai/sdk)), both **0.2.0** in this
tree; the registries still serve 0.1.1 until 0.2.0 is published, which is a
separate step (`docs/SDK_PUBLISHING.md`). The SDK versions independently of the
product. Additive in 1.2.0: `POST /sdk/v1/memory/recall`,
`POST /sdk/v1/ingest/upload` (multipart), `DELETE /sdk/v1/artifacts/{id}`
(consumer-scoped). Restricted domains raise HTTP 403 with
`retrieval_reason: consumer_domain_restricted` (SDK: `DomainRestrictedError`).

## Overview

The `/sdk/v1/` surface is 20 endpoints. The client libraries wrap **18** of
them, covering knowledge-base operations, health monitoring, content ingestion
(text / path / multipart / adapter-shaped), taxonomy, search, plugin discovery,
smart-routed LLM completion, memory extraction with job polling, memory recall,
consumer-scoped artifact delete, and server configuration. The remaining two —
the webhook receiver and the voice-note upload — are HTTP-only by design; see
the endpoint table. All JSON endpoints return typed responses defined by
Pydantic models in `src/mcp/app/models/sdk.py`.

## Authentication

**X-Client-ID** (required): Identifies your consumer for per-client rate
limiting and domain scoping. Every request must include this header.

**X-API-Key** (conditional): Required only when the server sets
`CERID_API_KEY`. Pass via the `X-API-Key` header.

```
X-Client-ID: my-app
X-API-Key: sk-cerid-...
```

Sibling env names (do not put the server `CERID_API_KEY` in a product client):

| Product | URL | Key | Client ID |
|---------|-----|-----|-----------|
| Trading | `TRADING_CERID_URL` (alias `CERID_MCP_URL`) | `TRADING_CERID_API_KEY` | `trading-agent` |
| Finance | DB `cerid_url` | DB `cerid_api_key` | `cerid-finance` |
| Anneal | `ANNEAL_CERID_URL` | `ANNEAL_CERID_API_KEY` | `cerid-anneal` |
| Boardroom | `BOARDROOM_CERID_MCP_URL` | `BOARDROOM_CERID_API_KEY` | (internal consumer id) |
| Server | n/a | `CERID_API_KEY` | n/a |

## Client cache (product policy, not in the SDK)

Query L1 caches are owned by the product wrapper. Do not cache ingest, health, or llm.

| Client | Query L1 TTL |
|--------|----------------|
| Trading | 20s (+ Redis 120s) |
| Boardroom | 30s (+ Redis 180s) |
| Finance | 5 min (LRU 200) |
| Anneal | none (write outbox is durability) |
| SDK | none |

## OpenAPI Spec

The full OpenAPI 3.x specification is available at:

```
GET /sdk/v1/openapi.json
```

Use this to generate client SDKs or import into API tools (Postman, Insomnia).
The spec declares both auth headers as `apiKey` security schemes, so a
generated client sends `X-Client-ID` (and `X-API-Key` when configured) without
hand-editing. The same document is committed at
[`docs/openapi-sdk-v1.json`](openapi-sdk-v1.json).

## Python SDK Quickstart

```bash
pip install cerid-sdk
```

```python
# The distribution is `cerid-sdk`; the import name is `cerid`.
# Sibling products vendor 0.2.0 until PyPI publish.
from cerid import CeridClient

client = CeridClient(
    base_url="http://localhost:8888",
    client_id="my-app",
    api_key="sk-cerid-...",  # optional  # pragma: allowlist secret
)

# Query the knowledge base (domains is a list; mix your own + built-ins).
# Results are plain dicts: the metadata keys vary by source.
result = client.kb.query("How does the circuit breaker work?", domains=["coding"])
print(result.results[0]["content"])

# Check service health
health = client.system.health()
print(health.version, health.services)

# Ingest content — any domain in your consumer's grant works; attach provenance metadata
resp = client.kb.ingest(
    "PostgreSQL uses MVCC for concurrency.",
    domain="databases",
    metadata={"title": "MVCC note", "provenance": "design_review"},
)
print(resp.artifact_id, resp.chunks)

# Verify claims — conversation_id is required (the server files the
# verification under it)
check = client.verify.check("Redis defaults to port 6380.", conversation_id="demo")
for claim in check.claims:
    print(claim["status"], claim["confidence"])
print(check.summary["overall_confidence"], "nli_skipped:", check.nli_skipped)

# Extract memories. Servers running the extraction queue answer 202 with a
# job to poll instead of an inline result — branch on the returned type.
from cerid.models import MemoryExtractAcceptedResponse

extracted = client.memory.extract("I prefer dark mode.", conversation_id="demo")
if isinstance(extracted, MemoryExtractAcceptedResponse):
    print(client.memory.get_job(extracted.job_id).status)
else:
    print(extracted.memories_stored, "memories stored")
```

## TypeScript SDK Quickstart

```bash
npm install @cerid-ai/sdk
```

```typescript
import { CeridClient, isMemoryExtractAccepted } from "@cerid-ai/sdk";

const client = new CeridClient({
  baseUrl: "http://localhost:8888",
  clientId: "my-app",
  apiKey: "sk-cerid-...", // optional  // pragma: allowlist secret
});

// Query the knowledge base (domains is a list; the field is top_k, matching
// the wire contract — there is no camelCase alias)
const result = await client.kb.query({ query: "circuit breaker pattern", domains: ["coding"], top_k: 5 });
console.log(result.results[0].content);

// Verify claims — conversation_id is required
const check = await client.verify.check({
  response_text: "Redis defaults to port 6380.",
  conversation_id: "demo",
});
console.log(check.summary.overall_confidence, check.nli_skipped);

// Extract memories; a queued server answers 202 with a job to poll
const extracted = await client.memory.extract({
  response_text: "I prefer dark mode.",
  conversation_id: "demo",
});
if (isMemoryExtractAccepted(extracted)) {
  const job = await client.memory.getJob(extracted.job_id);
  console.log(job.status);
} else {
  console.log(extracted.memories_stored, "memories stored");
}

// Check health
const health = await client.system.health();
console.log(health.version, health.services);

// Ingest content with provenance metadata (any domain in your consumer's grant works)
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

Ingest to and query **any domain in your consumer's grant** — not just the
built-in set. A custom domain needs no pre-registration of the domain itself:
ingest creates its collection on first use, and queries against it return your
content. An unknown domain with no data degrades to empty results (never a
400). List your domain explicitly so your private context is searched first.

The grant is your consumer's `allowed_domains` in `CONSUMER_REGISTRY`, keyed by
the `X-Client-ID` you send. Reading or writing a domain outside it returns
**403** `consumer_domain_restricted` (the SDKs raise `DomainRestrictedError`),
and a client ID that is not in the registry is granted `general` only — so a new
integration needs a registry entry naming its domains.

```python
client.kb.ingest("Q3 launch plan: target accounts and sequence.", domain="my_gtm")
result = client.kb.query("Q3 launch sequence", domains=["my_gtm", "general"])
```

> Operators: built-in domains can carry descriptions/icons via the
> `CERID_CUSTOM_DOMAINS` env var, but ad-hoc client domains work without it.

### Keeping web results out

When the knowledge base answers weakly (best match below
`RETRIEVAL_QUALITY_THRESHOLD`) or a time-scoped question finds nothing inside its
window, the query also consults external web sources and **appends** those rows
beside yours, tagged `source_type`/`domain` `"external"`. To keep them out, gate
the surface with `context_sources`:

```python
result = client.kb.query(
    "net worth and safe to spend", domains=["finance"], strict_domains=True,
    context_sources={"kb": True, "memory": True, "external": False},
)
```

```typescript
await client.kb.query({
  query: "net worth and safe to spend", domains: ["finance"], strict_domains: true,
  context_sources: { kb: true, memory: true, external: false },
});
```

`context_sources` is the only field that does this, in every `rag_mode`. It is
easy to reach for the wrong one: `strict_domains` only narrows the knowledge
base's own cross-domain bleed and never touches the web, and `source_config`'s
toggles are read only under `rag_mode: "custom_smart"`.

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

Endpoints 1-15 are wrapped by both client libraries. **16 and 17 are
HTTP-only**: the webhook is an inbound receiver an external service posts to
(nothing for a client to call), and the voice-note route takes a multipart
upload, which neither client library models. Call those two with a plain HTTP
request.

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
{"version": "1.2.0", "tier": "community", "features": {"hallucination_check": true, "workflow_engine": false}}
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

Errors use FastAPI's envelope — a single `detail` key. Both SDKs read it and
raise a typed error from it:

```json
{"detail": "Field 'query' is required"}
```

`detail` is a string on most paths and an object on the SLO-budget 503, which
reports the budget it could not meet:

```json
{"detail": {"error": "slo_budget_unsatisfiable", "budget_ms": 800, "floor_p95_ms": 2400}}
```

| Status | Meaning |
|--------|---------|
| 200 | Success |
| 202 | Accepted for background processing (async memory extract) |
| 401 | Missing or invalid `X-API-Key` |
| 403 | Authenticated but not permitted for this domain |
| 404 | No such resource (e.g. an unknown memory-extract `job_id`) |
| 422 | Invalid request parameters |
| 429 | Rate limit exceeded (check `Retry-After` header) |
| 503 | Backend service unavailable, or no model tier fits `slo_budget_ms` |

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
