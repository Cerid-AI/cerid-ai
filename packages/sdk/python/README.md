# cerid-sdk

Python client for the [Cerid AI Knowledge Companion](https://github.com/Cerid-AI/cerid-ai) HTTP API.

```bash
pip install cerid-sdk
```

Sync and async clients with typed responses. Resource groups for the
knowledge base, hallucination verification, semantic memory, and
system endpoints. Built against the stable `/sdk/v1/` surface; protocol
drift is enforced server-side by the `sdk-openapi-drift` CI gate.

## Quickstart

```python
from cerid import CeridClient

client = CeridClient(base_url="http://localhost:8888")

# Search the knowledge base
results = client.kb.query("what did I read about graph databases last week?")
for chunk in results.chunks:
    print(chunk.text, chunk.score)

# Verify a generated answer against the KB
report = client.verify.check(
    response_text="Cerid uses Neo4j for graph storage.",
    conversation_id="demo",
)
print(report.faithfulness, report.unsupported_claims)

# Recall semantic memory
memories = client.memory.recall("preferred deployment topology", top_k=5)
```

## Async client

```python
import asyncio
from cerid import AsyncCeridClient

async def main():
    async with AsyncCeridClient(base_url="http://localhost:8888") as client:
        results = await client.kb.query("graph databases")
        print(results.total)

asyncio.run(main())
```

## Resource groups

| Group | Endpoint | Purpose |
|---|---|---|
| `client.kb` | `/sdk/v1/query`, `/ingest`, `/search`, `/collections`, `/taxonomy` | Search + ingest the personal knowledge base |
| `client.verify` | `/sdk/v1/hallucination` | Streaming claim extraction + NLI-gated verification |
| `client.memory` | `/sdk/v1/memory/extract` | Semantic memory recall + extraction |
| `client.system` | `/sdk/v1/health`, `/health/detailed`, `/settings`, `/plugins` | Operational endpoints |

## Authentication

Cerid is local-first by default — `CeridClient(base_url=...)` connects
to a self-hosted instance with no credentials. For deployments behind
an authenticating proxy, pass headers via the underlying transport:

```python
client = CeridClient(
    base_url="https://cerid.your-org.internal",
    headers={"Authorization": "Bearer <token>"},
)
```

## Compatibility

- Python 3.9+ (3.11 / 3.12 actively tested)
- httpx 0.25+ (transport)
- pydantic 2.0+ (response models)

## Stability contract

The `/sdk/v1/` surface and this client's public types
(`CeridClient`, `AsyncCeridClient`, `CeridSDKError`, response models)
follow [semantic versioning](https://semver.org/). Any breaking
change to a `/sdk/v1/` endpoint is enforced caught by the
`sdk-openapi-drift` CI gate in the server repo.

`SDK_PROTOCOL_VERSION` in `cerid.__version__` mirrors the server's
expected wire-protocol version. Mismatched server / client versions
will surface as a `CeridSDKError` rather than silent payload skew.

## License

Apache-2.0. Source at the [Cerid AI repository](https://github.com/Cerid-AI/cerid-ai).
