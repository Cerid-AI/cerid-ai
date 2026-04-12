# Cerid AI — Lessons Learned

> Patterns captured during development to prevent repeat mistakes.
> Updated as corrections occur — review at session start.

---

## State Sync Architecture

### Use file-based sync for user state, not database
**When:** Building cross-machine sync for self-hosted single-user apps.
**Problem:** Considered SQLite or Postgres for user state persistence, but it adds infrastructure complexity for <50 conversations + 15 settings.
**Fix:** JSON files in a synced directory (Dropbox) are simpler and more portable. One file per conversation avoids merge conflicts. Dropbox handles transport + conflict detection for free.
**Pattern:** localStorage is the immediate cache (fast reads, offline resilience). Server writes to sync dir fire-and-forget. On mount, server data fills gaps in localStorage; localStorage wins on conflict by `updatedAt`.

### Don't mount sync volumes read-only if the server needs to write
**When:** Docker volume mounts for sync directories.
**Problem:** Sync dir was mounted `:ro` but the MCP server needed to write user state files.
**Fix:** Remove `:ro` when the application needs write access. Validate writability in `validate-env.sh`.

---

## Python Packaging

### `import *` skips underscore-prefixed names
**When:** Re-exporting from a package `__init__.py` via `from module import *`
**Problem:** Functions like `_strip_html_tags()` and `_strip_rtf()` are silently skipped because Python's `import *` convention excludes names starting with `_`.
**Fix:** Add an explicit `__all__` list to the package `__init__.py` that includes all underscore-prefixed names that need to be re-exported.
**Example:** `parsers/__init__.py` — must list `_strip_html_tags` and `_strip_rtf` in `__all__`.

### `import` is a reserved keyword — use `import_.py`
**When:** Creating a module for import functionality (e.g., sync import).
**Problem:** `import.py` is a syntax error — `import` is a Python keyword.
**Fix:** Use trailing underscore convention: `import_.py`. This is the standard Python naming convention for keyword conflicts (`class_`, `type_`, etc.).

### Re-export shim pattern for backward compatibility
**When:** Splitting a large module into a package while preserving all existing importers.
**Pattern:**
1. Create the new package (e.g., `parsers/`) with proper sub-modules
2. Replace the original file with a 2-line shim: `from package import *`
3. Use `__all__` in the package `__init__.py` for underscore names
4. All existing `from original_module import X` statements continue to work unchanged
**Reference:** `config/__init__.py` was the original example; `utils/parsers.py`, `utils/graph.py`, `cerid_sync_lib.py` all follow this pattern.

---

## Git & DevOps

### Root-relative `.gitignore` rules for common directory names
**When:** `.gitignore` has rules like `neo4j/`, `chroma/`, `redis/` to exclude data directories.
**Problem:** These rules match at ANY depth — so `src/mcp/db/neo4j/` is also ignored, blocking `git add`.
**Fix:** Use leading `/` for root-relative rules: `/neo4j/`, `/chroma/`, `/redis/`. This only matches at the repository root.

### Always check `git add` output for ignored files
**When:** Adding new directories whose names match existing `.gitignore` patterns.
**Fix:** Run `git add -n` (dry run) first, or check `git status` after `git add` to confirm files are staged.

---

## Testing

### Watch for `sys.modules` stub pollution across test files
**When:** One test file injects a module stub (e.g., `sys.modules["agents.query_agent"] = stub`) and another test file tries to import the real module.
**Problem:** pytest collects all test files before running them. If `test_hallucination.py` injects a stub `agents.query_agent` with only `agent_query`, then `test_query_agent.py` can't import `_get_adjacent_domains` because the stub is cached.
**Fix:** Guard the import in the consumer test: check if the cached module has the expected attributes, and `del sys.modules[...]` if not, so the real module loads.
**Rule:** Prefer `unittest.mock.patch` over manual `sys.modules` manipulation for test isolation.

### Use `create=True` when patching attributes on stub modules
**When:** conftest.py stubs heavy dependencies as empty `ModuleType` objects (e.g., `sys.modules["pandas"] = ModuleType("pandas")`).
**Problem:** `@patch("pandas.read_csv")` fails with `AttributeError: <module 'pandas'> does not have the attribute 'read_csv'` because the stub has no attributes.
**Fix:** Use `@patch("pandas.read_csv", create=True)` — this allows the mock patcher to create the attribute on the stub if it doesn't already exist.
**Applies to:** Any `@patch` targeting attributes on stub modules (docx.Document, openpyxl.load_workbook, etc.).

