#!/bin/bash
# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Cerid AI - Startup Script
#
# Usage:
#   ./scripts/start-cerid.sh           # start all services (unified compose)
#   ./scripts/start-cerid.sh --build   # rebuild images before starting (after code changes)
#   ./scripts/start-cerid.sh --force   # bypass pre-flight checks
#   ./scripts/start-cerid.sh --legacy  # use legacy 4-step compose startup

set -euo pipefail
CERID_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$CERID_ROOT/.env"

BUILD_FLAG=""
FORCE_FLAG=""
LEGACY_FLAG=""
for arg in "$@"; do
    case "$arg" in
        --build) BUILD_FLAG="--build" ;;
        --force) FORCE_FLAG="1" ;;
        --legacy) LEGACY_FLAG="1" ;;
        *)
            echo "Unknown option: $arg"
            echo "Usage: $0 [--build] [--force] [--legacy]"
            exit 1
            ;;
    esac
done

echo "=== Starting Cerid AI Stack ==="

# Verify Docker Compose V2 is available
if ! docker compose version >/dev/null 2>&1; then
    echo "Error: Docker Compose V2 is required (docker compose, not docker-compose)."
    echo "Install: https://docs.docker.com/compose/install/"
    exit 1
fi

# Auto-decrypt .env if missing but .env.age exists
if [ ! -f "$ENV_FILE" ] && [ -f "$CERID_ROOT/.env.age" ]; then
    echo "[env] Decrypting secrets..."
    "$CERID_ROOT/scripts/env-unlock.sh"
fi

if [ ! -f "$ENV_FILE" ]; then
    echo "Error: $ENV_FILE not found. Copy .env.example → .env and fill in values."
    exit 1
fi

# --- Port configuration (27E) ---
# Export with defaults so compose files and health checks use consistent values.
# Override in .env or environment to avoid port conflicts.
export CERID_PORT_GUI="${CERID_PORT_GUI:-3000}"
export CERID_PORT_MCP="${CERID_PORT_MCP:-8888}"
export CERID_PORT_BIFROST="${CERID_PORT_BIFROST:-8080}"
export CERID_PORT_NEO4J="${CERID_PORT_NEO4J:-7474}"
export CERID_PORT_NEO4J_BOLT="${CERID_PORT_NEO4J_BOLT:-7687}"
export CERID_PORT_CHROMA="${CERID_PORT_CHROMA:-8001}"
export CERID_PORT_REDIS="${CERID_PORT_REDIS:-6379}"

# --- Pre-flight checks (27B) ---
preflight_checks() {
    local fail=0

    # Check required env vars are non-empty
    for var in NEO4J_PASSWORD OPENROUTER_API_KEY; do
        local val
        val=$(grep "^${var}=" "$ENV_FILE" 2>/dev/null | cut -d'=' -f2- || echo "")
        if [ -z "$val" ]; then
            echo "  ERROR: $var is empty in .env"
            fail=1
        fi
    done

    # Check port conflicts (skip ports owned by our containers)
    local our_containers="ai-companion-mcp|ai-companion-neo4j|ai-companion-chroma|ai-companion-redis|bifrost|cerid-web"
    check_port() {
        local port=$1 service=$2
        local pid
        pid=$(lsof -i ":$port" -sTCP:LISTEN -t 2>/dev/null | head -1 || echo "")
        if [ -n "$pid" ]; then
            local proc
            proc=$(ps -p "$pid" -o comm= 2>/dev/null || echo "unknown")
            # Skip if it's one of our own containers (com.docker.backend)
            local cid
            cid=$(docker ps --filter "publish=$port" --format '{{.Names}}' 2>/dev/null || echo "")
            if echo "$cid" | grep -qE "$our_containers" 2>/dev/null; then
                return 0  # Our container — not a conflict
            fi
            echo "  ERROR: Port $port ($service) in use by $proc (PID $pid)"
            echo "    Fix: Stop the process or set CERID_PORT_${service^^} in .env"
            fail=1
        fi
    }
    check_port "$CERID_PORT_GUI" "gui"
    check_port "$CERID_PORT_MCP" "mcp"
    check_port "$CERID_PORT_BIFROST" "bifrost"
    check_port "$CERID_PORT_NEO4J" "neo4j"
    check_port "$CERID_PORT_CHROMA" "chroma"

    # Disk space warning (Docker data root)
    local docker_root avail_gb
    docker_root=$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || echo "/var/lib/docker")
    avail_gb=$(df -g "$docker_root" 2>/dev/null | awk 'NR==2{print $4}' || echo "999")
    if [ "${avail_gb:-999}" -lt 2 ] 2>/dev/null; then
        echo "  WARNING: Low disk space (${avail_gb}GB free) on Docker data root"
    fi

    if [ "$fail" -ne 0 ]; then
        echo ""
        echo "Pre-flight checks failed. Fix the issues above or use --force to bypass."
        return 1
    fi
    return 0
}

if [ -z "$FORCE_FLAG" ]; then
    echo "[preflight] Running pre-flight checks..."
    if ! preflight_checks; then
        exit 1
    fi
    echo "[preflight] All checks passed"
