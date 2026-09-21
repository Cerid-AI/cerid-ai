# cerid-sdk

Python client for the [Cerid AI Knowledge Companion](https://github.com/Cerid-AI/cerid-ai) HTTP API.

```bash
pip install cerid-sdk
```

Sync and async clients with typed responses. Resource groups for the
knowledge base, hallucination verification, semantic memory, smart-routed
LLM completion, and system endpoints. Built against the stable `/sdk/v1/`
surface; drift between the server's routes and the published spec is caught
in CI by `scripts/gen_sdk_openapi.py --check`.

## Quickstart

`client_id` is required — it is the `X-Client-ID` header the server uses for
per-client rate limiting and domain scoping.

```python
from cerid import CeridClient
from cerid.models import MemoryExtractAcceptedResponse

client = CeridClient(base_url="http://localhost:8888", client_id="my-app")

# Search the knowledge base. Results are plain dicts — the server's metadata
# keys vary by source, so they are not flattened into attributes.
answer = client.kb.query("what did I read about graph databases last week?", top_k=5)
print(answer.context)
for hit in answer.results:
    print(hit["content"], hit.get("relevance"))

# Verify a generated answer against the KB. conversation_id is required.
report = client.verify.check(
    "Cerid uses Neo4j for graph storage.",
    conversation_id="demo",
)
for claim in report.claims:
    print(claim["claim"], claim["status"], claim["confidence"])
print(report.summary["overall_confidence"], "nli_skipped:", report.nli_skipped)

# Extract memories from a conversation. On a server running the extraction
# queue this returns a 202 envelope to poll instead of an inline result.
result = client.memory.extract("I prefer dark mode.", conversation_id="demo")
if isinstance(result, MemoryExtractAcceptedResponse):
    job = client.memory.get_job(result.job_id)
    print(job.status)
else:
    print(result.memories_stored, "memories stored")

client.close()
```

## Async client

```python
import asyncio
from cerid import AsyncCeridClient

async def main():
    async with AsyncCeridClient(
        base_url="http://localhost:8888",
        client_id="my-app",
    ) as client:
        answer = await client.kb.query("graph databases")
        print(answer.total_results)

asyncio.run(main())
```

## Resource groups

| Group | Endpoints | Purpose |
|---|---|---|
| `client.kb` | `query`, `search`, `ingest`, `ingest_file`, `ingest_external`, `collections`, `taxonomy` | Search + ingest the personal knowledge base |
| `client.verify` | `check` | Claim extraction + NLI-gated verification |
| `client.memory` | `extract`, `get_job` | Memory extraction, sync or queued |
| `client.llm` | `complete` | Smart-routed completion across model tiers |
| `client.system` | `health`, `health_detailed`, `settings`, `plugins` | Operational endpoints |

## Authentication

Cerid is local-first: a self-hosted instance needs no credentials beyond the
`client_id`. Servers that set `CERID_API_KEY` also require an API key, which
the client sends as `X-API-Key`:

```python
client = CeridClient(
    base_url="https://cerid.your-org.internal",
    client_id="my-app",
    api_key="sk-cerid-...",  # pragma: allowlist secret
)
```

## Errors

Every non-2xx response raises a typed error carrying the HTTP status:

| Error | Raised on |
|---|---|
| `AuthenticationError` | 401 / 403 |
| `NotFoundError` | 404 |
| `ValidationError` | 422 |
| `RateLimitError` | 429 — `retry_after` carries `Retry-After` when the server sends it |
| `ServiceUnavailableError` | 503 |
| `ProtocolVersionError` | server reports an incompatible wire-protocol major version |
| `CeridSDKError` | base class; raised directly for any other non-2xx |

## Compatibility

- Python 3.9+ (3.11 / 3.12 actively tested)
- httpx 0.25+ (transport)
- pydantic 2.0+ (response models)

## Stability contract

The `/sdk/v1/` surface and this client's public types
(`CeridClient`, `AsyncCeridClient`, the error hierarchy, response models)
follow [semantic versioning](https://semver.org/). Any breaking
change to a `/sdk/v1/` endpoint is caught by the spec drift check in the
server repo's CI (`scripts/gen_sdk_openapi.py --check`, run in the `lint` job).

`SDK_PROTOCOL_VERSION` in `cerid.__version__` pins the wire-protocol version
this client was built against. `client.system.health()`, `health_detailed()`
and `settings()` compare it against the version the server reports and raise
`ProtocolVersionError` on a major-version mismatch, rather than letting
payload skew pass silently.

## License

Apache-2.0. Source at the [Cerid AI repository](https://github.com/Cerid-AI/cerid-ai).
