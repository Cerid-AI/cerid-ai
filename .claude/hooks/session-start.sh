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

# Global plugin check
echo ""
echo "=== Global Tooling ==="
MISSING_PLUGINS=()
REQUIRED_PLUGINS=(
  "superpowers@claude-plugins-official"
  "pyright-lsp@claude-plugins-official"
  "code-simplifier@claude-plugins-official"
  "claude-md-management@claude-plugins-official"
  "claude-code-setup@claude-plugins-official"
  "frontend-design@claude-plugins-official"
)

if [ -f "$HOME/.claude/settings.json" ]; then
  for plugin in "${REQUIRED_PLUGINS[@]}"; do
    if ! grep -q "$plugin" "$HOME/.claude/settings.json" 2>/dev/null; then
      MISSING_PLUGINS+=("$plugin")
    fi
  done
fi

if [ ${#MISSING_PLUGINS[@]} -eq 0 ]; then
  echo "Plugins: all ${#REQUIRED_PLUGINS[@]} required plugins installed"
else
  echo "⚠️  Missing plugins (run: bash ~/dotfiles/install.sh):"
  for p in "${MISSING_PLUGINS[@]}"; do
    echo "   - $p"
  done
fi

# Global MCP server check
MISSING_MCP=()
CLAUDE_JSON="$HOME/.claude.json"
if [ -f "$CLAUDE_JSON" ]; then
  for server in context7 github-mcp; do
    if ! grep -q "\"$server\"" "$CLAUDE_JSON" 2>/dev/null; then
      MISSING_MCP+=("$server")
    fi
  done
fi

if [ ${#MISSING_MCP[@]} -eq 0 ]; then
  echo "MCP servers: context7 + github-mcp installed"
else
  echo "⚠️  Missing MCP servers (run: bash ~/dotfiles/install.sh):"
  for m in "${MISSING_MCP[@]}"; do
    echo "   - $m"
  done
fi

echo "=== End Status ==="

# Always succeed — don't block session start
exit 0
