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

