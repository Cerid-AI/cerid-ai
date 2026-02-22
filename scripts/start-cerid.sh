#!/bin/bash
# Cerid AI - Startup Script

set -euo pipefail
CERID_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$CERID_ROOT/.env"

echo "=== Starting Cerid AI Stack ==="

# Auto-decrypt .env if missing but .env.age exists
if [ ! -f "$ENV_FILE" ] && [ -f "$CERID_ROOT/.env.age" ]; then
    echo "[env] Decrypting secrets..."
    "$CERID_ROOT/scripts/env-unlock.sh"
fi

if [ ! -f "$ENV_FILE" ]; then
    echo "Error: $ENV_FILE not found. Copy .env.example → .env and fill in values."
    exit 1
fi

# Ensure network exists
docker network create llm-network 2>/dev/null || true

# Start in dependency order — all stacks read .env from repo root
echo "[1/3] Starting Bifrost (LLM Gateway)..."
docker compose -f "$CERID_ROOT/stacks/bifrost/docker-compose.yml" --env-file "$ENV_FILE" up -d

echo "[2/3] Starting MCP Services..."
docker compose -f "$CERID_ROOT/src/mcp/docker-compose.yml" --env-file "$ENV_FILE" up -d

echo "[3/3] Starting LibreChat..."
docker compose -f "$CERID_ROOT/stacks/librechat/docker-compose.yml" --env-file "$ENV_FILE" up -d

echo ""
echo "Waiting 30s for services to initialize..."
sleep 30

echo ""
echo "=== Service Status ==="
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

echo ""
echo "=== Quick Health Check ==="
echo -n "MCP:       " && curl -s http://localhost:8888/health | grep -o '"status":"[^"]*"' || echo "FAILED"
echo -n "LibreChat: " && curl -s -o /dev/null -w "%{http_code}" http://localhost:3080 && echo " OK" || echo "FAILED"
echo -n "Bifrost:   " && curl -s -o /dev/null -w "%{http_code}" http://localhost:8080 && echo " OK" || echo "FAILED"
echo -n "Dashboard: " && curl -s -o /dev/null -w "%{http_code}" http://localhost:8501/_stcore/health && echo " OK" || echo "FAILED"

echo ""
echo "=== Access URLs ==="
echo "LibreChat: http://localhost:3080"
echo "Dashboard: http://localhost:8501"
echo "MCP Docs:  http://localhost:8888/docs"
echo "Bifrost:   http://localhost:8080"
