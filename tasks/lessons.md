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

## Docker

### Use `127.0.0.1` not `localhost` in Alpine healthchecks
**When:** Writing Docker healthcheck commands for Alpine-based containers.
**Problem:** `localhost` resolves to `::1` (IPv6) in Docker Alpine, but many services only listen on `0.0.0.0` (IPv4).
**Fix:** Always use `127.0.0.1` explicitly in healthcheck URLs.