else
    echo "[preflight] Skipped (--force)"
fi

# Ensure archive directory exists (MCP mounts it read-only)
ARCHIVE_DIR="${HOME}/cerid-archive"
if [ ! -d "$ARCHIVE_DIR" ] && [ ! -L "$ARCHIVE_DIR" ]; then
    echo "[setup] Creating archive directory at $ARCHIVE_DIR"
    mkdir -p "$ARCHIVE_DIR"/{coding,finance,projects,personal,general,inbox}
fi

# --- Detect LAN IP for remote access (iPad, other machines) ---
detect_lan_ip() {
    # macOS: try common interfaces, then scan all
    if command -v ipconfig &>/dev/null; then
        for iface in en0 en1 en2 en3 en4 en5; do
            local ip
            ip=$(ipconfig getifaddr "$iface" 2>/dev/null) && [ -n "$ip" ] && echo "$iface:$ip" && return
        done
        # Fallback: first routable IPv4 from ifconfig (skip loopback, link-local, Docker)
        local scan_ip
        scan_ip=$(ifconfig 2>/dev/null | grep 'inet ' | grep -v '127\.' | grep -v '169\.254\.' | grep -v '172\.17\.' | awk '{print $2}' | head -1)
        [ -n "$scan_ip" ] && echo "ifconfig-scan:$scan_ip" && return
    fi
    # Linux: hostname -I gives space-separated list of all IPs
    if command -v hostname &>/dev/null; then
        local linux_ip
        linux_ip=$(hostname -I 2>/dev/null | awk '{print $1}')
        [ -n "$linux_ip" ] && echo "hostname:$linux_ip" && return
    fi
    echo ""
}

if [ -z "${CERID_HOST:-}" ]; then
    _detected=$(detect_lan_ip)
    if [ -n "$_detected" ]; then
        _host_source="${_detected%%:*}"
        export CERID_HOST="${_detected#*:}"
    else
        _host_source="fallback"
        export CERID_HOST="localhost"
        echo "[net] WARNING: Could not detect LAN IP. LAN access will not work."
        echo "  Fix: Set CERID_HOST=<your-ip> in .env or export it before running this script."
    fi
else
    _host_source="env-override"
fi
echo "[net] CERID_HOST=$CERID_HOST (source: $_host_source)"

# When LAN access is enabled, bind to all interfaces
if [[ -n "$CERID_HOST" && "$CERID_HOST" != "localhost" && "$CERID_HOST" != "127.0.0.1" ]]; then
  export CERID_BIND_ADDR="${CERID_BIND_ADDR:-0.0.0.0}"
  echo "    LAN mode: binding to all interfaces (CERID_BIND_ADDR=$CERID_BIND_ADDR)"
fi

# Set runtime URLs for web container based on CERID_HOST
export VITE_MCP_URL="http://${CERID_HOST}:${CERID_PORT_MCP}"

# Force-recreate web container if VITE_MCP_URL changed (prevents stale IP bug)
WEB_RECREATE=""
CURRENT_MCP_URL=$(docker exec cerid-web cat /usr/share/nginx/html/env-config.js 2>/dev/null \
    | grep 'VITE_MCP_URL' | sed 's/.*"\(http[^"]*\)".*/\1/' || echo "")
if [ -n "$CURRENT_MCP_URL" ] && [ "$CURRENT_MCP_URL" != "$VITE_MCP_URL" ]; then
    echo "[net] MCP URL changed ($CURRENT_MCP_URL -> $VITE_MCP_URL), will recreate web container"
    WEB_RECREATE="--force-recreate"
fi

# Ensure network exists (only needed for legacy mode; unified compose creates it)
docker network create llm-network 2>/dev/null || true

if [ -n "$BUILD_FLAG" ]; then
    echo "[build] Rebuilding images with local Dockerfiles..."
fi

UNIFIED_COMPOSE="$CERID_ROOT/docker-compose.yml"

if [ -z "$LEGACY_FLAG" ] && [ -f "$UNIFIED_COMPOSE" ]; then
    # --- Unified compose: single command starts everything ---
    echo "[unified] Starting all services via root docker-compose.yml..."
    echo "  Startup order: Neo4j, ChromaDB, Redis → Bifrost → MCP → Web"
    echo "  (depends_on healthchecks enforce correct ordering)"
    docker compose -f "$UNIFIED_COMPOSE" --env-file "$ENV_FILE" up -d $BUILD_FLAG $WEB_RECREATE
else
    # --- Legacy 4-step startup (preserved for backward compatibility) ---
    echo "[legacy] Starting services in 4-step order..."
    echo "[1/4] Starting Infrastructure (Neo4j, ChromaDB, Redis)..."
    docker compose -f "$CERID_ROOT/stacks/infrastructure/docker-compose.yml" --env-file "$ENV_FILE" up -d

    echo "[2/4] Starting Bifrost (LLM Gateway)..."
    docker compose -f "$CERID_ROOT/stacks/bifrost/docker-compose.yml" --env-file "$ENV_FILE" up -d

    echo "[3/4] Starting MCP Server..."
    docker compose -f "$CERID_ROOT/src/mcp/docker-compose.yml" --env-file "$ENV_FILE" up -d $BUILD_FLAG

    echo "[4/4] Starting React GUI..."
    docker compose -f "$CERID_ROOT/src/web/docker-compose.yml" --env-file "$ENV_FILE" up -d $BUILD_FLAG $WEB_RECREATE
