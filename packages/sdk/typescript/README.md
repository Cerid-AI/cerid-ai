# @cerid-ai/sdk

TypeScript client for the [Cerid AI](https://github.com/Cerid-AI/cerid-ai)
`/sdk/v1` API — a self-hosted, privacy-first personal AI knowledge companion.

## Install

```bash
npm install @cerid-ai/sdk
```

## Usage

```ts
import { CeridClient } from '@cerid-ai/sdk'

const cerid = new CeridClient({
  baseUrl: 'http://127.0.0.1:8888',
  apiKey: process.env.CERID_API_KEY,
  clientId: 'my-app', // per-client rate limiting + domain scoping
})

const answer = await cerid.query('what did I decide about the storage layout?')
console.log(answer)
```

## Errors

Failures raise typed errors carrying the real HTTP status, so callers can
branch on the cause rather than parsing strings:

| Error | Raised on |
|---|---|
| `AuthenticationError` | 401 / 403 |
| `RateLimitError` | 429 |
| `CeridAPIError` | other non-2xx responses |

## Requirements

A running Cerid AI instance. The API is versioned at `/sdk/v1` and pinned by
contract tests against `docs/openapi-sdk-v1.json`, so this client keeps working
across internal refactors of the underlying `/agent/` routes.

## Compatibility

Independently versioned from the Cerid AI product: an `0.x` SDK is expected to
talk to a `1.x` server. The `/sdk/v1` contract is what binds them.

## License

Apache-2.0. The Cerid AI product itself is FSL-1.1-ALv2 (source-available,
converting to Apache-2.0 two years after each release); the SDKs are Apache-2.0
so integrating with Cerid carries no copyleft or source-availability obligation.
