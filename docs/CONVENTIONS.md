# Cerid AI — Conventions

> **Last refresh:** 2026-04-19 (Sprint H — extracted from CLAUDE.md, pruned duplicates)
> **Scope:** Project-specific style/approach conventions not enforced by lint rules
> **Owner:** New contributors read this first; senior maintainers amend as patterns solidify

Conventions that ARE enforceable by tools live in `.ruff.toml`, `pyproject.toml`, `.github/workflows/ci.yml` (see the drift-gate jobs), `src/mcp/.importlinter`, and the preservation harness. This doc is for the remainder — taste/approach rules you can't spell out as a lint rule.

## Process

- **Never add AI attribution** to commits, PRs, comments, or docs. Commits are authored by the human developer. No `Co-Authored-By: Claude` / `Anthropic` / etc. lines. (Enforced by dotfiles CLAUDE.md; repeated here because it's the most commonly-missed global rule.)
- **Session start:** Run `./scripts/validate-env.sh --quick` at the beginning of every development session.
- **New contributor first steps:** Read this file, then [`docs/ARCHITECTURE.md`](ARCHITECTURE.md), then [`docs/PRESERVATION.md`](PRESERVATION.md). Skip `CLAUDE.md` unless you are an LLM agent — it is written for that audience.

## Architecture

- **Layer boundary is absolute:** `core/` must never import from `app/`, `routers/`, `services/`, etc. Violations fail CI via `import-linter`.
- **Canonical import paths only:** `from core.utils.X import ...` and `from app.routers.X import ...`. The old bridge paths (`from utils.X`, `from agents.X`) no longer exist after Sprint E.
- **New routers live in `app/routers/`** — not `src/mcp/routers/`, which is reserved for `billing.py` (internal-strip target).
- **New agents live in `core/agents/`** if they're portable algorithm logic; in `app/agents/` if they're orchestration wrappers.

## Data & storage

- **ChromaDB metadata is strings/ints only.** Lists stored as JSON strings. Lists-of-lists violate `validate_embeddings`.
- **ChromaDB embeddings return `list[np.ndarray]`** (individual slices, not `.tolist()`) for 0.5.x compatibility.
- **Neo4j Cypher:** use explicit `RETURN` clauses, not map projections (breaks with Python string ops).
- **Deduplication:** SHA-256 of parsed text, atomic via Neo4j `UNIQUE CONSTRAINT` on `content_hash`.
- **Batch ChromaDB writes:** single `collection.add()` call per ingest, not per-chunk.
- **Neo4j auth validation:** `deps.py::get_neo4j()` runs `RETURN 1` (not just `verify_connectivity()`) — empty `NEO4J_PASSWORD` raises `RuntimeError` at startup.
- **Keywords metadata uses `keywords_json`** (JSON-encoded string) consistently across ingest paths. A 2026-03 inconsistency between `keywords` and `keywords_json` caused silent data loss; this name is now canonical.

## Event loop

- **CPU-bound ops offload to `asyncio.to_thread()`:** ChromaDB queries, ONNX embedding, BM25 tokenization, cross-encoder reranking.
- **`/agent/query` is gated by partitioned concurrency pools** (KB/CHAT/HEALTH) — not a process-wide semaphore. `/health` polling never serializes behind chat turns.
- **Frontend auto-inject KB queries** use `AbortController` + 500ms timeout to free browser connection slots before the chat stream fetch fires.

## Verification pipeline

- **One canonical claim shape:** `core.agents.hallucination.models.ClaimVerification`. Every producer emits it; every consumer reads `.artifact_ids()` and `.has_provenance()`. Adapter at the boundary handles legacy dict shapes. See `tests/test_canonical_claim_model.py` for contract.
- **`/agent/hallucination` auto-persists** by default (`persist=True`). Single call produces a fully provenanced `:VerificationReport`. External SDK consumers can opt out with `persist=False`.
- **Three provenance channels:** a saved `:VerificationReport` must carry ONE of:
  1. `[:VERIFIED]`/`[:EXTRACTED_FROM]` edges to `:Artifact` nodes (kb_nli path)
  2. `source_urls` array (web_search path)
  3. `verification_methods` array (cross_model / any path)
  The m0002 migration deletes nodes with all three empty.

## LLM call sites

- **Every `call_internal_llm(...)` takes a `stage=...` breadcrumb** for observability. Stage flows into structlog + Sentry scope. Contract test `tests/test_llm_call_site_contract.py` enforces kwarg validity across all call sites.
- **Model IDs route via the canonical `core.routing.smart_router`** — no bridge paths.
- **Free-tier models have aggressive rate limits** (~8 RPM). Use 8+ second base delay between calls; single 60s retry on 429 instead of exponential backoff (exponential burns quota faster).

## Circuit breakers

- All LLM call sites use breaker names registered in `circuit_breaker.py`.
- Register a new breaker when you add a new call site category.
- **Resetting openrouter-dependent breakers happens together:** `_openrouter_auth_probe_loop()` in `main.py` resets all seven at once on a successful auth probe. Adding a new breaker? Add it to the reset list.

## Rate limiting

- In-memory sliding window, keyed on `X-Client-ID` header (read directly from headers, not `request.state` — middleware ordering independence).
- Per-client limits in `config/settings.py::CLIENT_RATE_LIMITS`. GUI 20/min, trading-agent 80/min, unknown 10/min.
- Test harnesses generate fresh `X-Client-ID=smoke-<tag>-<uuid>` per test to prevent rate-limit collisions.

## Secrets & config

- Single `.env` at repo root, encrypted as `.env.age` via `age`. Key at `~/.config/cerid/age-key.txt`.
- `.env`, `.env.age`, `.env.local` never committed — enforced by `_SYNC_SKIP_BASENAMES` in `sync-repos.py` and `.gitignore`.
- **JWT startup validation:** `CERID_MULTI_USER=true` with missing `CERID_JWT_SECRET` raises `RuntimeError` at startup (not just a warning).
- **Docker env var pattern:** `src/mcp/docker-compose.yml` uses `env_file: ../../.env`. Don't add `${VAR}` interpolation in `environment:` for passthrough vars — empty-env interpolation overrides `env_file` entries.

## Observability defaults

- **Silent failures are observable:** every broad `except Exception:` in hot paths uses `log_swallowed_error(module, exc)` from `core.utils.swallowed`. Surfaces in `/health.invariants.swallowed_errors_last_hour`.
- **Request tracing:** `X-Request-ID` header propagates through every log line via the contextvar filter in `core.utils.request_id`.
- **All observability signals converge at `/health.invariants`** — no scattered secondary health endpoints.

## Docker

- Use `127.0.0.1` not `localhost` in Alpine healthchecks — Alpine resolves `localhost` to `::1` (IPv6), many services bind `0.0.0.0` (IPv4) only.
- Always verify Docker build success — `docker compose build` can return 0 with a cached fallback when the real build exits code 2. Grep the `--progress=plain` output for `error`.

## Frontend

- **Tailwind v4 via `@tailwindcss/vite` plugin** — no `tailwind.config.ts`.
- **shadcn/ui New York style, Zinc base color**, path alias `@/*` → `./src/*`.
- `crypto.randomUUID()` requires a secure context — on LAN-over-HTTP it's undefined. Use the shared `uuid()` helper in `src/web/src/lib/utils.ts` everywhere instead.
- **`.d.ts` basename must not collide with a `.ts` basename** in the same dir — TypeScript treats the `.d.ts` as a specific module declaration and ignores ambient declarations.

## Plugins & workflows

- Plugins carry a `manifest.json` (name, version, tier, description, entry). BSL-1.1, converts to Apache-2.0 after 3 years.
- Tier gating enforced at load time via `CERID_TIER`.
- Workflow engine uses Kahn's algorithm for DAG validation — cycles rejected.

## Cross-repo sync

- See [`docs/SYNC_PROTOCOL.md`](SYNC_PROTOCOL.md). In short: never `cp`/`rsync` between repos; use `scripts/sync-repos.py`; `validate` before and after every sync.

## When to retire a convention from this file

A convention moves OUT of this file when one of these happens:

1. **A lint rule catches it.** (Example: silent-catch is caught by `scripts/lint-no-silent-catch.py`.) Update the rule; delete the convention.
2. **A contract test enforces it.** (Example: claim shape is guarded by `tests/test_canonical_claim_model.py`.) Keep a one-line reference; delete the detail.
3. **It's specific to one file.** Move to an inline comment; delete here.

CONVENTIONS.md grows by one line per new pattern but should shrink over time as patterns graduate to code-enforced contracts.
