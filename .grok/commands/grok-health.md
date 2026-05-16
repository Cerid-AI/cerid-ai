# Cerid Health Dashboard (Grok Build) — Autonomous Orchestrator

**This is a high-autonomy command.** When invoked, you should actively execute multiple sub-commands and skills to produce a comprehensive health report with minimal further user input.

## Autonomous Execution Protocol

When the user runs `grok-health`, you **must** perform the following steps (in roughly this order), using the available Grok commands and skills:

### Phase 1: Infrastructure Health
1. Run `grok-stack status` (or equivalent Docker/service checks)
2. Check `cerid-kb` MCP health (port 8888)
3. Check React GUI / key services if relevant

### Phase 2: Architecture & Preservation (Critical)
4. Run `grok-preserve`
5. If significant issues appear, consider forking `grok-preservation-guard` as a reviewer

### Phase 3: Cross-Repository Overview
6. Invoke `grok-across-cerid status` (or use it to fork parallel `explore` subagents across the family)
7. Summarize git health, dirty state, open todos, and drift across the six canonical repos

### Phase 4: Local Inference & Hardware
8. Run `grok-quenchforge status` (GPU, local models, routing)

### Phase 5: Session & Tooling Hygiene
9. Run `grok-session-audit` (or perform equivalent context/tool relevance check)
10. Run `grok-mcp-audit` (optional but recommended for long sessions)

### Phase 6: Synthesis & Recommendations
After gathering the above data, produce a clean, prioritized **Cerid Health Report** containing:

- Overall system status (Green / Yellow / Red)
- Infrastructure issues
- Architecture / Preservation risks
- Family-wide observations (from `grok-across-cerid`)
- Local model / Quenchforge utilization
- Context & tooling hygiene notes
- Top 5 recommended actions (ranked by importance)

This command is designed to give the user (and the agent) a senior-engineer-level briefing on the state of the entire Cerid development environment with one invocation.

**Default behavior**: Be proactive. Execute the phases above unless the user explicitly asks for a narrow scope.
