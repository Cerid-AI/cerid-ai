#!/bin/bash
# Copyright (c) 2026 Cerid AI. All rights reserved.
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

# Shared health-check library (container probes, auth-aware redis/neo4j,
# HTTP tri-state with skip, zombie cleanup). Sourced early so both
# preflight and Quick Health Check can use it.
# shellcheck source=lib/healthcheck.sh
export CERID_ENV_FILE="$ENV_FILE"
source "$CERID_ROOT/scripts/lib/healthcheck.sh"

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

# --- Lightweight Mode (8GB machines — skips Neo4j) ---
LIGHTWEIGHT_MODE="${CERID_LIGHTWEIGHT:-}"
# Also read from .env if not set in environment
if [ -z "$LIGHTWEIGHT_MODE" ] && [ -f "$ENV_FILE" ]; then
    LIGHTWEIGHT_MODE=$(grep -s '^CERID_LIGHTWEIGHT=' "$ENV_FILE" 2>/dev/null | cut -d'=' -f2- || echo "")
fi

if [ "$LIGHTWEIGHT_MODE" = "true" ]; then
    echo "=== Starting Cerid AI Stack (LIGHTWEIGHT MODE) ==="
    echo "  Neo4j disabled — graph features unavailable"
    echo "  RAM requirement: ~4-6GB (down from ~12-16GB)"
else
    echo "=== Starting Cerid AI Stack ==="
fi

# --- Prerequisites ---
# Docker Compose V2
if ! docker compose version >/dev/null 2>&1; then
    echo "Error: Docker Compose V2 is required (docker compose, not docker-compose)."
    echo "Install: https://docs.docker.com/compose/install/"
    exit 1
fi

# curl (used for health checks and sidecar detection)
if ! command -v curl >/dev/null 2>&1; then
    echo "Warning: curl not found — health checks and sidecar detection will be skipped."
fi

# python3 (needed for sidecar, optional)
if ! command -v python3 >/dev/null 2>&1; then
    echo "Note: python3 not found on host — sidecar GPU acceleration unavailable."
fi

# Port availability check (non-blocking warnings)
for PORT_CHECK in "${CERID_PORT_GUI:-3000}:GUI" "${CERID_PORT_MCP:-8888}:MCP"; do
    _PORT="${PORT_CHECK%%:*}"
    _NAME="${PORT_CHECK##*:}"
    if command -v lsof >/dev/null 2>&1 && lsof -iTCP:"$_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
        echo "Warning: Port $_PORT ($_NAME) is already in use. Docker may fail to bind."
    fi
done

# Docker Desktop memory check (macOS/Windows only)
if [ "$(uname -s)" = "Darwin" ]; then
    _DOCKER_MEM=$(docker info --format '{{.MemTotal}}' 2>/dev/null || echo "0")
    _DOCKER_MEM_GB=$(( _DOCKER_MEM / 1073741824 ))
    if [ "$_DOCKER_MEM_GB" -gt 0 ] && [ "$_DOCKER_MEM_GB" -lt 4 ]; then
        echo ""
        echo "Warning: Docker Desktop has only ${_DOCKER_MEM_GB}GB RAM allocated."
        echo "  Cerid needs at least 4GB (8GB recommended)."
        echo "  Increase via: Docker Desktop → Settings → Resources → Memory"
        echo ""
    fi
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
export CERID_PORT_NEO4J="${CERID_PORT_NEO4J:-7474}"
export CERID_PORT_NEO4J_BOLT="${CERID_PORT_NEO4J_BOLT:-7687}"
export CERID_PORT_CHROMA="${CERID_PORT_CHROMA:-8001}"
export CERID_PORT_REDIS="${CERID_PORT_REDIS:-6379}"

