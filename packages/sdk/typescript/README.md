# @cerid-ai/sdk

TypeScript client for the [Cerid AI](https://github.com/Cerid-AI/cerid-ai)
`/sdk/v1` API — a self-hosted, privacy-first personal AI knowledge companion.

## Install

```bash
npm install @cerid-ai/sdk
```

## Usage

`clientId` is required — it is the `X-Client-ID` header the server uses for
per-client rate limiting and domain scoping.

```ts
import { CeridClient } from '@cerid-ai/sdk'

const cerid = new CeridClient({
  baseUrl: 'http://127.0.0.1:8888',
  apiKey: process.env.CERID_API_KEY,
  clientId: 'my-app', // per-client rate limiting + domain scoping
})

const answer = await cerid.kb.query({ query: 'what did I decide about the storage layout?' })
console.log(answer.context)
for (const hit of answer.results) {
  console.log(hit.content, hit.relevance)
}
```

Endpoints are grouped by resource: `cerid.kb`, `cerid.verify`, `cerid.memory`,
`cerid.llm`, `cerid.system`. See
[docs/SDK_GUIDE.md](https://github.com/Cerid-AI/cerid-ai/blob/main/docs/SDK_GUIDE.md)
for the full surface.

## Errors

Failures raise typed errors carrying the real HTTP status, so callers can
branch on the cause rather than parsing strings:

| Error | Raised on |
|---|---|
| `AuthenticationError` | 401 / 403 |
| `NotFoundError` | 404 |
| `ValidationError` | 422 |
| `RateLimitError` | 429 — `retryAfter` carries `Retry-After` when the server sends it |
| `ServiceUnavailableError` | 503 — `retryAfter` likewise on the SLO-budget path |
| `ProtocolVersionError` | server reports an incompatible wire-protocol major version |
| `CeridSDKError` | base class; raised directly for any other non-2xx |

## Requirements

A running Cerid AI instance. The API is versioned at `/sdk/v1` and pinned by
contract tests against `docs/openapi-sdk-v1.json`, so this client keeps working
across internal refactors of the underlying `/agent/` routes.

The package is **ESM-only** (no CommonJS `require` build) and uses global
`fetch`, so it needs Node 18.17+ or any modern browser/runtime. Pass your own
`fetch` implementation via the `fetch` option if you need to intercept
requests.

## Compatibility

Independently versioned from the Cerid AI product: an `0.x` SDK is expected to
talk to a `1.x` server. The `/sdk/v1` contract is what binds them, and the
exported `SDK_PROTOCOL_VERSION` names the wire protocol this build targets —
`system.health()` and `system.settings()` raise `ProtocolVersionError` if the
server reports a different major version.

## License

Apache-2.0. The Cerid AI product itself is FSL-1.1-ALv2 (source-available,
converting to Apache-2.0 two years after each release); the SDKs are Apache-2.0
so integrating with Cerid carries no copyleft or source-availability obligation.
