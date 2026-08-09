# MCP Audit (Grok Build)

Audit the current MCP surface for relevance, health, and token cost.

**What this command should do:**

1. List all currently configured MCP servers (global + project-level).
2. Check which ones are actually responding / healthy.
3. Estimate token cost of their tool schemas.
4. Recommend which MCPs can be safely ignored or disabled for the current phase of work.
5. Flag any mis-scoped MCPs (e.g. HA tools leaking into non-HA repos).

This is the Grok equivalent of Claude’s MCP auditing practices and pairs well with `grok-session-audit`.

Run this periodically during long sessions or when context feels heavy due to many tool descriptions.