# Auto-detect host memory (GB) for setup wizard system check.
# Inside Docker Desktop for Mac/Windows, /proc/meminfo reports VM memory, not host.
# This passes the real host RAM into the container via HOST_MEMORY_GB env var.
if [ -z "${HOST_MEMORY_GB:-}" ]; then
    case "$(uname -s)" in
        Darwin)  HOST_MEMORY_GB=$(sysctl -n hw.memsize 2>/dev/null | awk '{printf "%.1f", $1/1073741824}') ;;
        Linux)   HOST_MEMORY_GB=$(awk '/MemTotal/{printf "%.1f", $2/1048576}' /proc/meminfo 2>/dev/null) ;;
    esac
fi
export HOST_MEMORY_GB="${HOST_MEMORY_GB:-}"

# Auto-detect host hardware (OS, CPU, GPU) for setup wizard system check.
# Container can only see Linux kernel — these env vars pass real host info.
if [ -z "${HOST_OS:-}" ]; then
    case "$(uname -s)" in
        Darwin)
            HOST_OS="macOS $(sw_vers -productVersion 2>/dev/null || echo '')"
            HOST_CPU="$(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo 'Unknown')"
            HOST_CPU_CORES="$(sysctl -n hw.ncpu 2>/dev/null || echo '')"
            HOST_GPU="$(system_profiler SPDisplaysDataType 2>/dev/null | grep 'Chipset Model' | head -1 | sed 's/.*: //' || echo 'Unknown')"
            # Detect Apple Silicon vs Intel
            if sysctl -n machdep.cpu.brand_string 2>/dev/null | grep -q "Apple"; then
                HOST_GPU_ACCEL="metal"
            else
                HOST_GPU_ACCEL="none"
            fi
            ;;
        Linux)
            HOST_OS="Linux $(uname -r 2>/dev/null)"
            HOST_CPU="$(grep -m1 'model name' /proc/cpuinfo 2>/dev/null | sed 's/.*: //' || echo 'Unknown')"
            HOST_CPU_CORES="$(nproc 2>/dev/null || echo '')"
            if command -v nvidia-smi &>/dev/null; then
                HOST_GPU="$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 || echo 'Unknown')"
                HOST_GPU_ACCEL="cuda"
            elif [ -d /dev/kfd ]; then
                HOST_GPU_ACCEL="rocm"
                HOST_GPU="AMD GPU (ROCm)"
            else
                HOST_GPU="None detected"
                HOST_GPU_ACCEL="none"
            fi
            ;;
        MINGW*|MSYS*|CYGWIN*)
            HOST_OS="Windows ($(uname -s))"
            HOST_CPU="Unknown"
            HOST_CPU_CORES="$(nproc 2>/dev/null || echo '')"
            HOST_GPU="Unknown"
            HOST_GPU_ACCEL="none"
            ;;
    esac
fi
export HOST_OS="${HOST_OS:-}"
export HOST_CPU="${HOST_CPU:-}"
export HOST_CPU_CORES="${HOST_CPU_CORES:-}"
export HOST_GPU="${HOST_GPU:-}"
export HOST_GPU_ACCEL="${HOST_GPU_ACCEL:-}"

# Auto-select ONNX model variant based on CPU architecture.
# AVX2 quantized model (23MB, int8) is 4x faster but only works on x86 with AVX2.
# Generic float32 model (91MB) works on any CPU including ARM64/Apple Silicon.
if [ -z "${RERANK_ONNX_FILENAME:-}" ]; then
    _arch="$(uname -m)"
    case "$_arch" in
        x86_64|amd64)
            # Check for AVX2 support
            if [ "$(uname -s)" = "Darwin" ]; then
                _has_avx2=$(sysctl -n hw.optional.avx2_0 2>/dev/null || echo "0")
            else
                _has_avx2=$(grep -c avx2 /proc/cpuinfo 2>/dev/null || echo "0")
            fi
            if [ "$_has_avx2" -gt 0 ] 2>/dev/null; then
                export RERANK_ONNX_FILENAME="onnx/model_quint8_avx2.onnx"
            else
                export RERANK_ONNX_FILENAME="onnx/model.onnx"
            fi
            ;;
        *)
            # ARM64, aarch64, or unknown — use generic float32 model
            export RERANK_ONNX_FILENAME="onnx/model.onnx"
            ;;
    esac
