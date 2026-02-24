#!/bin/bash
# .claude/hooks/session-start.sh
# SessionStart hook: validates Docker stack health on session startup
# Outputs status summary for Claude context

cd "$CLAUDE_PROJECT_DIR" || exit 0

echo "=== Cerid AI Stack Status ==="

# Quick Docker container check (non-blocking)
if command -v docker &>/dev/null; then
  RUNNING=$(docker ps --format '{{.Names}}' 2>/dev/null | grep -c -E 'mcp|bifrost|neo4j|chroma|redis' || true)
  EXPECTED=5
  if [ "$RUNNING" -ge "$EXPECTED" ]; then
    echo "Docker: $RUNNING/$EXPECTED core services running"
  else
    echo "Docker: $RUNNING/$EXPECTED core services running (some may need starting)"
    echo "Run: ./scripts/start-cerid.sh"
  fi
else
  echo "Docker: not available"
fi

# MCP health check (fast, non-blocking)
if curl -sf --max-time 2 http://localhost:8888/health >/dev/null 2>&1; then
  echo "MCP Server: healthy (port 8888)"
else
  echo "MCP Server: not responding (port 8888)"
fi

# React GUI check
if curl -sf --max-time 2 http://localhost:3000 >/dev/null 2>&1; then
  echo "React GUI: healthy (port 3000)"
else
  echo "React GUI: not responding (port 3000)"
fi

echo "=== End Status ==="

# Always succeed — don't block session start
exit 0
