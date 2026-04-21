# Preservation Harness

> **What:** Automated gates asserting the 8 capability invariants that MUST survive every consolidation sprint.
> **Why:** Prevent the "we broke chat while cleaning up" class of regression during the v0.84.1 → v0.90.0 program.
> **Where:** [`src/mcp/tests/integration/test_preservation_*.py`](../src/mcp/tests/integration/)
> **When:** Every sprint's merge gate + on every PR via the `preservation` CI job.
> **Program context:** [`tasks/2026-04-19-consolidation-program.md`](../tasks/2026-04-19-consolidation-program.md)

---

## The 8 invariants

| # | Invariant | Guards against | File |
|---|-----------|----------------|------|
| I1 | `/health` returns healthy with orphans=0, NLI loaded, full envelope | Observability contract regression | `test_preservation_i1_health.py` |
| I2 | `/agent/query` returns QueryEnvelope (results + sources + 3-lane breakdown) | Source attribution loss | `test_preservation_i2_query.py` |
| I3 | Verification round-trip persists a provenanced report | Silent orphan reports (the P1.4 class) | `test_preservation_i3_verification.py` |
| I4 | Private Mode levels 0-3 round-trip + reject out-of-range | Validator narrowing, stale-doc 4-level myth | `test_preservation_i4_private_mode.py` |
| I5 | All 10 core `/agent/*` routes mounted, no 5xx on minimum body | Dropped `include_router()` during moves | `test_preservation_i5_agents.py` |
| I6 | `/sdk/v1/*` response shapes stable | Breaking SDK consumers (trading-agent, finance, boardroom) | `test_preservation_i6_sdk.py` |
| I7 | KB ingest → fetch → listing → delete round-trip | Indexer wiring regression | `test_preservation_i7_kb.py` |
| I8 | `/user-state/conversations` CRUD + bulk + validation | Sync-dir persistence loss | `test_preservation_i8_conversations.py` |

Total: **33 individual test cases** across 8 invariant files.

---

## Running locally

```bash
# Start the stack (if not already running)
./scripts/start-cerid.sh

# Run the full harness (inside the MCP container so neo4j DNS resolves)
make preservation-check

# Or run a single invariant:
docker exec ai-companion-mcp python -m pytest \
  tests/integration/test_preservation_i3_verification.py -v

# Or from a host venv (auto-detects NEO4J_URI→localhost):
NEO4J_PASSWORD=$(grep NEO4J_PASSWORD .env | cut -d= -f2) \
  python -m pytest src/mcp/tests/integration/ -m preservation -v
```

**Runtime:** ~60s for the full 33-test suite against a warm stack.

---

## CI

The `preservation` job in `.github/workflows/ci.yml` runs on every PR and on every push to `main`. It:

1. Boots the full Cerid stack via `docker compose up -d --wait`
2. Waits up to 60s for `/health` to report healthy
3. Runs the m0002 orphan cleanup migration (clean baseline)
4. Executes `pytest tests/integration/ -m preservation`
5. On failure, dumps the last 500 lines of every container's log
6. Always tears down the stack

The job is initially `continue-on-error: true` — surfaces as a warning but does not block merges — until it's been green for two consecutive runs on main. Flip to blocking by removing that line in ci.yml.

---

## Design principles (for contributors adding gates)

1. **Skip, don't fail, when the stack is unreachable.** The `stack_reachable` fixture in `conftest.py` handles this. Don't fail a dev-laptop pytest run just because docker isn't up.
2. **Unique `X-Client-ID` per test.** The `client_id` fixture generates one per test. Rate-limit buckets are per-client; sharing an id between tests poisons them. This is the exact bug that made smoke Test E poison Test I before v0.84.1.
3. **Every mutation cleans up.** Use the `cleanup_ids` fixture to register `(kind, id)` tuples for conversation / artifact teardown. Preservation tests create real data; leaks compound.
4. **No mocking.** These are end-to-end smoke checks. Unit tests belong in the parent `tests/` directory.
5. **Round-trip when creating writes that would orphan.** Any test that calls `/agent/hallucination` MUST also call `/verification/save` so no stub `:VerificationReport` is left behind. The session-level `_sweep_orphan_verification_reports` fixture is a safety net, not an excuse.
6. **Guard shapes, not implementations.** Tests assert the response *contract* — field names, types, value ranges — not specific values that depend on KB contents.

