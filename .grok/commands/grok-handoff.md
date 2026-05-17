# Smart Handoff Generator (Grok → Claude Code) — Autonomous

**This is a high-autonomy command.** When the user asks to hand off to Claude Code, you should actively gather rich context by executing relevant sub-commands and skills, then produce a high-quality `HANDOFF.md`.

## Autonomous Execution Protocol

When `grok-handoff` is invoked (optionally with a reason), you should:

### 1. Git & Work Context
- Run git status, recent commits, dirty files, branch info
- Check `tasks/todo.md` for open items
- Check `tasks/lessons.md` for recent insights

### 2. Architecture & Preservation Snapshot
- Run `grok-preserve`
- Consider forking `grok-preservation-guard` if architecture work was recently done

### 3. Cross-Repo Context (if relevant)
- Run `grok-across-cerid status` (or a scoped version) to understand family-wide state

### 4. Tooling & Environment
- Run `grok-quenchforge status` (local inference situation)
- Run `grok-mcp-audit` or manually note active MCPs
- Note which skills have been heavily used in this session

### 5. Session Health
- Run or simulate `grok-session-audit` to capture current context state and any relevant history

### 6. Generate HANDOFF.md

After collecting the above, create or update `HANDOFF.md` in the project root with excellent structure:

- Current Goal
- Completed Work (last changes + summaries)
- Open Questions / Risks
- Git State Summary
- Architecture & Preservation Snapshot
- Local Inference / Quenchforge Status
- Active Tools & MCPs
- Key Skills / Agents used recently
- Recommended First Steps for Claude Code
- Special Context (anything the receiving agent should know immediately)

**Always** ask the user for any additional personal notes they want added before finalizing the handoff document.

This command dramatically reduces context loss when switching between Grok Build and Claude Code.
