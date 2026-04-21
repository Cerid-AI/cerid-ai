# Claude Code Setup - Cerid AI

> Detailed setup instructions for Claude Code in this project. For project patterns and architecture, see the root `CLAUDE.md`.

## Prerequisites

- Docker running
- `.env` decrypted (`./scripts/env-unlock.sh`)
- `age` installed (`brew install age`)
- Archive directory exists (`~/cerid-archive/`)

## Project Setup

1. **Verify prerequisites:** Docker running, `.env` decrypted, `age` installed, archive directory exists
2. **Install recommended plugins** (see Global Plugins Required below)
3. **Run `./scripts/validate-env.sh`** to check all 14 environment validations
4. **If containers are down:** `./scripts/start-cerid.sh` (or `--build` after a `git pull`)

## Project-Level Config (committed, auto-applied)

| File | Purpose |
|------|---------|
| `.mcp.json` | Cerid KB MCP at `http://localhost:8888/mcp/sse` (26 `pkb_*` tools) -- Claude Code runs on the host so `localhost` is correct here |
| `.claude/settings.json` | Hooks config (session-start, safety-check, typecheck, pythonlint) |
| `.claude/hooks/session-start.sh` | SessionStart -- Docker + MCP + GUI health check |
| `.claude/hooks/safety-check.sh` | PreToolUse/Bash -- blocks destructive commands |
| `.claude/hooks/typecheck.sh` | PostToolUse/Edit\|Write -- `npx tsc --noEmit` for `.ts`/`.tsx` in `src/web/` |
| `.claude/hooks/pythonlint.sh` | PostToolUse/Edit\|Write -- `ruff check` for `.py` in `src/mcp/` |
| `.claude/commands/` | Custom commands: stack, test, sync, lock |
| `.claude/launch.json` | Dev server configs (cerid-web, react-gui, marketing) |
| `.claude/agents/kb-curator.md` | Opus subagent for KB schema-aware curation, dedup, and cross-store consistency checks |
| `.claudeignore` | Excludes node_modules, dist, runtime data, binaries, lock files |

## Per-Machine Config (gitignored)

| File | Purpose |
|------|---------|
| `.claude/settings.local.json` | Bash permission allowlists (auto-populated as you approve commands) |

## Global Plugins Required

Key plugins for this project:

- `superpowers` -- plan execution, TDD, code review, debugging workflows
- `pyright-lsp` -- Python type checking
- `frontend-design` -- React GUI development
- `claude-md-management` -- CLAUDE.md maintenance

## Global MCP Servers Required

- **context7** -- live docs for React, FastAPI, pydantic, ChromaDB, Neo4j
- **github-mcp** -- GitHub issues, PRs, actions

## Running Tests

**Python tests** (run in Docker since host macOS lacks chromadb):
```bash
docker run --rm -v "$(pwd)/src/mcp:/work" -w /work python:3.11-slim bash -c "pip install -q -r requirements.txt -r requirements-dev.txt && python -m pytest tests/ -v"
```

**Frontend tests:**
```bash
cd src/web && npx vitest run
```

## Session Start

Before beginning any development work, if not already done in this session:

1. Run `./scripts/validate-env.sh --quick` at the beginning of every session
2. Never add AI attribution to commits
4. If the session-start hook reports missing plugins or MCP servers, install them before proceeding
