# Changelog — `@cerid-ai/sdk`

Versioned independently of the Cerid AI product. `SDK_PROTOCOL_VERSION`
tracks the `/sdk/v1/` wire contract; the package version tracks this
client's release cadence.

## [0.3.0] — Unreleased — protocol 1.4.0

### Added

- Forgetting with a confirmation step: `kb.forgetPreview(subjects, { mode })` and `kb.forgetExecute(confirmToken)`.
  A preview names what would be removed (documents, passages) and returns a
  single-use `confirm_token` bound to exactly that set and mode, valid 15
  minutes. Execute runs only what the token covers, once, for the consumer
  it was issued to; a spent, expired or foreign token is a 409. A consumer
  limited to some domains may forget only documents and passages in the
  domains it may write. Types: `ForgetPreviewResponse` and `ForgetExecuteResponse`.

### Changed

- `SDK_PROTOCOL_VERSION` is `1.4.0` (two new endpoints, same major version).

## [0.2.2] — Unreleased — protocol 1.3.0

### Added

- Every `CeridSDKError` carries `errorCode`: the server's machine-readable
  name for the refusal (for example `CROSS_SITE_REQUEST_REFUSED` from the
  origin guard), read from the response body's `error_code` field. It is
  `null` when the body has none. The message also carries it, as
  `<detail> (<error_code>)`, so a logged error names its cause.

## [0.2.1] — Unreleased — protocol 1.3.0

### Changed

- `SDK_PROTOCOL_VERSION` is `"1.3.0"`. The server's hallucination `summary`
  carries an integer `agreed`: claims a second model agreed with and no source
  backs. `summary.verified` now counts only claims a source backs, so it can be
  lower than on a 1.2.0 server for the same response. A claim's own `status`
  is unchanged. `summary.agreed` is `undefined` on a report stored before the
  server sent it; do not read that as zero.
- The `HallucinationResponse.summary` doc comment names `agreed`. The type is
  unchanged (`Record<string, number>`).
- Same major version, so a 0.2.0 client against a 1.3.0 server passes the
  `ProtocolVersionError` check, and a 0.2.1 client against a 1.2.0 server
  does too.

## [0.2.0] — 2026-09-03 — protocol 1.2.0

A correctness release. Everything below was reachable from the documented
quickstart, so the practical advice is to upgrade rather than pin.

### Breaking

- **`memory.extract()` returns a union** of `MemoryExtractResponse` and the new
  `MemoryExtractAcceptedResponse`. On a server with the extraction queue
  enabled the call returns the 202 envelope; narrow with
  `isMemoryExtractAccepted()` and poll `memory.getJob(job_id)`. Previously the
  envelope was typed as a finished extraction that had stored 0 memories.
- **`HallucinationResponse.summary` is `Record<string, number>`**, not a fixed
  set of four counts — the server includes the float `overall_confidence` in
  the same object.

### Fixed

- The README quickstart called `cerid.query()`, which does not exist, and
  documented `CeridAPIError`, which the package never exported. Both READMEs'
  snippets are now compiled by `npm run typecheck` and executed by the tests.
- `exports` lists `types` before `import`, so strict resolvers find the type
  declarations instead of resolving the package untyped.

### Added

- `context_sources` on `QueryRequest` — e.g. `{ kb: true, memory: true,
  external: false }` keeps open-web rows out. It is the only request field that
  gates a whole retrieval surface: `strict_domains` narrows the KB's own domain
  bleed and never touches the web, and `source_config` only tunes weights under
  `rag_mode: "custom_smart"`. The server has always read it; `QueryRequest`
  exposed only `source_config`, so consumers set the field that does nothing.
- `categorize_mode` on `IngestFileRequest` — the route reads it.
- `retryAfter` on `RateLimitError` and `ServiceUnavailableError`, parsed from
  the server's `Retry-After` header (the Python client has always exposed it).
- `mode` and `nli_skipped` on `HallucinationResponse` — `nli_skipped` says
  whether to render a hedged or an authoritative verdict.
- `exclude_packs` on `SearchRequest` — personal-first retrieval.
- `SDK_PROTOCOL_VERSION` and `ProtocolVersionError`: `system.health()`,
  `healthDetailed()` and `settings()` now reject a server on a different
  wire-protocol major version instead of letting payload skew through.
- `engines.node` (>=18.17). The package is ESM-only and uses global `fetch`.
