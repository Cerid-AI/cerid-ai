# Cerid AI — Conventions

> **Last refresh:** 2026-05-15 (v0.95.3: 5 lessons graduated into CONVENTIONS entries below — "Never raise a timeout to fix slow code", "Default to most restrictive security setting", "Event-loop-bound singletons need owner-thread guards", "Middleware reads from immutable request data", "Patch the bridge module, not the source". Plus 5 lint scripts in `scripts/` enforcing the syntactic lessons.)
> **Scope:** Project-specific style/approach conventions not enforced by lint rules
> **Owner:** New contributors read this first; senior maintainers amend as patterns solidify

Conventions that ARE enforceable by tools live in `.ruff.toml`, `pyproject.toml`, `.github/workflows/ci.yml` (see the drift-gate jobs), `src/mcp/.importlinter`, and the preservation harness. This doc is for the remainder — taste/approach rules you can't spell out as a lint rule.

## Process

- **Never add AI attribution** to commits, PRs, comments, or docs. Commits are authored by the human developer. No `Co-Authored-By: Claude` / `Anthropic` / etc. lines. (Enforced by dotfiles CLAUDE.md; repeated here because it's the most commonly-missed global rule.)
- **Session start:** Run `./scripts/validate-env.sh --quick` at the beginning of every development session.
- **New contributor first steps:** Read this file, then [`docs/ARCHITECTURE.md`](ARCHITECTURE.md). Skip `CLAUDE.md` unless you are an LLM agent — it is written for that audience.
- **Run ruff from repo root.** `cd <repo-root> && ruff check src/mcp/` is the canonical invocation — matches what CI does. Running `cd src/mcp && ruff check .` resolves config from `src/mcp/pyproject.toml` (if present) instead of the root, producing a different rule set.
- **Run `make prepush` before every push** (graduated 2026-06-06). It chains `ci-local` (ruff/mypy/import/pytest/frontend/guard) + `drift-check` (the full remote `lint`-job gate set `ci-local` omits: env-example / router-registry / sdk-openapi drift, silent-catch, no-legacy-neo4j, import-star, module-getenv, docker-healthcheck, web-crypto, dts-collision, product-story, mcp-descriptions). `ci-local` ALONE is not enough — it misses the drift + silent-catch gates that then fail at remote-CI time.
- **`curl the running service before coding field access.**` Production response shapes drift from docs/code-reading (a promotion filter once silently skipped every claim because it checked `verdict`/`confidence`/`type` while prod dicts used `status`/`similarity`/no-type). Use `.get()` with fallback chains for variant field names; never assume a shape from docs alone.
- **LangGraph `StateGraph(dict)` type-ignores are per-call.** `# type: ignore[type-var]` on the `StateGraph(dict)` line suppresses construction but the bad type propagates — add the ignore to each `graph.add_node()` call individually. TypedDict is impractical when nodes use dynamic state keys.
- **`lint-no-silent-catch` shape constraints.** The linter flags any `except ...: logger.debug(...)` body regardless of exception type — narrowed catches (`except TimeoutError`) are flagged identically to broad swallows. Resolution depends on intent: expected fallback → `logger.info`; tolerated failure → `log_swallowed_error`. Tuple-form catches (`except (A, B): pass`) are NOT flagged (linter only matches `ast.Name(id="Exception")`). Review burden sits with humans.
- **Pin CI tool versions for local verification.** CI pins `ruff==0.15.4`, `pip-tools==7.5.3`, `import-linter` (unpinned but version-stable). Local `pip install ruff` may pull a newer release that flags different rules. Either match the CI version with `pip install ruff==0.15.4` or run the verification inside the Docker container (`docker run --rm -v "$(pwd):/work" -w /work python:3.12-slim bash -c "pip install ruff==0.15.4 && ruff check src/mcp/"`). The lock-sync regen is covered by `scripts/regen-lock.sh`; ruff/mypy match by hand. See `.github/workflows/ci.yml` for every tool's pinned version.

## Architecture

- **Layer boundary is absolute:** `core/` must never import from `app/`, `routers/`, `services/`, etc. Violations fail CI via `import-linter`.
- **Canonical import paths only:** `from core.utils.X import ...`, `from app.routers.X import ...`, `from app.db.neo4j.X import ...`. The old bridge paths (`from utils.X`, `from agents.X`, `from db.neo4j.X`) no longer exist — Sprint E retired `utils/` + `agents/`; the 2026-04-21 Neo4j unification retired the `db.neo4j` shim tree. `lint / no-legacy-neo4j-tree` CI job enforces the last one.
- **New routers live in `app/routers/`** — not `src/mcp/routers/`, which is reserved for `billing.py` (internal-strip target).
- **New agents live in `core/agents/`** if they're portable algorithm logic; in `app/agents/` if they're orchestration wrappers.
- **core↛app callbacks use the DI-sink pattern** (graduated 2026-06-06). Register `set_X_sink(fn)` in `app/main.py`'s lifespan; read `get_X_sink()` in core; pass only primitives across the boundary (no app types in core). In use: data-source registry, contradiction sink, source-ingest sink. Test end-to-end by wiring the sink in-process — a fresh `docker exec python` never runs app startup, so the sink reads `None` there.
- **Bulk import-path rewrites must hit all five forms** (graduated 2026-06-06). Retiring a bridge module + rewriting consumers: cover dotted (`from pkg.X import`), submodule (`from pkg import X`), bare (`import pkg.X`), `sys.modules["pkg.X"]` stub keys, and `@patch("pkg.X...")` strings in ONE pass. Pre-flight grep targets for the replacement prefix to avoid `core.core.X` double-prefixes; run the FULL pytest suite after (preservation is end-to-end and misses test-internal patch-target drift).

## Data & storage

- **ChromaDB metadata is strings/ints only.** Lists stored as JSON strings. Lists-of-lists violate `validate_embeddings`.
- **ChromaDB embeddings return `list[np.ndarray]`** (individual slices, not `.tolist()`) for 0.5.x compatibility.
- **Neo4j Cypher:** use explicit `RETURN` clauses, not map projections (breaks with Python string ops).
- **Deduplication:** SHA-256 of parsed text, atomic via Neo4j `UNIQUE CONSTRAINT` on `content_hash`.
- **Batch ChromaDB writes:** single `collection.add()` call per ingest, not per-chunk.
- **Neo4j auth validation:** `deps.py::get_neo4j()` runs `RETURN 1` (not just `verify_connectivity()`) — empty `NEO4J_PASSWORD` raises `RuntimeError` at startup.
- **Keywords metadata uses `keywords_json`** (JSON-encoded string) consistently across ingest paths. A 2026-03 inconsistency between `keywords` and `keywords_json` caused silent data loss; this name is now canonical.
- **Artifact `tags` come back from `list_artifacts`/`get_artifact` as a raw JSON STRING** (graduated 2026-06-06), not a parsed structure. Object-keyed readers MUST parse first — use `core.utils.artifact_tags.parse_tag_object` (handles str/dict/list, never raises). `tags.get(...)` on the raw string is an `AttributeError` (the digests 500 / daily-digest-inbox bug class).
- **Canonical models behind `from_legacy_dict()`: strict on structure, default-empty on text** (graduated 2026-06-06). A required `claim: str` on `ClaimVerification` made legacy dicts with provenance-but-no-text fail Pydantic, and the save path silently dropped them. Be strict on enums/ranges/identifiers; default-empty on text content (missing text isn't a reason to drop provenance); fixture-test every optional-field combination including "only provenance, no text."

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
- **One breaker per workload — never share across workloads with different latency/failure profiles** (graduated 2026-06-06). A single shared `quenchforge` breaker let slow-chat 502s lock out the healthy embed/rerank slots. Register per-workload breakers (`quenchforge-chat`/`-embed`/`-rerank`) explicitly; `get_breaker(name)` auto-creates unknown names with generic thresholds, so unregistered = accidental tuning. Local-GPU slots want transient-tolerant tuning (higher `failure_threshold`, short `recovery_timeout`), like the datasource breakers.
- **Retry INSIDE `breaker.call`, not the reverse** (graduated 2026-06-06). A retry loop wrapping `breaker.call` counts each retry as a separate breaker failure, so one transiently-failing request opens the circuit by itself. Wrap the whole retry sequence in one `breaker.call` (one logical request = at most one breaker outcome), mirroring `quenchforge_client`'s embed/rerank path. Diagnostic: `/health.inference_routing` says degraded but a direct backend probe returns 200 → suspect a stuck/over-eager breaker, not the backend.

## Performance (graduated from `tasks/lessons.md` 2026-05-15)

- **Never raise a timeout to fix slow code.** If something is slow, profile and find the bottleneck — increasing timeouts is a band-aid that masks the real problem. The 2026-04-06 verification-pipeline slowness was "fixed" by raising the timeout three times (10→20→60 s) before the actual cause was found (heuristic-first claim extraction reversed to LLM-first). Reversing the order produced <1.2 s first-event latency — the timeout never needed to grow.

## Security (graduated from `tasks/lessons.md` 2026-05-15)

- **Default to the most restrictive setting; let users opt in to openness.** CORS origins default to `localhost`, ports bind to `127.0.0.1`, sync directories default off, etc. Provide env vars for users who need broader access — never the reverse. Restrictive defaults + opt-in openness is safer than permissive defaults + opt-out hardening; users who need LAN access will set the env var, users who don't will never know they were protected.
- **Reuse `utils/encryption.py` (Fernet) — don't add new crypto.** New features needing encryption (sync dir at-rest, etc.) reuse the same Fernet/key-management plumbing that already protects API keys. Same battle-tested patterns, no new dependencies. Reaching for a new crypto library per feature is a smell.
- **Path traversal guards: always `resolve()` + `is_relative_to()` on user-supplied paths.** Bare `os.path.join(base, user_input)` is a CVE waiting to happen. Resolve to absolute, then `is_relative_to(base)`.
- **Privacy claims drift with feature adds.** When a feature changes WHERE data flows (cloud sync, analytics, telemetry, external APIs), update marketing site + CLAUDE.md privacy claims in the *same* PR. Treat privacy claims as code that needs updating, not background prose.
- **Server-side fetch of a user-supplied URL = SSRF — route it through `core/ingest/sources/safe_fetch.guarded_get`** (graduated 2026-06-06). Allowlist http(s); `getaddrinfo` ALL records and reject if ANY is loopback/private/link-local/reserved (ipaddress `.is_*`); `follow_redirects=False` + re-validate every redirect hop (a 3xx target is attacker-controlled too). Guard EVERY fetch site (connect/fetch_since/health_check). "Internal-only" is not an exemption — internal services (`http://ai-companion-neo4j:7474`, `http://169.254.169.254/`) are prime SSRF targets.
- **Untrusted XML (RSS/Atom feeds) → reject DTDs** (graduated 2026-06-06). stdlib `xml.etree` is XXE/billion-laughs-vulnerable; refuse any document containing `<!DOCTYPE`/`<!ENTITY` (both attacks need a declaration) + cap body size. Dependency-free equivalent of defusedxml's `forbid_dtd`/`forbid_entities` for untrusted input.

## Async & event loops (graduated from `tasks/lessons.md` 2026-05-15)

- **Singletons of event-loop-bound objects need an owner-thread guard.** `httpx.AsyncClient`, `asyncio.Lock`, `asyncio.Queue`, etc. tie themselves to whichever event loop creates them. If a worker thread spins up a transient loop (e.g. `_run_coro_isolated` for sync-adapted async code in ingestion), it must NOT consume the module-level singleton. The fix pattern: gate caching on `threading.current_thread() is threading.main_thread()`; worker threads get a one-shot client (`_acquire_client()` helper); the singleton is reserved for the main loop (uvicorn-owned). Also track owner-loop identity and recycle on mismatch — pytest changes loops between tests. Regression test: `tests/test_llm_client_loop_safety.py::test_worker_thread_call_does_not_poison_singleton`.

## Middleware (graduated from `tasks/lessons.md` 2026-05-15)

- **Middleware reads from immutable request data, not from `request.state` set by other middleware.** Middleware executes LIFO; the rate-limiter expecting an upstream-set `request.state.client_id` ran *before* the request-ID middleware that supposedly set it. Result: every client got default limits. Read identifying values from immutable sources (headers, URL, source IP) instead — order-independent and correct regardless of registration order.

## Testing (graduated from `tasks/lessons.md` 2026-05-15)

- **`@patch` targets the bridge module, not the source.** After the Phase C `agents/` / `utils/` retire-and-bridge migration, `from agents.foo import bar` in `tools.py` looks up `bar` in the `agents.foo` bridge module at runtime — even though the implementation lives at `core.agents.foo.bar`. `@patch("core.agents.foo.bar")` patches the source; runtime call still sees the original. Patch the **call-site lookup module** (`agents.foo.bar`) instead. The migration touched 547 patch targets across 34 test files.

### Mock hygiene (graduated from `tasks/lessons.md` 2026-05-19)

- **Same-module function calls skip `patch.object` when looked up by name.** Python resolves bare-name calls via the defining module's globals; an external `patch("pkg.submodule.fn")` doesn't reach a call site that lives in the same module as the function it calls. If the test needs to patch such a function, route the calling site through the package facade explicitly (`import pkg as _pkg; return _pkg.fn(...)`) so the patch can hook the lookup.
- **`@patch` on stub modules needs `create=True`.** When `conftest.py` stubs heavy optional deps as empty `ModuleType` objects (`sys.modules["pandas"] = ModuleType("pandas")`), `@patch("pandas.read_csv")` errors because the stub has no attribute. `@patch("pandas.read_csv", create=True)` lets the patcher add the attribute on the stub.
- **`side_effect` for mocks that return mutable containers.** `mock.return_value = [item]` returns the SAME list reference on every call — if the code under test does `results.extend(cross_results)` with both pointing at that one list, the list grows unexpectedly. Use `mock.side_effect = [[item], []]` to return fresh lists per call.
- **`sys.modules` stub pollution leaks across test files.** pytest collects all test modules before running them; if `test_a.py` injects `sys.modules["agents.foo"] = stub`, then `test_b.py`'s real import of `agents.foo` sees the cached stub instead. Prefer `unittest.mock.patch` over manual `sys.modules` manipulation. If a stub must be set, guard the import in the consumer test (`if not hasattr(cached, "expected_attr"): del sys.modules[...]`) so the real module loads.
- **MagicMock for async-iterator I/O calls needs a terminator.** `fake_redis.xread.return_value = None` makes every call return None synchronously and instantly — a streaming endpoint's `while True: xread(block=5000)` loop becomes a tight CPU spin that doesn't yield to `TestClient.stream()`'s close, and the test hangs to CI timeout. Use `fake_redis.xread.side_effect = [None, asyncio.CancelledError()]` so the second call raises and the `except asyncio.CancelledError: return` branch exits cleanly.
- **MagicMock substitutes for entire modules break numeric attribute use.** `core.agents.query_agent.config = MagicMock()` makes `config.AGENT_QUERY_BUDGET_SECONDS` another MagicMock, which `asyncio.wait_for(..., timeout=config.AGENT_QUERY_BUDGET_SECONDS)` chokes on (`max(0, MagicMock())` → TypeError). Patch the specific attributes with real numeric values when the code does arithmetic / comparisons on them.
- **Audit test assertions after standardizing error message helpers.** `extractError()` and similar normalizers parse server response bodies and surface the actual error string. Tests that asserted on a hard-coded fallback message break. After standardizing an error-handling helper, grep `tests/` for the prior fallback message and update assertions.
- **`python -u` for backgrounded test runs.** Python defaults to block buffering when stdout is redirected to a pipe; a pytest run that produces thousands of lines appears to hang because the log file holds only the first few lines until the buffer flushes. Always pass `-u` (unbuffered) for `python -m pytest ... > /tmp/log.txt &` invocations. Or pipe through `tee`. The "is it stuck or just buffering" debugging cost is one full cycle per run otherwise.

## Streaming vs non-streaming paths (graduated from `tasks/lessons.md` 2026-05-19)

When two code paths produce the same output shape (e.g. streaming + non-streaming verification, agent query response in two endpoints), wire **every** feature addition into both — or refactor to one shared implementation. Silent drift between streaming and non-streaming is the rule, not the exception. Concrete incidents this rule covers:

- 2026-04-13: `verify_response_streaming()` had verified-memory promotion, `check_hallucinations()` (non-streaming) did not.
- 2026-04-15: `verify_response_streaming()` threaded `response_context`/`claim_context` via `_extract_claim_context()`, `check_hallucinations()` called `verify_claim()` with neither — claims via `/agent/hallucination` validated in isolation, producing false-unverified verdicts.
- 2026-04-13: `HallucinationCheckRequest` (4 fields) vs `StreamingVerificationRequest` (8 fields, including `expert_mode`) — non-streaming handler couldn't pass `expert_mode` because the request model didn't declare it.

When adding a post-processing step, grep for all functions that produce the input data — not just the one you're looking at. Prefer a contract test that runs the same input through both paths and asserts the output shapes match.

## Caching multi-backend routers (graduated from `tasks/lessons.md` 2026-05-19)

A cache that fronts a multi-backend router (Quenchforge → sidecar → ONNX, OpenRouter → ollama → quenchforge, …) **must derive its namespace from the active backend that will actually serve the request**, not from the wrapper's static configuration. The wrapper's `model_id` says one thing; the routing logic may have flipped on a breaker, a config knob, or a fallback chain and now be serving a *different* vector space.

If the router checks `is_embeddings_provider_quenchforge()` and `is_sidecar_reachable()` to decide, the cache's namespace function must check the same predicates and pick the same branch. The 2026-05-17 v0.96.0 incident showed how a breaker fall-through silently mixed nomic vectors with Snowflake vectors in the same ChromaDB collection — retrieval collapsed because queries embedded by one model were k-NN-searched against documents embedded by a different one.

**Pattern to detect**: retrieval quality drops sharply after a configuration change (provider flip, model swap, breaker recovery) without any code change. Nearest-neighbour distances cluster bimodally — half the corpus at normal distances, half at ~sqrt(2) (orthogonal between unrelated vector spaces).

**Reference**: `core/utils/embedding_cache.py::EmbeddingCache._active_namespace` encodes the predicate at the key level so model swaps are forced into distinct keyspaces by construction.

## Persistence guards (graduated from `tasks/lessons.md` 2026-05-19)

When building a `write_result` / `save_baseline` / `persist_run` function for an evaluation harness, the naive "replace current with latest, push old to history" shape silently overwrites the canonical when an experimental run undershoots. The 2026-05-17 incident clobbered the v0.95.9 minimum-viable canonical (recall=0.432, n=468) with a `production-stack+qa` 60-item smoke run (recall=0.133) — different variant, much lower score, but the persistence layer didn't know.

**Two-arm guard** (in `tests/eval/longmemeval/persistence.py::write_result` since 2026-05-17, sample-size arm added 2026-05-18):

1. **Sample-size guard**: a new run with fewer items than the canonical can never replace it, regardless of variant. Stratified-subset runs and smoke tests are diagnostic, not baselines.
2. **Variant-aware guard**: when the new run has equal-or-larger sample size but a *different* variant and equal-or-lower recall, the canonical stays in place. Mixed-variant comparisons are noisy; the safe default is preserve.

Same-variant equal-or-larger-sample runs always replace — that's the operator's expected behaviour when re-running the same pipeline at the same or expanded scale. Promoting a smaller-sample or different-variant result requires manual JSON surgery (or a future `--promote-variant` flag).

## Re-export bridges (added 2026-05-15)

Some `__init__.py` files exist solely as back-compat shims — they
re-export everything from a canonical source package so pre-Phase-C
imports keep working. The lint job
`lint / import-star-without-all` flags `from X import *` without
`__all__` because Python silently drops `_underscore_names` in that
pattern. Documented bridges are exempt: prepend the literal phrase
**`Re-export bridge`** to the file's docstring or top-of-file comment
(must appear in the first 10 lines).

Examples in repo: `src/mcp/{config,parsers,middleware,routers}/__init__.py`.

When adding a new bridge:

```python
# Copyright …
"""Re-export bridge — back-compat shim for the new package.
…
"""
from new.package import *  # noqa: F401, F403
```

Bridges are NOT the preferred pattern — they exist for transitional
back-compat. New consumer code should import from the canonical
`from new.package import X` path instead. Track bridge removal in
the same PR that retires the last `from old.package import X` call
site.

## Rate limiting

- In-memory sliding window, keyed on `X-Client-ID` header (read directly from headers, not `request.state` — middleware ordering independence).
- Per-client limits in `config/settings.py::CLIENT_RATE_LIMITS`. GUI 20/min, trading-agent 80/min, unknown 10/min.
- Test harnesses generate fresh `X-Client-ID=smoke-<tag>-<uuid>` per test to prevent rate-limit collisions.

## Secrets & config

- Single `.env` at repo root, encrypted as `.env.age` via `age`. Key at `~/.config/cerid/age-key.txt`.
- `.env`, `.env.age`, `.env.local` never committed — enforced by `_SYNC_SKIP_BASENAMES` in `sync-repos.py` and `.gitignore`.
- **JWT startup validation:** `CERID_MULTI_USER=true` with missing `CERID_JWT_SECRET` raises `RuntimeError` at startup (not just a warning).
- **Docker env var pattern:** `src/mcp/docker-compose.yml` uses `env_file: ../../.env`. Don't add `${VAR}` interpolation in `environment:` for passthrough vars — empty-env interpolation overrides `env_file` entries.
- **When new code writes a global that an env/config var also sets, enumerate the precedence explicitly** (graduated 2026-06-06). A license `_reconcile` set `FEATURE_TIER=community` whenever Redis held no license — silently clobbering a `CERID_TIER=enterprise` env override at every bootstrap/poll. Env is the floor, dynamic state is the elevator: never fall back to an absolute (`set_tier("community")`); fall back to the configured baseline. Add a review dimension for "interaction with pre-existing config that controls the same state," and dogfood (deploy for real) — the bug lived at the seam, invisible to in-isolation review.
- **Dependabot major bumps drop transitive deps.** chromadb 1.x removed `chroma-hnswlib`; eslint 10.x broke `eslint-plugin-react-hooks`. Review upper-bound changes against breaking-change notes; keep conservative upper bounds (`<0.6`, not `<1.6`) for libs with known major-version API breaks.

## Observability defaults

- **Silent failures are observable:** every broad `except Exception:` in hot paths uses `log_swallowed_error(module, exc)` from `core.utils.swallowed`. Surfaces in `/health.invariants.swallowed_errors_last_hour`.
- **Request tracing:** `X-Request-ID` header propagates through every log line via the contextvar filter in `core.utils.request_id`.
- **All observability signals converge at `/health.invariants`** — no scattered secondary health endpoints.

## Docker

- Use `127.0.0.1` not `localhost` in Alpine healthchecks — Alpine resolves `localhost` to `::1` (IPv6), many services bind `0.0.0.0` (IPv4) only.
- Always verify Docker build success — `docker compose build` can return 0 with a cached fallback when the real build exits code 2. Grep the `--progress=plain` output for `error`.
- **Healthcheck side effects must be idempotent across restarts.** A sentinel file (`/tmp/.health_ok`) lives in the writable layer and survives `docker restart` plus the auto-restart Docker performs after a healthcheck-driven SIGTERM. Naively-gated `kill -s TERM 1` patterns produce infinite restart loops. Gate the kill so it only fires when the sentinel was created during the *current* PID 1's lifetime: `[ -n "$(find /tmp/.health_ok -newer /proc/1 2>/dev/null)" ] && kill -s TERM 1`. `find ... -newer /proc/1` returns the path only when the sentinel's mtime is strictly newer than PID 1's start.
- **`external: true` networks cross compose-project boundaries.** When a second project opts into the same `external: true` network, both projects join the SAME Docker bridge — full DNS leakage between projects. To run a sandbox alongside production, override the network in the overlay (`networks: llm-network: name: cerid-sandbox-llm-network; external: false`) plus per-service aliases for the canonical hostnames. Distinct container names + ports are NOT enough; the network is the load-bearing piece. Verify with `docker network ls` showing two distinct bridges.

## CI drift gates

- **Adding a new drift-gate CI job?** Decide whether it's deterministic or has environmental variability before picking the rollout shape:
  - **Deterministic checks** (path-existence, pure-Python AST walks, file-hash comparisons) — ship blocking from day one and add to `docker` `needs[]` immediately. No flakiness exposure that soft-gating would absorb. Example: `lint / no-legacy-neo4j-tree` (2026-04-21).
  - **Environmentally-variable checks** (anything that runs `pip-compile`, docker-compose, or a live-stack boot) — ship with `continue-on-error: true`, watch two consecutive green runs on `main`, then flip to blocking. Example: `lint / sdk-openapi-drift` (2026-04-21; four-run wait for extra confidence).
- Either shape must end up blocking in `docker` `needs[]`. Soft-warning CI gates do not exist in this repo by policy (re-check: `grep 'continue-on-error' .github/workflows/ci.yml` should match zero drift jobs).
- **`.trivyignore` must be referenced in EVERY Trivy scan step.** A multi-image CI workflow (one Trivy step per image) only applies the ignore-file to the steps that explicitly set `trivyignores: .trivyignore`. Add the field to every `aquasecurity/trivy-action` invocation.
- **Preservation tests run from the host, not in the container.** `src/mcp/.dockerignore` excludes `tests/` — the runtime image doesn't ship them. Boot the stack via `docker-compose`, then run `pytest -m preservation` on the GitHub Actions runner against the container's public HTTP port. Preservation tests are pure `pytest + httpx + neo4j` with no `app.*` / `core.*` imports, so they have no container-side dependency. The Makefile target mirrors CI: `cd src/mcp && ../../.venv/bin/python -m pytest tests/integration/ -m preservation ...`.

## Cross-repo sync

A bidirectional sync workflow maintains the public `cerid-ai` mirror from the canonical development repo. Top three rules to never violate:

1. **Always use `scripts/sync-repos.py`**, never `cp` / `rsync` / direct edits.
2. **Add gitignored data directories to `_SYNC_SKIP_PREFIXES`** — `Path.rglob('*')` doesn't honour `.gitignore`. The skip list is the sync walker's parallel to git's ignore.
3. **File deletions in internal need explicit `--track-deletions`** on `to-public`. Without it, orphans pile up in public and break CI typecheck on imports of removed symbols.

## Frontend

- **Tailwind v4 via `@tailwindcss/vite` plugin** — no `tailwind.config.ts`.
- **shadcn/ui New York style, Zinc base color**, path alias `@/*` → `./src/*`.
- `crypto.randomUUID()` requires a secure context — on LAN-over-HTTP it's undefined. Use the shared `uuid()` helper in `src/web/src/lib/utils.ts` everywhere instead.
- **`.d.ts` basename must not collide with a `.ts` basename** in the same dir — TypeScript treats the `.d.ts` as a specific module declaration and ignores ambient declarations.

### Frontend patterns (graduated from `tasks/lessons.md` 2026-05-19)

- **Don't derive multi-state UI from a boolean server field.** When server state is less expressive than UI state (3+ display states, boolean server field), treat localStorage / a `useSettings`-style hook as source of truth for the richer state and sync the simplified version to the server. Duplicating state derivation in the consuming component is how `Select` snaps back to the wrong value on every render.
- **React infinite render loops** come from three recurring patterns: (a) object reference comparisons in `useEffect` deps when the value is from `useMemo` — compare by identity strings instead (e.g. `conversation_id + count`); (b) context callbacks (`useConversationsContext`) creating new references on every state update — store the callback in `useRef` and access via `.current`; (c) state updates fired during render — defer with `setTimeout(0)`.
- **`useRef + tick threshold` for high-frequency value churn.** A value that updates every SSE chunk during streaming doesn't need every render. Store the raw value in `useRef`, maintain a separate `useState` "tick" counter, and only `setTick()` when the ref crosses a meaningful threshold (e.g. every ~100 estimated tokens). Reduces re-renders from ~500/response to ~5. Reference: `use-live-metrics.ts` (`CHARS_PER_TICK = 400`).
- **"Confidence" in retrieval UI means relevance, not correctness.** When surfacing KB retrieval scores, label as "Relevance" or "Match score" — not "Confidence". "Confidence" reads to users as "we are sure this is true", but the field is actually a similarity score between query and stored content. Rename at the UI layer; the backend field name (`confidence` or `similarity`) is incidental.
- **Vite `manualChunks` defeats lazy-loading of large libraries.** Putting `react-syntax-highlighter` (or any 1MB+ library) into `manualChunks` forces a single eager chunk for the entire library — even with `React.lazy()` on the consumer side. Wrap in a thin module (`syntax-highlighter.ts`) that imports only the sub-modules / languages you need (`PrismLight` + 25 languages), then lazy-load the wrapper. Reduces the chunk from 1.6 MB to 104 KB.
- **`@internal` JSDoc beats un-exporting for test-accessed internals.** Un-exporting a function that has 10+ direct test cases forces rewriting every test to go through the public API — losing granular coverage and risking subtle behavioural drift. Keep the function exported, annotate with `@internal`, and let TypeScript surface the constraint at consumer sites. Reference: `model-router.ts` (4 functions, 45 test cases).
- **Chrome aggressively caches localhost.** `no-store` headers don't prevent disk cache on localhost; old JS bundles served even after a rebuild. Two defences: nginx `/assets/` returns 404 on miss instead of `index.html` fallback so cache-broken requests fail loudly; and dynamic API calls append `?_t=${Date.now()}` cache busters.

### Frontend UI contracts (SEXTANT settings + Subjects, 2026-06)

- **Primitive ladder.** Before hand-rolling JSX, climb: shadcn primitive
  (`components/ui/`) → Cerid primitive (`EmptyState`, `DomainBadge`,
  `ProgressBar`, …, also under `components/ui/`) → feature-area composite →
  hand-rolled (last resort). Two copies of the same `flex … rounded-md border`
  means extract a primitive. The drift gate guards the bottom of the ladder.
- **4-state UX matrix.** Every data-backed pane handles Loading (`Skeleton`
  shaped like the content), Error (`Alert variant="destructive"` + retry),
  Empty (`EmptyState`), and Success explicitly — never a bare `<div>` or muted
  string for empty. Pane tests assert all four render and are axe-clean.
- **Settings registry is the single source of truth.** Every control is one
  `SettingDef` in `lib/settings-registry/`; never hard-code a settings row.
  `writer` is a discriminated union
  (`settings-patch | preferences | endpoint | local | env | readonly`) so
  storage dispatch is type-checked. `keywords` must retain old tab names so
  search still finds moved settings. See
  [`docs/UI_ARCHITECTURE.md`](UI_ARCHITECTURE.md) § Settings registry.
- **`AdvancedDisclosure` consumption discipline.** The Simple|Advanced "detail
  level" (localStorage `cerid-settings-mode`) is read **only** by
  `AdvancedDisclosure` to pick a default-open state. It is not an app-wide UI
  mode — no other component branches on it. Search hits and `?setting=` deep
  links force-open the containing expander in either mode.
- **Entitlement gating goes through `useEntitlements()`.** One consolidated
  treatment returning `{available | locked | flag-off | degraded}` per setting,
  plus one Recommendations card per surface — don't scatter ad-hoc
  tier/flag checks.
- **Cross-pane navigation goes through `useNavigation()`.** Use `goTo(pane,
  {mode, entity, …})` / `composeChat(...)` — never `window.location` or a
  router (there is none). Wiki↔Atlas, Atlas→Wiki, etc. all route through this
  contract; the legacy redirect map in `navigation-context.tsx` keeps old pane
  names working.

## Design tokens (D.1)

> **Last refresh:** 2026-06-11 (added `--color-domain-*` lens tokens + Frontend UI contracts above)
> **Drift gate:** `scripts/lint-no-design-drift.py` (CI job `lint / no-design-drift`)

### Canonical design tokens

Cerid's design tokens live as CSS custom properties in `src/web/src/index.css`.
Use these in Tailwind classes via the `text-foreground`, `bg-primary`, etc. utility
names — not raw hex values.

**Core semantic tokens** (light + dark mode via `@media (prefers-color-scheme: dark)` or `.dark`):

| Token | Usage |
|---|---|
| `--background` / `bg-background` | Page / app background |
| `--foreground` / `text-foreground` | Primary text |
| `--card` / `bg-card` | Card surfaces |
| `--popover` / `bg-popover` | Popover / dropdown surfaces |
| `--primary` / `bg-primary` | Primary action colour |
| `--secondary` / `bg-secondary` | Secondary action colour |
| `--muted` / `bg-muted` | Muted backgrounds (disabled, subtle) |
| `--muted-foreground` / `text-muted-foreground` | De-emphasised text |
| `--accent` / `bg-accent` | Accent highlights (teal family) |
| `--destructive` / `bg-destructive` | Destructive actions and error states |
| `--border` / `border-border` | Standard border colour |
| `--input` / `border-input` | Form field borders |
| `--ring` / `ring-ring` | Focus ring colour |
| `--brand` / `bg-brand` | Cerid teal brand colour |
| `--brand-foreground` / `text-brand-foreground` | Text on brand-coloured surfaces |

**Chart tokens:** `--chart-1` … `--chart-5` — use via `fill-chart-1` etc.

**Domain lens tokens:** `--color-domain-0` … `--color-domain-11` plus an
`other` token — the family-wide Domain colour lens. Never hard-code a
per-domain colour; map through `domainSlot(domain)` in `lib/graph/identity.ts`
(salt 796, collision-free for the canonical 12). The old `DOMAIN_BADGE_COLORS`
map was deleted.

**Sidebar tokens:** `--sidebar`, `--sidebar-foreground`, `--sidebar-primary`,
`--sidebar-accent`, `--sidebar-border`, `--sidebar-ring`.

**Claim-overlay tokens:** `--claim-verified-border`, `--claim-refuted-bg`,
`--claim-refuted-border`, `--claim-unverified-bg`, `--claim-unverified-border`,
`--claim-evasion-bg`, `--claim-citation-bg`.

**Radius tokens:** `--radius-sm` through `--radius-4xl` — use via `rounded-sm` etc.

### Forbidden patterns

The drift gate (`scripts/lint-no-design-drift.py`) blocks four categories of
violation in `src/web/src/` (`.ts` / `.tsx` files):

1. **Raw hex literals** — `#ff0000`, `#abc`, `#rrggbbaa`. Replace with a CSS-var-backed
   Tailwind token or a named semantic class. Permitted inside CSS files and inside
   `var(--my-token, #fallback)` CSS fallbacks (linter excludes these automatically).

2. **Inline `style={{}}` props** — `<div style={{ width: "100%" }}>`. Use Tailwind's
   arbitrary-value mechanism (`w-full`, `w-1/2`) or a CSS variable. For dynamic values
   that genuinely need an inline style (e.g. a percentage-based progress bar that cannot
   be expressed as a Tailwind token), add `// drift-allowed: <reason>` at the end of
   the line.

3. **Tailwind arbitrary values** — `text-[10px]`, `p-[3px]`, `max-w-[240px]`, `ring-[3px]`.
   Replace with a canonical Tailwind scale step (`text-xs`, `p-1`, `max-w-sm`, etc.) or
   add a design token to `index.css`. The `src/web/src/components/ui/` shadcn directory
   is excluded from this check — shadcn-generated components use `ring-[3px]` etc. by design.

4. **Non-lucide icon imports** — `@heroicons`, `react-icons`, `@material-ui/icons`, etc.
   Use `lucide-react` exclusively. See `components.json` `iconLibrary: "lucide"`.

5. **Non-shadcn motion libraries** — `framer-motion`, `gsap`, `react-spring`, etc.
   Use Tailwind's built-in `animate-*` utilities or Radix's built-in CSS transitions.

### Allowlist / suppression mechanism

Two suppression paths exist for legitimate exceptions:

**Inline suppression** — append `// drift-allowed: <reason>` on the violating line:
```tsx
<ScrollArea style={{ maxHeight }}>  // drift-allowed: dynamic max-height drives scroll region
```

**Allow-file** — pass `--allow-file path/to/allow.txt` to the script. Format: `path:lineno` per line, comments with `#`. Use for multi-line ranges or file-level exceptions.

Neither suppression path is a licence to drift. All suppressions should be reviewed in
code review and ideally tracked as follow-up items against the D.1 punch list
(`tasks/2026-05-10-D1-design-drift-punch-list.md`).

### CI gate

The `lint / no-design-drift` CI job runs `scripts/lint-no-design-drift.py` in
`--report-only` mode — violations are printed to the job log but do not block the build
during the remediation window. Promotion to blocking follows the standard drift-gate
protocol: add to `docker needs[]` and remove `--report-only` after two consecutive
`main` builds show zero violations.

## Plugins & workflows

- Plugins carry a `manifest.json` (name, version, tier, description, entry). `plugins/` and `src/mcp/plugins/` are BUSL-1.1 and convert to Apache-2.0 after 3 years; `plugins-premium/` is proprietary (all rights reserved) and is not distributed — it is absent from the public repository by design.
- Tier gating enforced at load time via `CERID_TIER`.
- Workflow engine uses Kahn's algorithm for DAG validation — cycles rejected.

## When to retire a convention from this file

A convention moves OUT of this file when one of these happens:

1. **A lint rule catches it.** (Example: silent-catch is caught by `scripts/lint-no-silent-catch.py`.) Update the rule; delete the convention.
2. **A contract test enforces it.** (Example: claim shape is guarded by `tests/test_canonical_claim_model.py`.) Keep a one-line reference; delete the detail.
3. **It's specific to one file.** Move to an inline comment; delete here.

CONVENTIONS.md grows by one line per new pattern but should shrink over time as patterns graduate to code-enforced contracts.
