# Pre-Large Change Hook (Grok Build)

**Activate this before any large, multi-file, or high-impact edit.**

This is a general-purpose Grok safety hook.

## Triggers

- Editing >5–7 files in one session
- Touching core business logic, data models, or agent systems
- Cross-cutting changes (e.g. logging, error handling, auth, config)
- User says "big change", "major update", "refactor this area", etc.

## Recommended Protocol

1. **Pause and Assess Scope**
   - Ask: Is this change better broken into smaller, reviewable pieces?
   - Consider using the `implement` skill instead of direct editing.

2. **Run Relevant Guards**
   - `grok-preserve` (if architecture involved)
   - Relevant lint/typecheck (`grok-test`, `pythonlint`, `typecheck`, etc.)
   - `grok-kb-curate` if touching ingestion or KB code

3. **Subagent Recommendation**
   - For complex changes, strongly consider forking a Reviewer or using the full `implement` loop with multiple specialized reviewers.

4. **Risk Documentation**
   - Document the blast radius before starting.

This hook helps Grok avoid the common failure mode of over-editing in one go without proper verification.
