# Session Start Hook (Grok Build)

**Run this at the beginning of every new Grok Build session in cerid-ai family repos.**

This hook replaces and improves upon the Claude `session-start.sh`.

## Actions to Perform

### 1. Docker / Core Services Health
```bash
echo "=== Cerid AI Stack Status ==="
if command -v docker >/dev/null 2>&1; then
  RUNNING=$(docker ps --format '{{.Names}}' 2>/dev/null | grep -c -E 'mcp|bifrost|neo4j|chroma|redis' || true)
  echo "Docker services running: $RUNNING / 5 expected"
  if [ "$RUNNING" -lt 5 ]; then
    echo "⚠️  Some services may be down. Consider running: ./scripts/start-cerid.sh"
  fi
else
  echo "Docker not available in this shell"
fi
```

### 2. Knowledge Base MCP Health
```bash
if curl -sf --max-time 2 http://localhost:8888/health >/dev/null 2>&1; then
  echo "✓ cerid-kb MCP healthy (port 8888)"
else
  echo "✗ cerid-kb MCP not responding — RAG will be degraded"
fi
```

### 3. Preservation Reminder (Cerid-specific)
```bash
echo ""
echo "=== Architecture Reminder ==="
echo "Remember: core/ must never import from app/. Run 'grok-preserve' or 'make preservation-check' before structural changes."
```

### 4. Quenchforge / Local Models (if relevant)
```bash
echo ""
echo "Check local inference status with 'grok-quenchforge status' if doing heavy embedding or agent work."
```

### 5. Recommended First Actions
After running the above, the agent should:
- Summarize the current health of the environment
- Note any degraded capabilities (e.g. KB down, Docker services down)
- Ask the user what they want to work on today

**This hook should run automatically in spirit at the start of every session.**
