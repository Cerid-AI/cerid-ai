# Lessons Learned

Patterns discovered during development. Review at session start for relevant projects.

---

## v0.82 Sprint (2026-04-05)

### Live-test everything
Never mark a task done based on code existing. Test with `curl` against running containers (`localhost:8888`, `localhost:3000`). The CI environment (Linux x86) differs from Docker Desktop (macOS ARM).

### Dependency lock must stay in sync
Pre-commit hook blocks commits when `requirements.txt` changes without matching `requirements.lock`. Always run `make lock-python` or the Docker pip-compile command after changing deps.

### Silent `except: pass` is a CI failure
Ruff BLE001 + the custom "no silent except:pass" lint rule (threshold-based at 21) catch bare exception handlers. Always add `logger.debug()` or use specific exception types with `# noqa: BLE001`.

### Import ordering (ruff I001)
Ruff catches unsorted import blocks. Conditional imports inside `if` blocks need `# noqa: I001`. Blank lines between stdlib imports create separate blocks — keep them together.

### Frontend test text must match UI
When changing visible text (e.g. "Install Ollama" → "All platforms"), update corresponding test assertions in `src/web/src/__tests__/`. TypeScript build catches unused imports but not stale test strings.

### ChromaDB: always get_or_create_collection
Memory recall and conversation features fail on fresh installs if collections don't exist. Always use `get_or_create_collection`, never `get_collection`.

### Internal keeps structlog, public doesn't
`structlog` was removed from public requirements (replaced with stdlib `logging` in `startup_self_test.py`). Internal keeps it because `test_trading_agent.py` imports it. Don't sync the structlog removal to internal.

### Sidecar runs outside Docker
The FastEmbed sidecar needs host GPU access (Metal/CUDA). `start-cerid.sh` auto-detects installed deps (`onnxruntime` + `fastapi`) and auto-starts sidecar in background with `nohup`.

### Docker image: use /usr/local/bin/docker
On macOS, the Docker CLI may not be in shell PATH. Always use `/usr/local/bin/docker` in scripts and commands run from Claude Code.

### Use 127.0.0.1 not localhost in Docker Alpine healthchecks
Alpine-based containers (Redis) don't resolve `localhost` reliably. Use `127.0.0.1` in healthcheck commands.

### Model IDs need openrouter/ prefix for Bifrost
When configuring models through Bifrost, always include the `openrouter/` prefix (e.g. `openrouter/openai/gpt-4o-mini`).

### Semantic cache dimension must match embedding model
768 dimensions for Snowflake Arctic Embed M v1.5. Mismatched dimensions cause silent retrieval failures.