fi

# Persist HOST_* values to .env so `docker compose up` without start-cerid.sh
# still passes real host hardware info into containers.
# Uses delete+append (not sed substitution) to safely handle special chars
# in hardware strings like "Intel(R) Core(TM) i9-13900K @ 3.00GHz".
_persist_host_var() {
    local key="$1" val="$2"
    [ -z "$val" ] && return
    # Remove existing line (if any), then append the new value
    grep -v "^${key}=" "$ENV_FILE" > "${ENV_FILE}.tmp" 2>/dev/null || true
    echo "${key}=${val}" >> "${ENV_FILE}.tmp"
    mv "${ENV_FILE}.tmp" "$ENV_FILE"
}
_persist_host_var HOST_MEMORY_GB "$HOST_MEMORY_GB"
_persist_host_var HOST_OS "$HOST_OS"
_persist_host_var HOST_CPU "$HOST_CPU"
_persist_host_var HOST_CPU_CORES "$HOST_CPU_CORES"
_persist_host_var HOST_GPU "$HOST_GPU"
_persist_host_var HOST_GPU_ACCEL "$HOST_GPU_ACCEL"
_persist_host_var RERANK_ONNX_FILENAME "$RERANK_ONNX_FILENAME"

# Platform-aware Ollama URL default (Section 4: Multi-OS)
# macOS/Windows Docker Desktop: host.docker.internal
# Linux Docker Engine: localhost (host network accessible)
if [ -z "${OLLAMA_URL:-}" ]; then
    case "$(uname -s)" in
        Darwin)  export OLLAMA_URL="http://host.docker.internal:11434" ;;
        MINGW*|MSYS*|CYGWIN*)  export OLLAMA_URL="http://host.docker.internal:11434" ;;
        Linux)
            # Docker Desktop supports host.docker.internal; native Docker Engine does not.
            if docker info 2>/dev/null | grep -q "Desktop"; then
                export OLLAMA_URL="http://host.docker.internal:11434"
            else
                # Native Docker Engine — use the default bridge gateway
                _bridge_ip=$(docker network inspect bridge --format '{{range .IPAM.Config}}{{.Gateway}}{{end}}' 2>/dev/null || echo "172.17.0.1")
                export OLLAMA_URL="http://${_bridge_ip}:11434"
            fi
            ;;
    esac
fi

# --- Pre-flight checks (27B) ---
preflight_checks() {
    local fail=0

    # Check required env vars are non-empty
    local required_vars="OPENROUTER_API_KEY"
    if [ "$LIGHTWEIGHT_MODE" != "true" ]; then
        required_vars="NEO4J_PASSWORD OPENROUTER_API_KEY"
    fi
    for var in $required_vars; do
        local val
        val=$(grep "^${var}=" "$ENV_FILE" 2>/dev/null | cut -d'=' -f2- || echo "")
        if [ -z "$val" ]; then
            echo "  ERROR: $var is empty in .env"
            fail=1
        fi
    done

    # Check port conflicts (skip ports owned by our containers)
    local our_containers="ai-companion-mcp|ai-companion-neo4j|ai-companion-chroma|ai-companion-redis|cerid-web"

    # Cross-platform port listener detection (macOS lsof, Linux ss/netstat)
    _port_in_use() {
        local port=$1
        if command -v ss &>/dev/null; then
            ss -tlnp 2>/dev/null | grep -q ":${port} " && return 0
        elif command -v lsof &>/dev/null; then
            lsof -i ":${port}" -sTCP:LISTEN -t &>/dev/null && return 0
        elif command -v netstat &>/dev/null; then
            netstat -tlnp 2>/dev/null | grep -q ":${port} " && return 0
        fi
        return 1
    }

    check_port() {
        local port=$1 service=$2
        if _port_in_use "$port"; then
            # Skip if it's one of our own containers
            local cid
            cid=$(docker ps --filter "publish=$port" --format '{{.Names}}' 2>/dev/null || echo "")
            if echo "$cid" | grep -qE "$our_containers" 2>/dev/null; then
                return 0  # Our container — not a conflict
            fi
            echo "  ERROR: Port $port ($service) is already in use"
            echo "    Fix: Stop the process or set CERID_PORT_${service^^} in .env"
            fail=1
        fi
    }
    check_port "$CERID_PORT_GUI" "gui"
    check_port "$CERID_PORT_MCP" "mcp"
    if [ "$LIGHTWEIGHT_MODE" != "true" ]; then
        check_port "$CERID_PORT_NEO4J" "neo4j"
    fi
    check_port "$CERID_PORT_CHROMA" "chroma"

    # Disk space warning (Docker data root) — cross-platform
    local docker_root avail_gb
    docker_root=$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || echo "/var/lib/docker")
    if [[ "$OSTYPE" == "darwin"* ]]; then
        avail_gb=$(df -g "$docker_root" 2>/dev/null | awk 'NR==2{print $4}' || echo "999")
    else
        # Linux df returns 1K-blocks by default
        local avail_kb
        avail_kb=$(df "$docker_root" 2>/dev/null | awk 'NR==2{print $4}' || echo "999999999")
        avail_gb=$((avail_kb / 1024 / 1024))
    fi
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

