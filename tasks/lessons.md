# Cerid AI — Lessons Learned

> Patterns captured during development to prevent repeat mistakes.
> Updated as corrections occur — review at session start.

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