fi

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
echo "Waiting for services to initialize..."

wait_for_service() {
    local name="$1" url="$2" max_wait="${3:-60}" interval="${4:-3}"
    local elapsed=0
    while [ "$elapsed" -lt "$max_wait" ]; do
        if curl -sf -o /dev/null "$url" 2>/dev/null; then
            return 0
        fi
        sleep "$interval"
        elapsed=$((elapsed + interval))
    done
    return 1
}

CRITICAL_FAIL=0

echo -n "  Neo4j..."
wait_for_service "Neo4j" "http://127.0.0.1:${CERID_PORT_NEO4J}" 60 && echo " ready" || echo " timeout"
echo -n "  ChromaDB..."
wait_for_service "ChromaDB" "http://127.0.0.1:${CERID_PORT_CHROMA}/api/v1/heartbeat" 30 && echo " ready" || echo " timeout"
echo -n "  Bifrost..."
wait_for_service "Bifrost" "http://localhost:${CERID_PORT_BIFROST}/health" 30 && echo " ready" || { echo " timeout"; CRITICAL_FAIL=1; }
echo -n "  MCP..."
wait_for_service "MCP" "http://localhost:${CERID_PORT_MCP}/health" 90 && echo " ready" || { echo " timeout"; CRITICAL_FAIL=1; }
echo -n "  React GUI..."
wait_for_service "React GUI" "http://localhost:${CERID_PORT_GUI}" 60 && echo " ready" || { echo " timeout"; CRITICAL_FAIL=1; }

# Validate LAN reachability (the bug that prompted Phase 27)
if [ "$CERID_HOST" != "localhost" ]; then
    echo -n "  MCP (LAN)..."
    if wait_for_service "MCP-LAN" "$VITE_MCP_URL/health" 10 2; then
        echo " ready"
    else
        echo " UNREACHABLE"
        echo ""
        echo "  WARNING: MCP is healthy on localhost but not at $VITE_MCP_URL"
        echo "  The React GUI will show 'Checking...' on other devices."
        echo "  Possible causes:"
        echo "    - macOS firewall blocking port $CERID_PORT_MCP"
        echo "    - VPN routing preventing LAN access"
        echo "    - Detected IP ($CERID_HOST) is incorrect — set CERID_HOST in .env"
    fi
fi

echo ""
echo "=== Service Status ==="
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

echo ""
echo "=== Quick Health Check ==="

# Structured health output
check_health() {
    local name=$1 url=$2
    local code
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "$url" 2>/dev/null || echo "000")
    case "$code" in
        200) printf "  %-12s \033[32mOK\033[0m\n" "$name" ;;
        000) printf "  %-12s \033[31mUNREACHABLE\033[0m\n" "$name" ;;
        *)   printf "  %-12s \033[33mHTTP %s\033[0m\n" "$name" "$code" ;;
    esac
}

check_health "MCP" "http://localhost:${CERID_PORT_MCP}/health"
check_health "React GUI" "http://localhost:${CERID_PORT_GUI}"
check_health "Bifrost" "http://localhost:${CERID_PORT_BIFROST}"
check_health "Neo4j" "http://localhost:${CERID_PORT_NEO4J}"
check_health "ChromaDB" "http://localhost:${CERID_PORT_CHROMA}/api/v1/heartbeat"
REDIS_PASS=$(grep -s '^REDIS_PASSWORD=' "$ENV_FILE" | cut -d'=' -f2- || echo "")
printf "  %-12s " "Redis"
if redis-cli -p "$CERID_PORT_REDIS" ${REDIS_PASS:+-a "$REDIS_PASS"} ping 2>/dev/null | grep -q PONG; then
    printf "\033[32mOK\033[0m\n"
else
    printf "\033[31mUNREACHABLE\033[0m\n"
fi

echo ""
echo "=== Access URLs ==="
echo "React GUI: http://localhost:${CERID_PORT_GUI}"
echo "MCP Docs:  http://localhost:${CERID_PORT_MCP}/docs"
echo "Bifrost:   http://localhost:${CERID_PORT_BIFROST}"
if [ "$CERID_HOST" != "localhost" ]; then
    echo ""
    echo "=== LAN Access (iPad / other devices) ==="
    echo "React GUI: http://${CERID_HOST}:${CERID_PORT_GUI}"
    echo "MCP API:   http://${CERID_HOST}:${CERID_PORT_MCP}"
    if [ -n "${GATEWAY_ENABLED}" ] || [ "${CERID_GATEWAY:-}" = "true" ]; then
        echo "HTTPS:     https://${CERID_HOST} (Caddy, self-signed cert)"
    fi
fi

if [ "$CRITICAL_FAIL" -ne 0 ]; then
    echo ""
    echo "WARNING: One or more critical services failed to start. Check logs with:"
    echo "  docker logs <container-name>"
    exit 2
fi