# --- Ollama Add-On Prompt ---
OLLAMA_PROFILE=""
OLLAMA_DEFAULT_MODEL="llama3.2:3b"

# Source GPU detection
source "$CERID_ROOT/scripts/detect-gpu.sh" 2>/dev/null || true

# [0/4] Inference Sidecar — detect, auto-start, or offer install
SIDECAR_PORT="${CERID_SIDECAR_PORT:-8889}"
SIDECAR_URL="${CERID_SIDECAR_URL:-http://localhost:$SIDECAR_PORT}"
SIDECAR_INSTALLED=false

# Check if sidecar dependencies are installed (fastembed or onnxruntime + fastapi)
if python3 -c "import onnxruntime; import fastapi" 2>/dev/null; then
    SIDECAR_INSTALLED=true
fi

if curl -sf "$SIDECAR_URL/health" >/dev/null 2>&1; then
    # Sidecar already running
    echo "[sidecar] FastEmbed sidecar detected at $SIDECAR_URL"
    SIDECAR_HEALTH=$(curl -sf "$SIDECAR_URL/health" 2>/dev/null || echo "{}")
    echo "[sidecar] $(echo "$SIDECAR_HEALTH" | grep -o '"platform":"[^"]*"' | head -1 || echo "running")"
elif [ "$SIDECAR_INSTALLED" = "true" ]; then
    # Sidecar installed but not running — auto-start in background
    echo "[sidecar] Sidecar installed but not running — starting in background..."
    CERID_SIDECAR_PORT="$SIDECAR_PORT" nohup python3 "$CERID_ROOT/scripts/cerid-sidecar.py" \
        > "$CERID_ROOT/logs/sidecar.log" 2>&1 &
    SIDECAR_PID=$!
    echo "[sidecar] Started (PID $SIDECAR_PID, log: logs/sidecar.log)"
    # Give it a moment to start
    sleep 2
    if curl -sf "$SIDECAR_URL/health" >/dev/null 2>&1; then
        echo "[sidecar] Health check passed"
    else
        echo "[sidecar] Warning: sidecar started but not yet healthy (may still be loading models)"
    fi
else
    # Not installed — check if GPU acceleration would help
    OLLAMA_REACHABLE=false
    if curl -sf "http://localhost:11434/api/tags" >/dev/null 2>&1 || \
       curl -sf "http://host.docker.internal:11434/api/tags" >/dev/null 2>&1; then
        OLLAMA_REACHABLE=true
    fi

    if [ "$OLLAMA_REACHABLE" = "false" ] && [ "${FORCE_FLAG:-}" != "1" ]; then
        echo ""
        echo "[sidecar] No GPU acceleration detected (no sidecar, no Ollama)"
        echo "  The embedding sidecar runs natively and uses your GPU for faster ingestion."
        echo "  Hardware: ${CERID_GPU_LABEL:-Unknown}"
        echo ""
        SIDECAR_ANSWER=""
        if [ -t 0 ] && [ -r /dev/tty ]; then
            read -r -p "  Install embedding sidecar for faster ingestion? [Y/n]: " SIDECAR_ANSWER </dev/tty 2>/dev/null || SIDECAR_ANSWER=""
        else
            echo "  (non-interactive shell — skipping sidecar prompt; export CERID_INSTALL_SIDECAR=1 to force install)"
            [ "${CERID_INSTALL_SIDECAR:-}" = "1" ] && SIDECAR_ANSWER="y"
        fi
        # POSIX-portable lowercase (avoids bash 4+ ${VAR,,} which fails on macOS /bin/bash 3.2)
        SIDECAR_LOWER=$(printf '%s' "${SIDECAR_ANSWER:-n}" | tr '[:upper:]' '[:lower:]')
        if [ "$SIDECAR_LOWER" != "n" ]; then
            echo "[sidecar] Installing..."
            bash "$CERID_ROOT/scripts/install-sidecar.sh"
            # Start sidecar in background
            mkdir -p "$CERID_ROOT/logs"
            CERID_SIDECAR_PORT="$SIDECAR_PORT" nohup python3 "$CERID_ROOT/scripts/cerid-sidecar.py" \
                > "$CERID_ROOT/logs/sidecar.log" 2>&1 &
            echo "[sidecar] Started in background (PID $!, log: logs/sidecar.log)"
            sleep 3
            if curl -sf "$SIDECAR_URL/health" >/dev/null 2>&1; then
                echo "[sidecar] Health check passed"
            else
                echo "[sidecar] Warning: still loading models — will be detected on next health check"
            fi
        else
            echo "[sidecar] Skipped (Docker CPU embeddings will be used)"
            echo "[sidecar] Install later: bash scripts/install-sidecar.sh"
        fi
    else
        if [ "$OLLAMA_REACHABLE" = "true" ]; then
            echo "[sidecar] Ollama detected — using Ollama for LLM tasks (sidecar optional)"
        else
            echo "[sidecar] No sidecar detected (optional — Docker CPU embeddings will be used)"
        fi
        echo "[sidecar] Install with: bash scripts/install-sidecar.sh"
    fi
fi

# Check if Ollama is already configured
OLLAMA_CONFIGURED=$(grep -s '^OLLAMA_ENABLED=' "$ENV_FILE" 2>/dev/null | cut -d'=' -f2- || echo "")

if [ -z "$OLLAMA_CONFIGURED" ]; then
    # First run — prompt the user
    echo ""
    echo "[ollama] Local LLM Add-On Available"
    echo "  Model:    $OLLAMA_DEFAULT_MODEL (3B params, ~2GB download)"
    echo "  Purpose:  Local AI for verification context, smart routing, and extraction"
    echo "  Benefit:  Zero cloud cost for internal pipeline operations"
    echo "  Hardware: ${CERID_GPU_LABEL:-Unknown}"
    if [ "${CERID_OLLAMA_IMAGE:-}" = "native" ]; then
        echo "  Note:     macOS Metal detected — Ollama runs natively (not in Docker)"
        echo "            Install via: brew install ollama && ollama serve"
    fi
    echo ""
    OLLAMA_ANSWER="n"
    if [ -t 0 ] && [ -r /dev/tty ]; then
        read -r -p "  Install Ollama add-on? [y/N]: " OLLAMA_ANSWER </dev/tty 2>/dev/null || OLLAMA_ANSWER="n"
    else
        echo "  (non-interactive shell — skipping Ollama prompt; export CERID_INSTALL_OLLAMA=1 to force install)"
        [ "${CERID_INSTALL_OLLAMA:-}" = "1" ] && OLLAMA_ANSWER="y"
    fi

    if [[ "$OLLAMA_ANSWER" =~ ^[Yy]$ ]]; then
        echo "OLLAMA_ENABLED=true" >> "$ENV_FILE"
        echo "INTERNAL_LLM_PROVIDER=ollama" >> "$ENV_FILE"
        echo "INTERNAL_LLM_MODEL=$OLLAMA_DEFAULT_MODEL" >> "$ENV_FILE"
        if [ "${CERID_OLLAMA_IMAGE:-}" != "native" ]; then
            echo "OLLAMA_URL=http://cerid-ollama:11434" >> "$ENV_FILE"
        fi
        OLLAMA_CONFIGURED="true"
        echo "  [ollama] Enabled — will start with stack"
    else
        echo "OLLAMA_ENABLED=false" >> "$ENV_FILE"
        OLLAMA_CONFIGURED="false"
        echo "  [ollama] Skipped — enable later in Settings or re-run with OLLAMA_ENABLED=true"
    fi
fi

if [ "$OLLAMA_CONFIGURED" = "true" ] && [ "${CERID_OLLAMA_IMAGE:-}" != "native" ]; then
    OLLAMA_PROFILE="--profile ollama"
fi

# Ensure archive directory exists (MCP mounts it read-only)
ARCHIVE_DIR="${HOME}/cerid-archive"
if [ ! -d "$ARCHIVE_DIR" ] && [ ! -L "$ARCHIVE_DIR" ]; then
    echo "[setup] Creating archive directory at $ARCHIVE_DIR"
    mkdir -p "$ARCHIVE_DIR"/{coding,finance,projects,personal,general,inbox}
fi

# --- Detect LAN IP for remote access (iPad, other machines) ---
detect_lan_ip() {
    # Cross-platform LAN IP detection
    # 1. Linux: ip route (most reliable on Linux/WSL)
    if command -v ip &>/dev/null; then
        local ip_route
        ip_route=$(ip -4 route get 1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}')
        [ -n "$ip_route" ] && echo "ip-route:$ip_route" && return
    fi
    # 2. macOS: ipconfig on common interfaces
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
    # 3. Linux fallback: hostname -I
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

# When LAN access is enabled, bind to all interfaces (requires explicit opt-in)
if [[ -n "$CERID_HOST" && "$CERID_HOST" != "localhost" && "$CERID_HOST" != "127.0.0.1" ]]; then
  if [[ "${CERID_LAN_MODE:-}" == "true" ]]; then
    export CERID_BIND_ADDR="${CERID_BIND_ADDR:-0.0.0.0}"
    echo "[WARNING] LAN mode enabled — binding ALL services to 0.0.0.0"
    echo "    Exposed ports: Neo4j(7474/7687), ChromaDB(8001), Redis(6379), MCP(8888), GUI(3000)"
    echo "    Ensure your network is trusted or set up the Caddy HTTPS gateway."
  else
    echo "    LAN IP detected ($CERID_HOST) but CERID_LAN_MODE is not set."
    echo "    Services will bind to 127.0.0.1 only. Set CERID_LAN_MODE=true in .env to enable LAN access."
  fi
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

# Create the external Docker network that ALL compose files reference.
# Previously the root docker-compose.yml used "driver: bridge" which silently
# created a project-scoped "cerid-ai_llm-network" instead of reusing this
# network, splitting infra containers from MCP/web.  Now that the root compose
# also declares "external: true", this line is required for both unified and
# legacy startup modes.
docker network create llm-network 2>/dev/null || true

if [ -n "$BUILD_FLAG" ]; then
    echo "[build] Rebuilding images with local Dockerfiles..."
fi

UNIFIED_COMPOSE="$CERID_ROOT/docker-compose.yml"
LIGHTWEIGHT_OVERRIDE="$CERID_ROOT/docker-compose.lightweight.yml"

# Generate lightweight compose override (removes Neo4j dependency from mcp-server)
if [ "$LIGHTWEIGHT_MODE" = "true" ]; then
    cat > "$LIGHTWEIGHT_OVERRIDE" <<'LEOF'
# Auto-generated by start-cerid.sh for CERID_LIGHTWEIGHT=true
# Removes Neo4j service and dependency from mcp-server
services:
  neo4j:
    profiles: ["disabled"]
  mcp-server:
    depends_on:
      chromadb:
        condition: service_healthy
      redis:
        condition: service_healthy
LEOF
fi

# Ensure Docker bind-mount directories exist on the host.
# If these are missing, Docker creates them as root-owned dirs and services
# (especially ChromaDB's SQLite) fail with write permission errors.
mkdir -p "$CERID_ROOT/stacks/infrastructure/data/"{chroma,neo4j,neo4j-logs,redis}

# Zombie-container cleanup — any ai-companion-*/cerid-* container in
# Exited/Dead/Created state holds its name, causing `docker compose up`
# to fail with an opaque conflict error. Remove them up front.
cleanup_zombies

# Generate VERSION file from pyproject.toml for Docker build
echo "[build] Generating src/mcp/VERSION file..."
make version-file

if [ -z "$LEGACY_FLAG" ] && [ -f "$UNIFIED_COMPOSE" ]; then
    COMPOSE_FILES="-f $UNIFIED_COMPOSE"
    if [ "$LIGHTWEIGHT_MODE" = "true" ]; then
        COMPOSE_FILES="$COMPOSE_FILES -f $LIGHTWEIGHT_OVERRIDE"
        echo "[unified] Starting services via docker-compose.yml + lightweight override..."
        echo "  Startup order: ChromaDB, Redis → MCP → Web (Neo4j SKIPPED)"
    else
        echo "[unified] Starting all services via root docker-compose.yml..."
        echo "  Startup order: Neo4j, ChromaDB, Redis → MCP → Web"
    fi
    echo "  (depends_on healthchecks enforce correct ordering)"
    docker compose $COMPOSE_FILES --env-file "$ENV_FILE" $OLLAMA_PROFILE up -d $BUILD_FLAG $WEB_RECREATE
else
    # --- Legacy 4-step startup (preserved for backward compatibility) ---
    echo "[legacy] Starting services in 4-step order..."
    if [ "$LIGHTWEIGHT_MODE" = "true" ]; then
        echo "[1/3] Starting Infrastructure (ChromaDB, Redis — Neo4j SKIPPED)..."
        docker compose -f "$CERID_ROOT/stacks/infrastructure/docker-compose.yml" --env-file "$ENV_FILE" up -d chromadb redis
    else
        echo "[1/3] Starting Infrastructure (Neo4j, ChromaDB, Redis)..."
        docker compose -f "$CERID_ROOT/stacks/infrastructure/docker-compose.yml" --env-file "$ENV_FILE" up -d
    fi

    echo "[2/3] Starting MCP Server..."
    docker compose -f "$CERID_ROOT/src/mcp/docker-compose.yml" --env-file "$ENV_FILE" up -d $BUILD_FLAG

    echo "[3/3] Starting React GUI..."
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

if [ "$LIGHTWEIGHT_MODE" = "true" ]; then
    echo "  Neo4j... SKIPPED (lightweight mode)"
else
    echo -n "  Neo4j..."
    wait_for_service "Neo4j" "http://127.0.0.1:${CERID_PORT_NEO4J}" 60 && echo " ready" || echo " timeout"
fi
echo -n "  ChromaDB..."
wait_for_service "ChromaDB" "http://127.0.0.1:${CERID_PORT_CHROMA}/api/v1/heartbeat" 30 && echo " ready" || echo " timeout"
echo -n "  MCP..."
wait_for_service "MCP" "http://localhost:${CERID_PORT_MCP}/health" 90 && echo " ready" || { echo " timeout"; CRITICAL_FAIL=1; }
echo -n "  React GUI..."
wait_for_service "React GUI" "http://localhost:${CERID_PORT_GUI}" 60 && echo " ready" || { echo " timeout"; CRITICAL_FAIL=1; }

# Ollama: wait for service + pull default model
if [ -n "$OLLAMA_PROFILE" ]; then
    echo -n "  Ollama..."
    if wait_for_service "Ollama" "http://127.0.0.1:11434/api/tags" 60; then
        echo " ready"
        # Pull default model if not already present
        local_models=$(curl -sf http://127.0.0.1:11434/api/tags 2>/dev/null | grep -c "$OLLAMA_DEFAULT_MODEL" || echo "0")
        if [ "$local_models" = "0" ]; then
            echo "  [ollama] Pulling $OLLAMA_DEFAULT_MODEL (~1GB)..."
            docker exec cerid-ollama ollama pull "$OLLAMA_DEFAULT_MODEL" 2>&1 | tail -1
        else
            echo "  [ollama] Model $OLLAMA_DEFAULT_MODEL already available"
        fi
    else
        echo " timeout (Ollama will be available later)"
    fi
elif [ "$OLLAMA_CONFIGURED" = "true" ] && [ "${CERID_OLLAMA_IMAGE:-}" = "native" ]; then
    # macOS native Ollama — check if it's running
    echo -n "  Ollama (native)..."
    OLLAMA_NATIVE_URL=$(grep -s '^OLLAMA_URL=' "$ENV_FILE" 2>/dev/null | cut -d'=' -f2- || echo "http://localhost:11434")
    if wait_for_service "Ollama" "${OLLAMA_NATIVE_URL}/api/tags" 5 1; then
        echo " ready"
    else
        echo " not running (start with: ollama serve)"
    fi
fi

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

# All probes below delegate to scripts/lib/healthcheck.sh so that start-cerid
# and validate-env stay in lockstep. The library handles:
#   - Redis: authenticated PING via `docker exec`, honoring REDIS_PASSWORD
#   - Neo4j:  authenticated Cypher probe (not just HTTP, which ignores auth)

check_http "MCP"       "http://localhost:${CERID_PORT_MCP}/health" || true
check_http "React GUI" "http://localhost:${CERID_PORT_GUI}"        || true

if [ "$LIGHTWEIGHT_MODE" = "true" ]; then
    skip "Neo4j — lightweight mode"
else
    check_neo4j ai-companion-neo4j "${NEO4J_USER:-neo4j}" "${NEO4J_PASSWORD:-}" || true
fi

check_http  "ChromaDB" "http://localhost:${CERID_PORT_CHROMA}/api/v1/heartbeat" || true
check_redis ai-companion-redis "${REDIS_PASSWORD:-}" || true

echo ""
echo "=== Access URLs ==="
echo "React GUI: http://localhost:${CERID_PORT_GUI}"
echo "MCP Docs:  http://localhost:${CERID_PORT_MCP}/docs"
if [ "$CERID_HOST" != "localhost" ]; then
    echo ""
    echo "=== LAN Access (iPad / other devices) ==="
    echo "React GUI: http://${CERID_HOST}:${CERID_PORT_GUI}"
    echo "MCP API:   http://${CERID_HOST}:${CERID_PORT_MCP}"
    if [ -n "${GATEWAY_ENABLED}" ] || [ "${CERID_GATEWAY:-}" = "true" ]; then
        echo "HTTPS:     https://${CERID_HOST} (Caddy, self-signed cert)"
    fi
fi

# Clean up generated lightweight override
if [ -f "$LIGHTWEIGHT_OVERRIDE" ]; then
    rm -f "$LIGHTWEIGHT_OVERRIDE"
fi

if [ "$CRITICAL_FAIL" -ne 0 ]; then
    echo ""
    echo "WARNING: One or more critical services failed to start. Check logs with:"
    echo "  docker logs <container-name>"
    exit 2
fi
