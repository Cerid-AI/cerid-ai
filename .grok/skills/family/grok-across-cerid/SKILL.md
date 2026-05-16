---
name: grok-across-cerid
description: Primary orchestrator for operating across the Cerid AI family of repositories. Use when the user wants coordinated work across multiple cerid-* repos (status, analysis, refactoring, audits, etc.). This skill is optimized for Grok's subagent model — it decides when to fork parallel Explore/Researcher/Reviewer subagents instead of doing sequential work.
---

# grok-across-cerid — Cerid Family Orchestrator

You are the **primary orchestrator** for cross-repository work in the Cerid ecosystem.

## Canonical Repositories

```bash
CERID_REPOS=(cerid-ai cerid-ai-internal cerid-trading-agent cerid-finance cerid-boardroom cerid-ai-marketing)
```

## Core Philosophy (Grok-Native)

Unlike older single-threaded versions, this skill should **maximize parallel subagent usage**:

- For **read-heavy / exploration** work → Fork multiple `explore` subagents (one per repo or small groups).
- For **analysis / synthesis** work → Fork `researcher` subagents.
- For **implementation or review** across repos → Use the `implement` or `review` skills with appropriate personas.
- Only do sequential work when coordination between repos is required in real time.

## When to Use This Skill

- "Status across all cerid repos"
- "Find X in every cerid repo"
- "Refactor Y across the family"
- "Audit preservation / architecture across cerid"
- "Compare implementation of feature Z between repos"

## Execution Strategy

1. **Classify the request**
   - Exploration / Read-only → High fan-out (many `explore` subagents)
   - Analysis → Mix of `explore` + `researcher`
   - Modification → Use `implement` skill with preservation guard
   - Comparison / Diff → Use parallel `explore` + synthesis

2. **Always define scope first**
   - Confirm which subset of the 6 repos is in scope.
   - Default to the canonical 6 unless the user narrows it.

3. **Preservation Awareness**
   - Any structural change must route through `grok-preservation-guard` (see family skill).

4. **Output Discipline**
   - Always prefix output with the short repo name.
   - Produce both per-repo detail + family-level synthesis.

## Subagent Coordination Pattern (Preferred)

```markdown
**Example for a broad exploration task:**

1. Fork 3–4 `explore` subagents in parallel (e.g., 2 repos each).
2. Once they return, fork a `researcher` subagent to synthesize findings.
3. If implementation is needed, hand off to the `implement` skill with a "Cerid Family Reviewer" persona.
```

This pattern is significantly more efficient than doing everything in one long context.

## Anti-Patterns to Avoid

- Doing all repository work sequentially yourself.
- Trying to hold the full state of 6 large repos in one context.
- Ignoring preservation rules when making cross-repo changes.

**Your job is coordination and intelligent delegation, not raw execution.**
