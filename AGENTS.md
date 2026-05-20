# AGENTS.md — Grok Heavy Environment (Cerid-AI Family)

**This file is the canonical instruction set for Grok 4 Heavy, Claude Code, and the official Grok Build CLI.**

## Project Landscape
- **Primary development**: `cerid-ai-internal` (canonical)
- **Public distribution**: `cerid-ai`
- **Supporting projects**: `quenchforge`, trading-agent, etc.
- **Dotfiles**: `~/dotfiles` (shell, LaunchAgents, Claude hooks, skills)

## Grok 4 Heavy Multi-Agent Protocol (Always Use)
- **Planner** — Task decomposition, preservation check, phase planning
- **Coder** — Implementation respecting core/app split, Docker-first, Quenchforge routing
- **Reviewer** — Security, silent-catch, design-drift, lint, preservation invariants
- **Researcher** — Real-time knowledge + MCP tools
- **MCP Orchestrator** — Coordinates cerid-kb + local MCP servers
- **Preservation Guard** — Ensures `make preservation-check` passes on architecture changes

## Critical Conventions (from your internal repo + dotfiles)
- `core/` must never import from `app/` (import-linter enforced)
- Use `log_swallowed_error()` for broad catches
- Run `make preservation-check` before any major core/app changes
- Prefer Quenchforge for local inference (GPU-aware)
- No AI attribution in commits (human-authored only)
- Respect `.mcp.json` governance (allowlist + audit)

## Multi-Agent Workflow Rules (graduated from `cerid-ai-internal/tasks/lessons.md` 2026-05-19)

These rules govern how each agent role behaves regardless of which CLI surface (Grok Heavy, Claude Code, Grok Build) is driving:

1. **Subagent reports describe local state, not the merged tree.** After a multi-agent swarm, re-run the full verification sweep against the combined working tree. Each agent's "tests pass" was true inside their container/worktree; the merge can break across them. Integration testing is mandatory; trust but verify.
2. **Verify subagent line-number citations before acting on them.** Citations are often hallucinated by 10-50 lines, or reference fabricated symbols. Budget ~30s per claim to `Read` the cited file at the cited line. Treat citations as hypotheses, not facts.
3. **Validate "open" items in docs against grep before triaging.** `tasks/todo.md`, `docs/ROADMAP.md` drift faster than code. Grep for the missing symbol / endpoint / count before acting on a backlog claim. Stale items waste full sprints rebuilding shipped features.
4. **Background-task output buffering is not "still running".** A Python process redirected to a file with default block buffering can appear hung when it's actually done — the buffer never flushed. Check both (a) the log file AND (b) whether the PID is alive AND (c) whether a task-notification has fired. Use `python -u` for all backgrounded test/eval runs.
5. **`git add -u` for gitignored-but-tracked files.** When `git status` shows `tasks/todo.md` as modified but `git add tasks/todo.md` errors with "paths are ignored", use `git add -u <path>` (update-tracked mode) — NOT `git add -f` (force-add, risks staging a genuinely-gitignored neighbour).

## Tooling & MCP Usage
- Primary MCP: `cerid-kb` at `http://localhost:8888/mcp/sse`
- Local MCPs: filesystem, github, browser (via `start-mcps.sh`)
- Respect `~/dotfiles` for shell aliases, Docker, LaunchAgents, Claude hooks

## Handoff Protocol (Grok Heavy ↔ Claude Code)
When switching agents, **always** create a `HANDOFF.md` file containing:

- **Current Goal**
- **Completed Work** (files changed + key summaries)
- **Open Questions / Risks**
- **Git State** (branch, recent commits)
- **Next Preferred Agent**
- **Special Context** (e.g. Quenchforge status, preservation baseline)

## Daily Workflow Recommendation
1. Heavy reasoning & planning → grok.com (full multi-agent)
2. Terminal execution → official Grok CLI (when Intel support lands) or community bridge
3. IDE work → Cursor (best Grok support right now)
4. Start MCPs → `./start-mcps.sh`

## Commit Policy
Human only. No `Co-Authored-By` or AI mentions.

