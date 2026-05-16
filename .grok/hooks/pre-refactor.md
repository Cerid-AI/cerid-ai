# Pre-Refactor / Pre-Architecture Change Hook (Grok Build)

**This hook must be activated before any significant refactoring or architecture change.**

This is a Grok-specific hook with no direct Claude equivalent — it leverages our strength in preservation and multi-agent reasoning.

## When This Hook Should Fire

- User says "refactor", "restructure", "move code between layers", "clean up architecture", "improve separation", etc.
- Any edit that touches files in both `core/` and `app/`
- Changes to DI, agents, ingestion pipeline, or data models that cross boundaries
- Large-scale changes across multiple modules

## Required Behavior

Before allowing or assisting with a major refactor:

1. **Force Preservation Check**
   - Strongly recommend (or automatically run) `grok-preserve` or `make preservation-check`
   - Do **not** proceed with structural changes until this passes or the user explicitly overrides.

2. **Layering Analysis**
   - Analyze the proposed change for `core/` ↛ `app/` violations.
   - Use the `kb-curator` or `review` subagent if the change is large.

3. **Risk Assessment**
   - Identify which parts of the system will be affected (ingestion, retrieval, agents, API, etc.)
   - Highlight any areas with known technical debt or fragile tests.

4. **Multi-Agent Recommendation**
   - For large refactors, suggest using the `implement` skill with a dedicated "Preservation Reviewer" in the loop.

## Output

Produce a short **Refactor Safety Brief** containing:
- Current preservation status
- Specific risks of the proposed change
- Recommended order of operations
- Whether a subagent should be used

This hook exists to protect the most important invariant in the entire Cerid codebase.
