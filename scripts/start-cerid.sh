#!/bin/bash
# Cerid AI - Startup Script

echo "=== Starting Cerid AI Stack ==="

# Ensure network exists
docker network create llm-network 2>/dev/null || true

# Start in dependency order
echo "[1/3] Starting Bifrost (LLM Gateway)..."
cd ~/cerid-ai/stacks/bifrost && docker compose up -d

echo "[2/3] Starting MCP Services..."
cd ~/cerid-ai/src/mcp && docker compose up -d

echo "[3/3] Starting LibreChat..."
cd ~/cerid-ai/stacks/librechat && docker compose up -d

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

echo ""
echo "=== Access URLs ==="
echo "LibreChat: http://localhost:3080"
echo "MCP Docs:  http://localhost:8888/docs"
echo "Bifrost:   http://localhost:8080"