---

## Recovery playbook — when a preservation test fails

### I1 `verification_report_orphans != 0`

A :VerificationReport was saved without provenance. Either:
- **The writer regressed** (Sprint B/C target): check `app/db/neo4j/artifacts.py::save_verification_report` for the `source_artifact_id` flat-shape handling (see commit `9725835`).
- **A test leaked a stub** and the session sweep hasn't run yet. Re-run; if it persists, grep new test files for `/agent/hallucination` calls not paired with `/verification/save`.

Manual cleanup:
```cypher
MATCH (r:VerificationReport)
WHERE NOT (r)-[:VERIFIED|EXTRACTED_FROM]->()
  AND (r.source_urls IS NULL OR size(r.source_urls) = 0)
  AND (r.verification_methods IS NULL OR size(r.verification_methods) = 0)
DETACH DELETE r
```

### I2 `source_breakdown sum != results length`

The QueryEnvelope writer drifted. Check `core/agents/query_agent.py` — the Task-0 single-writer was the v0.84.0 fix; regression means some path returned results without updating source_breakdown.

### I3 `report_node_found: True, provenance: False`

The Sprint C bug has returned. The writer was called without any provenance channel populated. Inspect the claim dict shape on the failing call — likely a new producer that doesn't emit the canonical Claim model (see Sprint B).

### I4 `POST level=4 returned 200`

The Pydantic validator on `PrivateModeRequest.level` was widened. Check `app/routers/settings.py:410-411` — the constraint is `ge=0, le=3`.

### I5 `/openapi.json missing /agent/{name}`

A router was unregistered. Check `app/main.py` for the `include_router(agents.router)` line; check `app/routers/agents.py` for the decorator on the relevant handler.

### I6 `version!=semver` or missing SDK field

The `/sdk/v1/health` response drifted. Check `app/routers/sdk.py::sdk_health` — the response wraps `health_check()` and adds `version`, `features`, `internal_llm`. Add back whichever field was dropped.

### I7 `artifact not in /artifacts listing`

The ingestion → index → listing wiring broke. Most likely the new artifact isn't committed before `/artifacts` reads it — check `ingestion.py::ingest_content` for a missing flush/commit step. Or `artifacts.py` query filter broke.

### I8 `conversation round-trip failed`

Sync directory write failure. Most common cause: `CERID_SYNC_DIR` not mounted or permissions changed. Confirm `ls $CERID_SYNC_DIR` succeeds from inside the MCP container.

---

## Extending the harness

When a new capability ships, add a preservation test for it:

1. Create `src/mcp/tests/integration/test_preservation_iN_name.py` using the existing files as templates.
2. Use the `http_client`, `cleanup_ids`, `neo4j_driver` fixtures.
3. Update the table at the top of this doc.
4. Add the invariant to the charter in `tasks/2026-04-19-consolidation-program.md`.

New invariants become gates for every subsequent sprint.

---

## Known limitations

- **CI job requires `OPENROUTER_API_KEY` secret** for the verification invariants (I3, I6). Preservation tests that need LLM calls skip cleanly when the key is absent.
- **Sync invariant I8 skips** when `CERID_SYNC_DIR` is not configured. Fine for most CI envs; production must run with it.
- **No FE preservation tests yet.** The 8 invariants cover the backend surface. Sprint A captures the FE contract via existing vitest suite (744 tests); a future program may add FE-level preservation gates.
