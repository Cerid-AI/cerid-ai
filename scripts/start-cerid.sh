#!/bin/bash
# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Cerid AI - Startup Script
#
# Usage:
#   ./scripts/start-cerid.sh           # start all services
#   ./scripts/start-cerid.sh --build   # rebuild images before starting (after code changes)

set -euo pipefail
CERID_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$CERID_ROOT/.env"

BUILD_FLAG=""
for arg in "$@"; do
    case "$arg" in
        --build) BUILD_FLAG="--build" ;;
        *)
            echo "Unknown option: $arg"
            echo "Usage: $0 [--build]"
            exit 1
            ;;
    esac
done

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

# Ensure archive directory exists (MCP mounts it read-only)
ARCHIVE_DIR="${HOME}/cerid-archive"
if [ ! -d "$ARCHIVE_DIR" ] && [ ! -L "$ARCHIVE_DIR" ]; then
    echo "[setup] Creating archive directory at $ARCHIVE_DIR"
    mkdir -p "$ARCHIVE_DIR"/{coding,finance,projects,personal,general,inbox}
fi

# Detect LAN IP for remote access (iPad, other machines)
if [ -z "${CERID_HOST:-}" ]; then
    LAN_IP=$(ipconfig getifaddr en0 2>/dev/null || ip -4 addr show scope global 2>/dev/null | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | head -1 || echo "")
    if [ -n "$LAN_IP" ]; then
        export CERID_HOST="$LAN_IP"
    else
        export CERID_HOST="localhost"
    fi
fi
echo "[net] CERID_HOST=$CERID_HOST"

# Set runtime URLs for web container based on CERID_HOST
export VITE_MCP_URL="http://${CERID_HOST}:8888"

# Ensure network exists
docker network create llm-network 2>/dev/null || true

if [ -n "$BUILD_FLAG" ]; then
    echo "[build] Rebuilding images with local Dockerfiles..."
fi

# Start in dependency order — all stacks read .env from repo root
echo "[1/5] Starting Infrastructure (Neo4j, ChromaDB, Redis)..."
docker compose -f "$CERID_ROOT/stacks/infrastructure/docker-compose.yml" --env-file "$ENV_FILE" up -d

echo "[2/5] Starting Bifrost (LLM Gateway)..."
docker compose -f "$CERID_ROOT/stacks/bifrost/docker-compose.yml" --env-file "$ENV_FILE" up -d

echo "[3/5] Starting MCP Services (MCP + Dashboard)..."
docker compose -f "$CERID_ROOT/src/mcp/docker-compose.yml" --env-file "$ENV_FILE" up -d $BUILD_FLAG

echo "[4/5] Starting React GUI..."
docker compose -f "$CERID_ROOT/src/web/docker-compose.yml" --env-file "$ENV_FILE" up -d $BUILD_FLAG

echo "[5/5] Starting LibreChat..."
docker compose -f "$CERID_ROOT/stacks/librechat/docker-compose.yml" --env-file "$ENV_FILE" up -d

# Optional: Caddy reverse proxy for local HTTPS
GATEWAY_ENABLED=$(grep -s '^CERID_GATEWAY=true' "$ENV_FILE" 2>/dev/null || echo "")
if [ -n "${GATEWAY_ENABLED}" ] || [ "${CERID_GATEWAY:-}" = "true" ]; then
    echo "[6/6] Starting Caddy Gateway (HTTPS)..."
    docker compose -f "$CERID_ROOT/stacks/gateway/docker-compose.yml" --env-file "$ENV_FILE" up -d
fi

# Optional: Cloudflare Tunnel for public demos
TUNNEL_TOKEN=$(grep -s '^CLOUDFLARE_TUNNEL_TOKEN=' "$ENV_FILE" 2>/dev/null | cut -d'=' -f2- || echo "")
if [ -n "${TUNNEL_TOKEN}" ] && [ "${TUNNEL_TOKEN}" != "" ]; then
    echo "[7/7] Starting Cloudflare Tunnel..."
    docker compose -f "$CERID_ROOT/stacks/tunnel/docker-compose.yml" --env-file "$ENV_FILE" up -d
fi

echo ""
echo "Waiting 30s for services to initialize..."
sleep 30

echo ""
echo "=== Service Status ==="
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

echo ""
echo "=== Quick Health Check ==="
echo -n "MCP:       " && curl -s http://localhost:8888/health | grep -o '"status":"[^"]*"' || echo "FAILED"
echo -n "React GUI: " && curl -s -o /dev/null -w "%{http_code}" http://localhost:3000 && echo " OK" || echo "FAILED"
echo -n "LibreChat: " && curl -s -o /dev/null -w "%{http_code}" http://localhost:3080 && echo " OK" || echo "FAILED"
echo -n "Bifrost:   " && curl -s -o /dev/null -w "%{http_code}" http://localhost:8080 && echo " OK" || echo "FAILED"
echo -n "Dashboard: " && curl -s -o /dev/null -w "%{http_code}" http://localhost:8501/_stcore/health && echo " OK" || echo "FAILED"
echo -n "Neo4j:     " && curl -s -o /dev/null -w "%{http_code}" http://localhost:7474 && echo " OK" || echo "FAILED"
echo -n "ChromaDB:  " && curl -s -o /dev/null -w "%{http_code}" http://localhost:8001/api/v1/heartbeat && echo " OK" || echo "FAILED"
echo -n "Redis:     " && redis-cli -p 6379 ping 2>/dev/null || echo "FAILED"

echo ""
echo "=== Access URLs ==="
echo "React GUI: http://localhost:3000  (primary)"
echo "LibreChat: http://localhost:3080"
echo "Dashboard: http://localhost:8501"
echo "MCP Docs:  http://localhost:8888/docs"
echo "Bifrost:   http://localhost:8080"
if [ "$CERID_HOST" != "localhost" ]; then
    echo ""
    echo "=== LAN Access (iPad / other devices) ==="
    echo "React GUI: http://${CERID_HOST}:3000"
    echo "MCP API:   http://${CERID_HOST}:8888"
    if [ -n "${GATEWAY_ENABLED}" ] || [ "${CERID_GATEWAY:-}" = "true" ]; then
        echo "HTTPS:     https://${CERID_HOST} (Caddy, self-signed cert)"
    fi
fi
