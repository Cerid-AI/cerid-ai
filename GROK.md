# Grok Heavy Configuration — Cerid-AI Family

**Always activate Grok 4 Heavy multi-agent mode.**

**Roles**:
- Planner → Task breakdown + preservation check
- Coder → Implementation (core/app split, Docker-first, Quenchforge)
- Reviewer → Security, lint, design-drift, preservation
- Researcher + MCP Orchestrator → Local tools + real-time
- Preservation Guard → `make preservation-check`

**Respect**:
- core/ never imports app/
- Existing .mcp.json governance
- ~/dotfiles (aliases, LaunchAgents, shell)
- No AI attribution in commits

**Handoff**: Always produce HANDOFF.md when switching to Claude Code.
