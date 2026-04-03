# Cerid AI SDK Integration Guide

Build clients that integrate with Cerid AI via the stable SDK endpoints at
`/sdk/v1/`. This versioned contract survives internal refactoring of core paths.

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/sdk/v1/query` | KB search with hybrid BM25+vector retrieval |
| POST | `/sdk/v1/hallucination` | Verify factual claims against the KB |
| POST | `/sdk/v1/memory/extract` | Extract and store knowledge from text |
| GET | `/sdk/v1/health` | Service health and feature flags |

Current SDK version: **1.1.0**.

## Authentication

**X-Client-ID** (required): Identifies your consumer for rate limiting and
domain scoping. Every request must include this header.

**X-API-Key** (optional): Required only when the server has `CERID_API_KEY` set.

## Consumer Registration

Consumers are configured in `config/settings.py` via `CONSUMER_REGISTRY`. Each
entry defines a rate limit (requests/minute) and allowed KB domains. Results are
automatically scoped to the consumer's allowed domains.

## Endpoint Reference

### POST /sdk/v1/query

**Request:**
```json
{"query": "How does the circuit breaker pattern work?", "domain": "code", "top_k": 5}
```

**Response:**
```json
{
  "answer": "The circuit breaker pattern prevents cascading failures...",
  "sources": [{"title": "circuit_breaker.py", "domain": "code", "similarity": 0.92, "chunk_text": "..."}],
  "query": "How does the circuit breaker pattern work?",
  "domain": "code"
}
```

### POST /sdk/v1/hallucination

**Request:**
```json
{"response_text": "Redis uses port 6380 by default.", "context": "Redis configuration"}
```

**Response:**
```json
{
  "claims": [{"claim": "Redis uses port 6380 by default", "status": "unverified", "confidence": 0.15, "sources": []}],
  "overall_score": 0.15,
  "verified_count": 0,
  "total_claims": 1
}
```

Claim statuses: `verified`, `unverified`, `uncertain`.

### POST /sdk/v1/memory/extract

**Request:**
```json
{"text": "We decided to use PostgreSQL 16 for the finance project.", "conversation_id": "finance-001"}
```

**Response:**
```json
{
  "extracted": [{"fact": "Finance project uses PostgreSQL 16", "type": "decision", "confidence": 0.95}],
  "stored_count": 1
}
```

### GET /sdk/v1/health

**Response:**
```json
{
  "status": "healthy",
  "version": "1.1.0",
  "features": {
    "enable_hallucination_check": true,
    "enable_feedback_loop": true,
    "enable_self_rag": true,
    "enable_memory_extraction": true
  },
  "internal_llm": {"provider": "openrouter", "model": "anthropic/claude-sonnet-4", "ollama_enabled": false}
}
```

## Rate Limiting

Per-client sliding window keyed by `X-Client-ID`. Each consumer has an
independent counter. Exceeding the limit returns HTTP 429 with `Retry-After`.
Requests without `X-Client-ID` share a global bucket.

## Error Handling

All errors follow the `CeridError` JSON format:

```json
{"error": {"type": "ValidationError", "message": "Field 'query' is required", "code": "VALIDATION_ERROR"}}
```

| Code | Meaning |
|------|---------|
| 200 | Success |
| 422 | Invalid request parameters |
| 429 | Rate limit exceeded |
| 503 | Backend service unavailable |

On 503, check `/sdk/v1/health` for details on which services are affected.

## Example Integration

```python
import httpx

CERID_URL = "http://localhost:8888"
CLIENT_ID = "my-app"

async def query_kb(query: str, domain: str = "general") -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{CERID_URL}/sdk/v1/query",
            json={"query": query, "domain": domain, "top_k": 5},
            headers={"X-Client-ID": CLIENT_ID},
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json()

async def check_health() -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{CERID_URL}/sdk/v1/health",
            headers={"X-Client-ID": CLIENT_ID},
            timeout=5.0,
        )
        resp.raise_for_status()
        return resp.json()
```

For production, add retry logic with exponential backoff on 503 and respect the
`Retry-After` header on 429.

## MCP SSE Transport

For tool-based integration, Cerid AI also exposes an MCP server over SSE
transport. Tools are prefixed with `pkb_` (e.g., `pkb_query`,
`pkb_ingest_content`). See [API_REFERENCE.md](API_REFERENCE.md) for the full
tool list.

## Best Practices

- Always send `X-Client-ID` for proper rate limit isolation and domain scoping.
- Use `/sdk/v1/health` as a readiness probe before sending traffic.
- Handle 503 gracefully -- the system has a 5-tier degradation model.
- Pin to the SDK version. Breaking changes will use a new prefix (`/sdk/v2/`).
