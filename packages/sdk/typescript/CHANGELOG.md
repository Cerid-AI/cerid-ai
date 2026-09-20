# Changelog — `@cerid-ai/sdk`

Versioned independently of the Cerid AI product. `SDK_PROTOCOL_VERSION`
tracks the `/sdk/v1/` wire contract; the package version tracks this
client's release cadence.

## [0.2.0] — 2026-09-03 — protocol 1.2.0

A correctness release. Everything below was reachable from the documented
quickstart, so the practical advice is to upgrade rather than pin.

### Breaking

- **`IngestFileRequest.categorize_mode` removed.** The route reads `file_path`,
  `domain` and `tags`; the field was dropped on arrival.
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

- `retryAfter` on `RateLimitError` and `ServiceUnavailableError`, parsed from
  the server's `Retry-After` header (the Python client has always exposed it).
- `mode` and `nli_skipped` on `HallucinationResponse` — `nli_skipped` says
  whether to render a hedged or an authoritative verdict.
- `exclude_packs` on `SearchRequest` — personal-first retrieval.
- `SDK_PROTOCOL_VERSION` and `ProtocolVersionError`: `system.health()`,
  `healthDetailed()` and `settings()` now reject a server on a different
  wire-protocol major version instead of letting payload skew through.
- `engines.node` (>=18.17). The package is ESM-only and uses global `fetch`.
