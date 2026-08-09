# Across Cerid (Grok Build Command)

Quick entry point for cross-repository operations on the Cerid family.

**Recommended usage:**
- `status` or no argument → High-level health across all six repos (git state, dirty files, open todos)
- `pull` → Fetch + fast-forward where safe
- `typecheck` → Run type checking across Python and TS repos
- `todos` → Summarize open work items across the family
- `diff` → Compare cerid-ai-internal vs public mirror

This command is a thin, convenient wrapper around the powerful `grok-across-cerid` skill.

For complex multi-repo work, prefer invoking the `grok-across-cerid` skill directly so it can intelligently fork subagents.
