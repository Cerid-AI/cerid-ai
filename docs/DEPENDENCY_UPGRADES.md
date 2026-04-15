# Dependency Upgrade Queue

> **Purpose:** Track staged platform/runtime upgrades that need dedicated coordination
> (not handled by routine dependabot group PRs). Updated as items land or conditions change.
> **Last reviewed:** 2026-04-15

For the routine-bump policy, see `.github/dependabot.yml`: `npm-deps` and `python-deps`
groups match `minor` + `patch` only; majors arrive as individual PRs per-package. The
docker `python` base image is additionally gated against major+minor bumps — the
runtime upgrade is tracked below as its own coordinated PR, not a transitive docker
change that would skip the necessary Dockerfile/CI/pyproject/mypy coordination.

---

## Recently landed (2026-04-14 session)

| Package | Change | Commit |
|---|---|---|
| lucide-react | 0.577 → 1.8 (major) | a98f485 |
| jsdom | 28 → 29 (major) | b55b643 |
| vite + @vitejs/plugin-react | 7 → 8 / 5 → 6 (major) | c262f55 |
| typescript | 5.9 → 6.0 (major) | e439e96 |
| softprops/action-gh-release | v2 → v3 (PR #31) | 9e94c18 |
| neo4j driver | 5.28 → 6.1 (major) | 30f0c2f |
| langgraph | 0.6 → 1.1 (major) | dac3d80 |

---

## Queued — ready to execute when scheduled

### Python runtime 3.11 → 3.12

**Current state:** Python 3.11 pinned in `src/mcp/Dockerfile`, `.github/workflows/ci.yml`
(container image `python:3.11-slim`, `actions/setup-python`), `pyproject.toml`
(`requires-python = ">=3.11"`, `python_version = "3.11"` for mypy), and called out in
`CLAUDE.md` + `docs/DEPENDENCY_COUPLING.md`.

**Driver:** preventive hygiene. Python 3.11 EOL is October 2027 — no urgency, but
compounding perf (3.12 ≈ +5% over 3.11; 3.13 adds JIT + opt-in free-threading) is
relevant for our async FastAPI + ONNX workloads.

**Why 3.12 before 3.14:** dependabot proposed `3.11.14-slim → 3.14.0-slim`, skipping two
majors. Step the upgrade to surface any dep-specific breakage incrementally.

**Files to touch (all coordinated, single PR):**
- `src/mcp/Dockerfile` — base image `python:3.11-slim` → `python:3.12-slim`
- `.github/workflows/ci.yml` — every `container: python:3.11-slim` reference; every
  `actions/setup-python` invocation if version is pinned
- `pyproject.toml` — `requires-python = ">=3.12"`, `[tool.mypy] python_version = "3.12"`
- `docs/DEPENDENCY_COUPLING.md` — the "Python 3.11 (Dockerfile + CI + pyproject.toml)"
  callout
- `CLAUDE.md` — version callout
- `src/mcp/requirements.lock` — regenerated under the new image (hashes will all shift)
- `packages/desktop/` — any Python-bundled hooks (if applicable)

**Validation checklist:**
1. Rebuild Docker image cleanly under `python:3.12-slim`.
2. Run full pytest suite in the new container (expect 2374 pass, same as today).
3. Run `mypy src/mcp/` under 3.12 (type stubs sometimes drift).
4. Run `import-linter` to confirm boundary contracts unchanged.
5. Run `scripts/validate-env.sh` with a rebuilt MCP container.
6. Smoke: ingest a test artifact, query it, assert Neo4j + Chroma write succeeded.
7. Confirm ONNX Runtime and FastEmbed sidecar load cleanly on 3.12.

**Risks to watch:**
- `torch` / `sentence-transformers` native wheel availability on 3.12 (usually fine).
- `chromadb==0.5.23` wheel availability on 3.12 (stays client-pinned).
- Any deprecation warnings from the `asyncio` / `typing` stdlib surface we depend on.

---

### ChromaDB 0.5 → 1.x (client + server)

**Blocker:** client is pinned to `<0.6` because the server (deployed in
`stacks/infrastructure/`) runs 0.5.23. Client/server must match for the wire protocol.

**Unblock order:** server upgrade first (Docker image bump in
`stacks/infrastructure/docker-compose.yml`), then widen client constraint in
`requirements.txt`, then regenerate lock.

**Validation:** snapshot-restore the KB first (`scripts/backup-kb.sh`) — the 0.x → 1.x
collection format migrated.

---

### ESLint 9 → 10

**Blocker:** `eslint-plugin-react-hooks` latest stable (v7.x) peer-requires
`eslint <= 9`. Only canary releases of react-hooks support ESLint 10.

**Ignored** in `.github/dependabot.yml` until a stable react-hooks v8 ships with
`eslint: ^10` peer support. Watch:
<https://github.com/facebook/react/tree/main/packages/eslint-plugin-react-hooks>

When unblocked, the upgrade itself is straightforward — our config is already flat
(`eslint.config.js`), so the main ESLint 10 breaking change (eslintrc support removed)
is a non-event.

---

### Python runtime 3.12 → 3.13

Deferred beyond 3.12. 3.13 unlocks the opt-in free-threaded build (no-GIL) and the JIT;
evaluate once 3.12 lands and the dep tree shows stable 3.13 wheel coverage.
