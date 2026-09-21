# Changelog — `cerid-sdk`

Versioned independently of the Cerid AI product. `SDK_PROTOCOL_VERSION`
tracks the `/sdk/v1/` wire contract; the package version tracks this
client's release cadence.

## [0.2.0] — 2026-09-03 — protocol 1.2.0

A correctness release. Everything below was reachable from the documented
quickstart, so the practical advice is to upgrade rather than pin.

### Breaking

- **`verify.check()` no longer accepts `context` or `claims`.** The server's
  request model has neither field, so pydantic dropped both silently. A caller
  passing `claims=[...]` believed it was skipping claim extraction; it was not.
- **`kb.query()` no longer accepts `conversation_id`.** Not a field on the
  query request either — queries were never correlated per conversation.
- **`conversation_id` is now required** on `verify.check()` and
  `memory.extract()`. The server requires it (`min_length=1`), so the previous
  optional parameter made the documented default call a guaranteed 422.
- **`memory.extract()` returns a union.** On a server with the extraction queue
  enabled the call returns `MemoryExtractAcceptedResponse` (HTTP 202) instead of
  a result; branch on the type and poll `memory.get_job(job_id)`. Previously the
  202 envelope was parsed as a finished extraction that had stored 0 memories.

### Fixed

- `verify.check()` raised `ValidationError` on every real thorough-mode
  response: `summary` was typed `Dict[str, int]`, but the server puts the float
  `overall_confidence` in the same mapping.
- The README quickstart did not run: `client_id` is required, query results are
  dicts, `verify.check` takes `response` positionally, and `memory.recall()`
  does not exist. Every snippet in the README and in `docs/SDK_GUIDE.md` is now
  executed by the test suite.
- `pytest tests/` works from a checkout without an editable install.

### Added

- `context_sources` on `kb.query()` — e.g. `{"kb": True, "memory": True,
  "external": False}` keeps open-web rows out. It is the only request field
  that gates a whole retrieval surface; `strict_domains` narrows the KB's own
  domain bleed and never touches the web. The server has always read it; no
  SDK exposed it, so consumers reached for fields that do nothing here.
- `categorize_mode` on `kb.ingest_file()` is read by the route again (an
  earlier draft of this release said it was removed while the server ignored
  it; the route now honours it).
- `mode` and `nli_skipped` on `HallucinationResponse` — `nli_skipped` is the
  server's signal for whether to render a hedged or an authoritative verdict.
- `exclude_packs` on `kb.search()` — personal-first retrieval, previously
  unreachable from the client.
- `ProtocolVersionError`, raised by `system.health()`, `health_detailed()` and
  `settings()` when the server reports a different wire-protocol major version.
  The README had promised this check for two releases; it now exists.
