---
name: grok-audit-context
description: Use when the user asks what's loaded in the current Grok Build session, why context feels heavy, how many tokens subagents are using, or how to reduce overhead. Covers Grok-specific concepts: injected system prompt size, available skills (from ~/.grok/skills + project .grok/skills + bundled), active MCP servers (grok_com_github etc.), subagent types (implement, review, explore, plan, best-of-n), and the workspace path. Recommends trims. Complements (does not duplicate) the Claude `audit-context` skill.
---

# Grok Build Context Audit

Inspect the current Grok Build / Grok Heavy session for context bloat and recommend targeted reductions.

Grok Build has a different loading model than Claude Code:
- Large system prompt injected on every turn (includes full tool schemas + skill metadata).
- Subagents (implement, review, explore, plan, design, best-of-n) each carry their own persona + instructions.
- Skills are discovered from multiple locations and listed in the system reminder.
- MCP servers appear as tools when connected.

## What to Capture

### 1. Workspace & Identity
```bash
echo "Workspace Path (from runtime): $PWD"
echo "Effective develop root: /develop or /Users/sunrunner/Develop"
echo "Dotfiles: ~/dotfiles (source of truth for GROK.md + skills)"
```

### 2. Skills visible to this session
Look at the system-reminder block that lists "Available skills". Count them and group by source:

- Bundled skills (`~/.grok/bundled/skills/`) — implement, review, best-of-n, design, pr-babysit, etc. (heavy but very capable)
- User skills (`~/.grok/skills/`)
- Project skills (`<cwd>/.grok/skills/` or walking up to `~/Develop/.grok/skills/`)
- Any `.claude/skills/` that the environment also surfaced (hybrid visibility)

Report rough token cost estimate: number of skills × 60–120 tokens each at session start.

### 3. Active MCP servers
```bash
# Grok Build surfaces connected MCPs in the system prompt / available tools
# Look for "grok_com_github" and any project-specific ones (cerid-kb, ha-*, etc.)
```

Note which ones are global vs project-scoped.

### 4. Subagent overhead
Grok Build's power comes from subagents. Each one carries:
- Full persona prompt (planner, coder, reviewer, researcher, preservation guard, etc.)
- Access to the same tool set + skills

When the user has run `implement`, `review`, or multiple Explore agents, context can grow quickly because each subagent maintains its own history.

### 5. Current working directory scope
Confirm we are under `/develop` (or `/Users/sunrunner/Develop`).

If the user is working in a single cerid-* repo, having the full set of Develop-scoped skills + all cerid MCPs loaded may be unnecessary.

## Recommendations (Grok-flavored)

1. **Disable unused bundled skills** (if the user rarely uses `design`, `pr-babysit`, `best-of-n`, etc.). These are the heaviest.
2. **Prefer project-scoped skills** over global ones for cerid-specific or HA-specific workflows.
3. **Use `implement` skill only when the task genuinely benefits from the full reviewer loop** — it is powerful but expensive.
4. **For pure exploration**, use a single `explore` subagent or manual parallel tool calls instead of forking many Explore agents.
5. **MCP scoping**: If `cerid-kb` or HA tools are appearing in unrelated cerid repos, check for a leaky parent `.grok/config.toml` or `.mcp.json`.

## Output Format

Produce a clear markdown report with:
- Current workspace
- Breakdown of loaded skills by source + rough token estimate
- Active MCP servers
- Subagent usage pattern observed in this session
- Concrete, ordered trim recommendations (e.g. "move X skill from global to project", "disable bundled skill Y in ~/.grok/config.toml", "add .grokignore equivalent if one exists")

Do **not** automatically edit config files — present the diffs and let the user approve.

## Out of Scope

- Does not analyze the user's application code performance.
- Does not touch Claude Code session state (use the Claude `audit-context` skill for that).