### Use `side_effect` for mocks that return mutable containers
**When:** A mock's `return_value` is a list that gets mutated by the code under test.
**Problem:** `mock.return_value = [item]` returns the SAME list on every call. If the code does `results.extend(cross_results)` where both are the same list reference, the list grows unexpectedly.
**Fix:** Use `mock.side_effect = [[item], []]` to return fresh lists on each call.

---

## Code Quality

### Don't derive multi-state UI from a boolean server field
**When:** A UI control has 3+ states but the server API stores a boolean.
**Problem:** Settings Pane derived model router Select value from `enable_model_router: boolean` — `true` mapped to "Recommend", `false` to "Manual". "Auto" could never be displayed. The Select snapped back to "Recommend" on every render.
**Fix:** Use the canonical local state source (the `useSettings` hook's `routingMode`, backed by localStorage) which already tracks all 3 states. The hook syncs the boolean to the server. Don't duplicate state derivation logic in the consuming component.
**Pattern:** When server state is less expressive than UI state, treat localStorage (or a React hook) as the source of truth for the richer state, and sync the simplified version to the server.

### Check for latent bugs during structural splits
**When:** Splitting a large file into sub-modules.
**Lesson:** The mechanical process of splitting reveals bugs that were hidden in the original monolith. During the sync lib split, found 3 instances of `collection_name` (undefined) instead of `coll_name` (the correct local variable) in error logging paths. Also found duplicated utility functions (`_utcnow_iso()`) that should use the canonical version.
**Action:** Review each function carefully during extraction, not just copy-paste.

---

## Bifrost / OpenRouter

### Model IDs require `openrouter/` prefix
**When:** Configuring `CATEGORIZE_MODELS` or any model ID for Bifrost LLM gateway.
**Problem:** Bifrost requires the `provider/model` format (e.g., `openrouter/meta-llama/...`). Without the prefix, Bifrost returns `{"message":"model should be in provider/model format"}`.
**Fix:** Always prefix model IDs with `openrouter/` when routing through Bifrost to OpenRouter.

### Free-tier models have aggressive rate limits
**When:** Calling free OpenRouter models (`:free` suffix) in bulk operations like synopsis generation.
**Problem:** Free models are limited to ~8 RPM. Retry loops on 429 burn through the quota even faster since each retry counts as a request.
**Fix:** Use 8+ second base delay between requests. On 429, wait a full 60s before a single retry — don't use exponential backoff with multiple retries, as that wastes quota. Accept incremental progress across multiple runs.

---

## Docker

### Use `127.0.0.1` not `localhost` in Alpine healthchecks
**When:** Writing Docker healthcheck commands for Alpine-based containers.
**Problem:** `localhost` resolves to `::1` (IPv6) in Docker Alpine, but many services only listen on `0.0.0.0` (IPv4).
**Fix:** Always use `127.0.0.1` explicitly in healthcheck URLs.

---

## CI / DevOps

### pip-compile lock sync requires matching Python micro-version
**When:** CI job verifies `requirements.lock` matches `pip-compile` output.
**Problem:** `actions/setup-python@v5` installs a different Python 3.11.x micro-version than the `python:3.11-slim` Docker image. Different micro-versions produce different wheel hashes from `pip-compile`, causing the lock-sync diff to fail on 2-3 hashes.
**Fix:** Use `container: python:3.11-slim` in the CI lock-sync job to match the Dockerfile exactly. Do not rely on `actions/setup-python` for hash-sensitive operations.

### pip-compile without `--upgrade` reuses stale resolution
**When:** Regenerating `requirements.lock` locally to match CI's lock-sync check.
**Problem:** `pip-compile` without `--upgrade` reuses pinned versions from the existing lock file. CI compiles from scratch into `/tmp/requirements.lock` (no existing lock), so it resolves the latest versions within ranges. This causes persistent diffs (e.g., `chromadb 0.5.20` locally vs `0.5.23` in CI, `huggingface-hub 1.7.1` vs `0.36.2`).
**Fix:** Always use `pip-compile --upgrade` when regenerating the lock file locally. This forces fresh latest-within-range resolution, matching CI's from-scratch behavior.
**Command:** `docker run --rm -v "$(pwd)/src/mcp:/work" -w /work python:3.11-slim bash -c "pip install -q pip-tools==7.5.3 && pip-compile --upgrade --generate-hashes --no-header --allow-unsafe -o requirements.lock requirements.txt"`

### LangGraph StateGraph(dict) type suppression needs per-call ignores
**When:** Using `StateGraph(dict)` with mypy and newer langgraph versions.
**Problem:** Adding `# type: ignore[type-var]` only to the `StateGraph(dict)` line suppresses the construction error, but the bad type propagates — every subsequent `graph.add_node()` call also produces `[type-var]` errors.
**Fix:** Add `# type: ignore[type-var]` to each `graph.add_node()` call individually. A TypedDict approach is impractical when the graph uses dynamic state keys throughout its node functions.

### Dependabot version ranges may break transitive dependencies
**When:** Dependabot widens version ranges (e.g., `chromadb>=0.5,<0.6` → `chromadb>=0.5,<1.6`).
**Problem:** Major version bumps can drop transitive deps. chromadb 1.x removed `chroma-hnswlib` (used by `import hnswlib` in semantic_cache.py), causing 10 test failures.
**Fix:** Always review Dependabot's upper bound changes against breaking change notes. For libraries with known API/dep breaks across majors, keep conservative upper bounds (e.g., `<0.6` not `<1.6`).
**Also:** eslint 10.x broke `eslint-plugin-react-hooks@7.0.1` (peer requires `^3-^9`). Same pattern — revert to `^9.x`.

### Trivy `.trivyignore` must be referenced in EACH scan step
**When:** Adding a `.trivyignore` file to suppress known CVEs in Trivy container scanning.
**Problem:** If the CI workflow has multiple Trivy scan steps (e.g., one for MCP image, one for web image), the `.trivyignore` is only applied to the step that explicitly sets `trivyignores: .trivyignore`. Other steps scan without suppressions.
**Fix:** Add `trivyignores: .trivyignore` to every `aquasecurity/trivy-action` step in the CI workflow, not just the first one.

---

## Frontend / Web APIs

### `crypto.randomUUID()` requires a secure context
**When:** Using `crypto.randomUUID()` in a web app accessed via LAN IP over plain HTTP.
**Problem:** `crypto.randomUUID()` is only available in [secure contexts](https://developer.mozilla.org/en-US/docs/Web/API/Crypto/randomUUID) (HTTPS or `localhost`). Accessing via `http://192.168.x.x` makes `crypto.randomUUID` `undefined`, causing `TypeError` on every call. This silently breaks all API calls using request ID headers, causing React Query to enter error state.
**Fix:** Use a shared `uuid()` helper that falls back to `crypto.getRandomValues()` (available in all contexts) to construct a UUID v4 manually. Export from `utils.ts` and use everywhere instead of `crypto.randomUUID()`.
**Reference:** `src/web/src/lib/utils.ts:uuid()`, used by `api.ts`, `use-chat.ts`, `use-conversations.ts`, `use-model-switch.ts`, `chat-panel.tsx`.

---

## Frontend / Bundle Size

### PrismLight vs full Prism for bundle size
**When:** Using `react-syntax-highlighter` in a Vite project with `manualChunks`.
**Problem:** The full `Prism` export bundles all 200+ language grammars (~1.6MB). Even with `React.lazy()`, placing `react-syntax-highlighter` in `manualChunks` forces Vite to create a single eager chunk for the entire library, bypassing code splitting.
**Fix:** Create a wrapper module (`syntax-highlighter.ts`) that imports `PrismLight` from the ESM sub-path, registers only the ~25 languages needed, and exports the configured component. Lazy-load the wrapper instead of the main package. Reduces the chunk from 1619KB to 104KB.
**Reference:** `src/web/src/lib/syntax-highlighter.ts`, `src/web/src/lib/react-syntax-highlighter.d.ts`

---

## TypeScript

### `.d.ts` files with same basename as `.ts` files conflict
**When:** Creating ambient module declarations for a library's ESM sub-paths (e.g., `react-syntax-highlighter/dist/esm/prism-light`).
**Problem:** If a `.d.ts` file has the same basename as a `.ts` file in the same directory (e.g., `syntax-highlighter.d.ts` alongside `syntax-highlighter.ts`), TypeScript treats the `.d.ts` as the type declaration for that specific `.ts` module rather than as ambient module declarations. The ambient `declare module` statements are ignored.
**Fix:** Name the `.d.ts` file distinctly from any `.ts` module (e.g., `react-syntax-highlighter.d.ts` instead of `syntax-highlighter.d.ts`).

---

## Refactoring (Phase 30)

### `extractError()` changes error messages — update test assertions
**When:** Standardizing API error handling to use `extractError()` across all functions.
**Problem:** `extractError()` parses the response body for a `detail` field (FastAPI convention) and surfaces the server's message instead of the generic fallback. Tests that assert on the fallback message (e.g., `expect(screen.getByText(/failed/i))`) break because the actual error text changes.
**Fix:** After standardizing error handling, audit tests that mock non-OK responses. Update assertions to match the new error messages.
**Reference:** `settings-pane.test.tsx` — changed from `/failed/i` to `/server error/i` after Sprint 4.1.

### `@internal` annotation vs un-exporting for test-accessed internals
**When:** Cleaning up exports that are only used by tests.
**Problem:** Un-exporting test-accessed functions forces rewriting all tests to go through the public API. This loses granular unit test coverage and risks introducing subtle bugs.
**Rule:** If a function has 10+ direct test cases, keep it exported with `@internal` JSDoc annotation. The test coverage value outweighs the encapsulation benefit.
**Reference:** `model-router.ts` — 4 functions with 45 total test cases kept as `@internal`.

### useRef + tick threshold pattern for controlled re-renders
**When:** A value changes frequently (e.g., every SSE chunk during streaming) but the derived display only needs periodic updates.
**Pattern:** Store the raw value in a `useRef`, maintain a `useState` "tick" counter, and only call `setTick()` when the ref crosses a meaningful threshold (e.g., every ~100 estimated tokens).
**Benefit:** Reduces re-renders from ~500 per response to ~5 while maintaining smooth UI updates.
**Reference:** `use-live-metrics.ts` — `CHARS_PER_TICK = 400`, `streamingCharsRef` + `streamingTick`.

---

## Vercel & Domain Deployment (Phase 33)

### Vercel GitHub App: configure repository access explicitly
**When:** Importing a private GitHub repo into Vercel via the dashboard.
**Problem:** Even with "All repositories" access on the GitHub App, Vercel may fail with "could not access the repository" error.
**Fix:** Use the Vercel CLI (`npx vercel deploy --prod`) as a fallback. Requires `npx vercel login` (device code flow) then `npx vercel link --scope <team>` before deploy.

### Vercel CLI requires team scope for non-interactive deploy
**When:** Running `npx vercel deploy --yes` in a CI/CD or automated context.
**Problem:** Without a linked project, the CLI prompts interactively. The `--yes` flag skips prompts but fails with `missing_scope` if no team is configured.
**Fix:** Run `npx vercel link --yes --scope <team-slug>` first to create `.vercel/project.json`, then `npx vercel deploy --yes --prod`.

### DNS propagation: CNAME faster than A records
**When:** Setting up custom domains on Vercel (or any CDN/hosting provider).
**Problem:** A records take longer to propagate (60s+) than CNAME records (~30s) because CNAME delegation is simpler for DNS resolvers to update.
**Fix:** Use `dig @8.8.8.8 cerid.ai` for non-cached DNS checks during propagation. Don't rely on the browser — Chrome caches DNS aggressively and may show stale results even after propagation completes.

### Chrome DNS cache vs curl for DNS verification
**When:** Verifying a domain change has propagated.
**Problem:** Chrome holds DNS cache even after global propagation. `curl -sI https://cerid.ai` returns HTTP/2 200 while Chrome still shows an error page.
**Fix:** Use `curl` for reliable DNS verification. If browser testing is needed, use Safari or `chrome://net-internals/#dns` to clear Chrome's DNS cache.

---

### Re-export bridge pattern for component promotion
**When:** Moving a component from a domain-specific module to shared UI (e.g., `kb/domain-filter.tsx` → `ui/domain-badge.tsx`).
**Pattern:**
1. Create the new file at the canonical location
2. Update all consumers to import from the new path
3. Leave a re-export in the old location for backward compatibility: `export { DomainBadge } from "@/components/ui/domain-badge"`
**Benefit:** Zero risk of breakage from missed imports, serves as documentation for anyone reading the old module.

---

## Privacy & Security

### Default to most restrictive setting, let users opt-in to openness
**When:** Configuring network exposure, CORS, port binding, or any security-relevant default.
**Problem:** CORS defaulted to `*` and ports bound to `0.0.0.0`, exposing services to the entire LAN. Convenient for development but inconsistent with a privacy-first product.
**Fix:** Default to the most locked-down setting (`localhost` CORS origins, `127.0.0.1` bind address). Provide env vars for users who need broader access to opt in explicitly.
**Pattern:** Restrictive defaults + opt-in openness is safer than permissive defaults + opt-out hardening. Users who need LAN access will set the env var; users who don't will never know they were protected.

### Audit privacy claims against actual data flows — claims drift as features are added
**When:** Adding features that change where data flows (e.g., cloud sync, analytics, telemetry).
**Problem:** Marketing site and CLAUDE.md claimed "all data stays local" but Phase 38D added Dropbox cloud sync, uploading user state to a third-party service. Nobody updated the privacy claims when the sync feature shipped.
**Fix:** Treat privacy claims as code — they need to be updated in the same PR that changes data flow. Add a mental checklist item: "Does this feature change where data goes? If yes, update marketing + CLAUDE.md."

### Use existing encryption infrastructure rather than adding new crypto
**When:** Adding encryption to a new feature (e.g., sync directory at-rest encryption).
**Problem:** Tempting to reach for a new encryption approach or library for each new encryption need.
**Fix:** Reuse `utils/encryption.py` (Fernet symmetric encryption from the `cryptography` library) which was already battle-tested for API key encryption. Same key management, same patterns, no new dependencies.

---

## Model Routing

### Chat model router vs verification pipeline have separate routing
**When:** Debugging "model X can't handle query Y" — e.g., temporal claims marked uncertain, real-time queries not routed to web-search models.
**Problem:** The verification pipeline has its own separate routing that already handles temporal claims correctly via `_is_current_event_claim()`. The bug was in the CHAT model router scoring/filtering, not verification.
**Fix:** Check the chat model router scoring and filtering first. The chat router (`model-router.ts`) controls which model receives the initial query; the verification pipeline (`use-verification-stream.ts`) only validates the response afterward. Fixing the wrong layer wastes time.
**Reference:** `src/web/src/lib/model-router.ts` (chat routing), `src/web/src/hooks/use-verification-stream.ts` (verification routing)

## Session: 2026-03-18/19 — Cross-Project Audit & Pipeline Fixes

### Trading Agent Module: Use Correct Neo4j Session API
- **Problem:** `trading_agent.py` called `neo4j.execute_read()` which doesn't exist on `BoltDriver`. Also called `chroma.query()` on the client wrapper instead of getting a collection first.
- **Fix:** Created `_neo4j_query()` helper using `driver.session().run()`. Changed ChromaDB to use `chroma.get_collection(name).query()` per domain.
- **Rule:** When writing functions that receive dependency-injected clients, check what API the injected object actually exposes. Don't assume method names.

### structlog Missing from Docker Container
- **Problem:** `structlog` was in `requirements.txt` but not in `requirements.lock` (hash-pinned). Dockerfile installs from lock file only.
- **Fix:** Generated hash for structlog and appended to `requirements.lock`.
- **Rule:** After adding a dependency to `requirements.txt`, always regenerate `requirements.lock` before building Docker images. Use `make lock-python`.

### Rate Limiting Middleware: Don't Depend on request.state From Other Middleware
- **Problem:** `RateLimitMiddleware` read `request.state.client_id` which was set by `RequestIDMiddleware`. But middleware executes LIFO — rate limiting ran before the ID was set. All clients got default limits.
- **Fix:** Read `X-Client-ID` directly from `request.headers` instead of `request.state`.
- **Rule:** Middleware should read from immutable request data (headers, URL), not from mutable state set by other middleware. Order-dependent state sharing between middleware is fragile.

### Keywords Metadata Key Mismatch
- **Problem:** `ingest_file()` stored keywords as `meta["keywords"]` but `ingest_content()` read `meta.get("keywords_json")`. AI-categorized keywords silently lost to Neo4j.
- **Fix:** Standardized on `keywords_json` everywhere (5 locations).
- **Rule:** Use a single canonical field name. Grep for all access patterns before adding new metadata fields.

### Embedding Dimension: Return list[np.ndarray] for ChromaDB 0.5.x
- **Problem:** `OnnxEmbeddingFunction.__call__()` returned `list[list[float]]` via `.tolist()`, but ChromaDB 0.5.23 `validate_embeddings()` requires `list[np.ndarray]`.
- **Fix:** Return `[embeddings[i] for i in range(embeddings.shape[0])]` — individual numpy array slices.
- **Rule:** Check the exact type contract of the library's validation function, not just the protocol type hints.

### New "trading" Domain for KB Segregation
- **What:** Added a dedicated `trading` domain to taxonomy with its own sub-categories and tag vocabulary. Moved trading-specific tags from `finance` to `trading`.
- **Why:** Trading agent queries were matching personal tax documents. Domain segregation ensures trading KB enrichment only searches trading-relevant content.
- **Reference:** `src/mcp/config/taxonomy.py` — TAXONOMY["trading"], TAG_VOCABULARY["trading"]

---

## Session: Verification Crash Debugging (2026-03-22)

### Docker Build Failures Are Silent
- `docker compose build --no-cache` can fail with exit code 2 inside the build stage but Docker still uses the cached previous image
- Always verify `npm run build` succeeds by checking build output for "built"
- TypeScript strict mode in Docker build (via `npm run build`) catches errors that `npx tsc --noEmit` misses (unused imports, missing Record keys)
- Fix: always check `docker compose build --progress=plain cerid-web 2>&1 | grep error` after builds

### React Infinite Render Loops
- Object reference comparisons in useEffect deps cause loops: `if (report !== reportRef.current)` fires every render when `report` comes from useMemo
- Context callbacks (useConversationsContext) get new references on every state update, creating cascading re-renders
- Fix: compare by identity strings (conversation_id + count), NOT object references
- Fix: store context callbacks in useRef and access via .current
- Fix: use setTimeout(0) to defer context state updates out of the render cycle

### Circuit Breaker Name Mismatches
- All LLM call sites must use breaker names that exist in circuit_breaker.py registry
- The registry must be updated whenever new call sites are added
- `f"bifrost-{breaker_name}"` in fallback paths can double-prefix names

### Claim Extraction Edge Cases
- `response_format: {"type": "json_object"}` forces LLMs to return objects, not arrays
- Claims may be wrapped: `{"claims": [...]}` — always unwrap before processing
- Pleasantry patterns must match ANYWHERE in sentence, not just at start

---

## Session 2026-03-27 — Phase C + Leapfrog + CI Fixes

### Bridge Module Patch Targets
- When Phase C moved code into `core/` with bridge modules, `import *` skips `_`-prefixed names
- `@patch` must target the module where the name is looked up at runtime, NOT the source module
- If `tools.py` does `from agents.foo import bar`, patch `agents.foo.bar` (bridge), not `core.agents.foo.bar` (source)
- 547 patch targets had to be updated across 34 test files

### import-linter
- `lint-imports` reads config from CWD — if CI runs `cd src/mcp && lint-imports`, config must be in `src/mcp/.importlinter` (not root pyproject.toml)
- Contract violations are real bugs: `config.model_providers` importing from `core.routing` violated the "config must not import core" rule

### React 19 ESLint
- `react-hooks/refs` rule forbids writing `ref.current = value` during render
- Must wrap in `useEffect(() => { ref.current = value }, [value])`
- `useRef<T>()` without initial value now requires explicit `undefined`: `useRef<T>(undefined)`

### Security Audit Patterns
- Path traversal: always `resolve()` + `is_relative_to()` on user-supplied paths

---

## Session 2026-04-06 — Beta Test Performance

### Never increase timeouts to fix performance problems
- **Problem:** Verification pipeline took 8-25s. I increased timeouts 3 times (10→20→60s) instead of investigating WHY extraction was slow.
- **Root cause:** `extract_claims()` always called LLM first (Ollama 8-25s), only fell back to <50ms heuristic if LLM returned empty. Heuristic handles 80%+ of responses.
- **Fix:** Reverse extraction order — heuristic first, LLM only as fallback. First event now <1.2s.
- **Rule:** If something is slow, profile it and find the bottleneck. Increasing timeouts is a band-aid that masks the real problem. Ask: "What would a senior dev say about increasing a timeout as a fix?"

### System-check endpoint runs inside Docker container, not on the host
- **Problem:** `shutil.which("docker")` returns None inside a container. `.env` file doesn't exist inside the container.
- **Fix:** Use `Path("/.dockerenv").exists()` for Docker detection. Read env vars (`os.getenv()`) for config keys (Docker passes them via `env_file`).
- **Rule:** When building system-check endpoints, think about WHERE the code runs. Container-resident code can't see host filesystem or binaries.
- SSRF: validate token endpoints from OIDC discovery docs (attacker controls the discovery JSON)
- XSS in entrypoints: JSON-encode env vars before writing to JS files
- Dict iteration: `list(dict.items())` to snapshot before async iteration

---

## Session 2026-04-07 — Beta Test, Performance, Wiring Sprint

### Increasing timeouts is not a fix for slow code
- **Problem:** Verification pipeline took 8-25s. Increased timeouts 3 times (10→20→60s).
- **Root cause:** `extract_claims()` always called LLM first, heuristic regex was only fallback.
- **Fix:** Reverse extraction order — heuristic first (<5ms), LLM only when heuristic finds nothing.
- **Rule:** Profile before patching. If something is slow, find WHY. Timeouts are band-aids.

### System-check endpoints run inside Docker, not on host
- `shutil.which("docker")` returns None inside containers. Use `Path("/.dockerenv").exists()`.
- `.env` file doesn't exist inside container. Read `os.getenv()` for config keys.

### Chrome aggressively caches localhost
- `no-store` headers don't prevent Chrome disk cache on localhost for initial responses.
- Old JS bundles cached even after rebuilds. Fix: nginx `/assets/` returns 404 on miss instead of index.html fallback.
- Always add `?_t=${Date.now()}` cache buster to dynamic API calls in fetchSystemCheck.

### Bridge module `import *` skips private names
- Python's star import excludes `_` prefixed names. Tests that mock `_extract_claims_heuristic` through bridge fail.
- 20+ private functions needed explicit re-exports across 12 bridge modules.
- **Rule:** When adding any `_private` function to core/, immediately add explicit re-export to the bridge.

### Trading/boardroom/finance are SDK clients, not core features
- Client repos connect via `/sdk/v1/` endpoints. Client-specific endpoints, models, scheduler jobs, and MCP tools belong in internal repo only.
- Public core repo should have ZERO client-specific code — only the SDK framework.
- **Cleaned:** 1107 lines of trading/boardroom contamination removed from public.

### KB verification false matches need term-overlap sanity check
- Vector similarity produces false matches (e.g., cabin project docs matching light wavelength claims).
- Fix: regex term extraction (<1ms), require 25% overlap between claim terms and source snippet.
- Applied before ALL fallback paths, not just the verified threshold.

### Verification routing should match training data cutoff
- Pre-2024 facts → cross-model (GPT-4o-mini, 5-7s). Post-2024 current events → web search (Grok :online, 15-18s).
- Empty KB caused ALL claims to force web_search. Fixed by checking `_is_current_event_claim()` before forcing.

### "Confidence" in KB panel means relevance, not correctness
- Backend `confidence` field = mean KB retrieval relevance. Not verification confidence.
- Renamed to "Relevance" in UI to prevent confusion.
- Bar only shows when results.length > 0 (post-filter), not totalResults > 0 (pre-filter).

### External data sources exist but were never wired
- 9 sources registered (Wikipedia, DuckDuckGo, etc.) but `registry.query_all()` never called from query pipeline.
- `orchestrated_query()` existed as dead code — never invoked from HTTP route.
- Fixed: added `rag_mode` to `AgentQueryRequest`, wired orchestrator + external sources.

### Commit attribution policy enforcement
- 47 public commits and 524 internal commits had `Co-Authored-By: Claude` in body.
- Fixed with `git filter-repo --message-callback` + force push.
- **Rule:** Never include AI attribution in commits. This is in dotfiles/CLAUDE.md but was not followed.

### NEVER bulk-copy files between internal and public repos
- **Problem:** Copying `settings.py` from public to internal deleted `CERID_TRADING_ENABLED`, `TRADING_AGENT_URL`, `CERID_BOARDROOM_ENABLED`, and consumer registry entries. Broke internal CI.
- **Problem:** Copying `agents.py` from public to internal deleted 5 trading endpoints. Copying `main.py` deleted alert/migration/ws_sync/trading/eval/billing router registrations.
- **Rule:** NEVER use `cp`, `rsync`, or bulk file operations between repos. Cherry-pick individual changes. ALWAYS diff each file before committing a sync.
- **Key files that DIFFER:** `config/settings.py`, `config/taxonomy.py`, `app/routers/agents.py`, `app/routers/sdk.py`, `app/main.py`, `app/tools.py`, `app/scheduler.py`, `.github/workflows/ci.yml`, `CLAUDE.md`
- **Recovery:** When internal CI fails with `Module has no attribute "CERID_TRADING_ENABLED"`, the public settings.py was copied over. Restore from `git show HEAD~1:src/mcp/config/settings.py`.

## Session 2026-04-09/10: NLI + Verification + Sync Architecture

### Architecture Changes
- **Surgical file splits**: 7 mixed files split into base + `*_internal.py`. Internal-only code (trading, boardroom, billing, enterprise) lives exclusively in `*_internal.py` files. Base files have zero internal references.
- **Sync tooling**: `scripts/sync-repos.py` (to-public, from-public, validate) + `.sync-manifest.yaml` automates bidirectional repo sync. No more manual cherry-picking.
- **NLI entailment service**: `core/utils/nli.py` — shared ONNX model (cross-encoder/nli-deberta-v3-xsmall, 22M params, <10ms). Replaces similarity-as-proof across verification, Self-RAG, RAGAS faithfulness, and RAG pipeline.
- **Source authority tiering**: Chat transcripts (filename `chat_*`) get 0.35x relevance discount in KB retrieval. Extracted memories (`memory_*`) retain full relevance. Prevents circular self-verification.
- **External source integration**: External data sources (Wikipedia, DuckDuckGo, etc.) run in parallel with KB queries in manual mode. Results tagged `domain: "external"` and discounted 0.6x.

### Bug Fixes
- `memory_type` was NOT passed to `calculate_memory_score()` — all memories used "decision" 90-day decay. Fixed: each type uses its configured half-life.
- `MEMORY_TYPES` had 3 different definitions across patterns.py, memory.py, settings.py. Unified to include all 6 canonical + 2 legacy aliases.
- Settings GET response missing `enable_memory_consolidation` and `enable_context_compression`. Fixed.
- Pipeline config bar had stale "Reranking" and "Graph RAG" controls not synced to backend. Replaced with server-synced Self-RAG, Query Decomposition, Semantic Cache + NLI indicator.

### Key Patterns
- **NLI gate fires AFTER KB retrieval, BEFORE verdict**: entailment ≥ 0.7 → verified, contradiction ≥ 0.6 → unverified, neutral → fallback to similarity + external verification.
- **Temporal claims always web-searched**: Even if NLI says "entailed," recency/current-event claims force web search to catch stale KB data.
- **Bootstrap order matters**: `extend_settings()` → `extend_taxonomy()` → register routers. Settings must be populated before any router reads `CERID_TRADING_ENABLED`.
- **Hook markers for sync**: Each mixed file has `# -- Internal ...` marker. Sync script truncates at marker for public, appends for internal.
- **Test isolation in CI**: `bootstrap_internal()` may run if any test imports `app.main`, extending TAXONOMY to 13 domains. Tests must assert supersets, not exact domain counts.

### Backlog (tasks/todo.md)
- Knowledge Packs: Downloadable curated fact packs (Wikidata subset)
- Hardware-Aware Preset Recommendations: Setup wizard should use detected RAM/CPU/GPU to recommend presets
- GPU-Aware Model Selection: Surface GPU acceleration for local model routing

### ALWAYS use sync-repos.py, NEVER manual file copy
**When:** Syncing changes between internal and public repos.
**Problem:** Multiple sessions manually copied files between repos (cp, direct edits), bypassing the sync manifest. This caused CLAUDE.md contamination (internal content in public), internal-only test files leaking to public, and stale worktrees with trading/boardroom content.
**Fix:** ALWAYS use `python scripts/sync-repos.py to-public` for syncing. The sync manifest (.sync-manifest.yaml) has internal_only patterns, mixed_files with hook markers, and forbidden_in_public patterns. A `leak-check` CI job in the public repo now scans for forbidden patterns on every push.
**Pattern:** Run `python scripts/sync-repos.py validate` before and after any sync operation. Never `cp` files directly.

### Circuit breakers that share an upstream must be reset together
**When:** Startup probe succeeds for a shared external service (e.g., OpenRouter).
**Problem:** `_openrouter_auth_probe_loop()` only reset the `openrouter` breaker on successful auth, but `bifrost-verify`, `bifrost-claims`, `bifrost-synopsis`, etc. all call the same OpenRouter API. Transient DNS failures at startup tripped `bifrost-verify` and it was never reset, causing verification to fail permanently until container restart.
**Fix:** Reset ALL OpenRouter-dependent breakers (7 total) in the probe success handler, not just the one named `openrouter`.
**Pattern:** When adding a circuit breaker for a new call site to an existing upstream, add it to the startup probe's reset list in `main.py:_openrouter_auth_probe_loop()`.
