#!/usr/bin/env bash
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Cerid AI — Guided setup for new installations
# Usage: ./scripts/setup.sh
#
# Idempotent — safe to run again (skips completed steps).
# Delegates service startup to start-cerid.sh for consistent IP detection,
# pre-flight validation, and health checks.

set -euo pipefail
CERID_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$CERID_ROOT/.env"

echo "=== Cerid AI Setup ==="
echo ""

# --- [1/7] Prerequisites ---
echo "[1/7] Checking prerequisites..."

if ! command -v docker &>/dev/null; then
    echo "  ERROR: Docker is required."
    echo "  Install: https://docs.docker.com/get-docker/"
    exit 1
fi

if ! docker compose version &>/dev/null; then
    echo "  ERROR: Docker Compose V2 is required (docker compose, not docker-compose)."
    echo "  Install: https://docs.docker.com/compose/install/"
    exit 1
fi

if ! docker info &>/dev/null 2>&1; then
    echo "  ERROR: Docker daemon is not running."
    echo "  Fix: Open Docker Desktop or start the Docker service."
    exit 1
fi

echo "  Docker: $(docker --version | head -1)"
echo "  Compose: $(docker compose version | head -1)"

# --- [2/7] Environment ---
echo "[2/7] Setting up environment..."

if [ -f "$ENV_FILE" ]; then
    echo "  .env already exists"
elif [ -f "$CERID_ROOT/.env.age" ]; then
    if command -v age &>/dev/null && [ -f "${CERID_AGE_KEY:-$HOME/.config/cerid/age-key.txt}" ]; then
        echo "  Decrypting .env from .env.age..."
        "$CERID_ROOT/scripts/env-unlock.sh"
    else
        echo "  .env.age found but age decryption not available."
        echo "  Copying .env.example as starting point..."
        cp "$CERID_ROOT/.env.example" "$ENV_FILE"
    fi
else
    echo "  Creating .env from template..."
    cp "$CERID_ROOT/.env.example" "$ENV_FILE"
fi

# Interactive prompts for required secrets (only if empty and terminal is interactive)
if [ -t 0 ]; then
    # Auto-generate NEO4J_PASSWORD if empty
    neo4j_pass=$(grep "^NEO4J_PASSWORD=" "$ENV_FILE" 2>/dev/null | cut -d'=' -f2- || echo "")
    if [ -z "$neo4j_pass" ]; then
        neo4j_pass=$(openssl rand -hex 12 2>/dev/null || head -c 24 /dev/urandom | base64 | tr -d '/+=' | head -c 24)
        # Cross-platform sed in-place: use temp file (works on macOS, Linux, WSL)
        sed "s|^NEO4J_PASSWORD=.*|NEO4J_PASSWORD=$neo4j_pass|" "$ENV_FILE" > "$ENV_FILE.tmp" && mv "$ENV_FILE.tmp" "$ENV_FILE"
        echo "  Generated NEO4J_PASSWORD (random)"
    fi

    # Prompt for OpenRouter API key if empty
    or_key=$(grep "^OPENROUTER_API_KEY=" "$ENV_FILE" 2>/dev/null | cut -d'=' -f2- || echo "")
    if [ -z "$or_key" ]; then
        echo ""
        echo "  An OpenRouter API key is required for LLM access."
        echo "  Get one at: https://openrouter.ai/keys"
        echo ""
        read -rp "  Enter your OpenRouter API key (or press Enter to skip): " or_key
        if [ -n "$or_key" ]; then
            # Cross-platform sed in-place: use temp file (works on macOS, Linux, WSL)
            sed "s|^OPENROUTER_API_KEY=.*|OPENROUTER_API_KEY=$or_key|" "$ENV_FILE" > "$ENV_FILE.tmp" && mv "$ENV_FILE.tmp" "$ENV_FILE"
            echo "  Saved OPENROUTER_API_KEY"
        else
            echo "  Skipped — you can set OPENROUTER_API_KEY in .env later"
        fi
    fi
else
    echo "  Non-interactive mode — check .env for required API keys"
fi

# --- [3/7] Docker network ---
echo "[3/7] Creating Docker network..."
docker network create llm-network 2>/dev/null && echo "  Created llm-network" || echo "  llm-network already exists"

# --- [4/7] Directories ---
echo "[4/7] Setting up directories..."
ARCHIVE_DIR="${HOME}/cerid-archive"
if [ -d "$ARCHIVE_DIR" ] || [ -L "$ARCHIVE_DIR" ]; then
    echo "  Archive directory exists: $ARCHIVE_DIR"
else
    mkdir -p "$ARCHIVE_DIR"/{coding,finance,projects,personal,general,inbox}
    echo "  Created $ARCHIVE_DIR with domain folders"
fi

# --- [5/7] Start services ---
echo "[5/7] Starting services (delegating to start-cerid.sh)..."
echo ""
"$CERID_ROOT/scripts/start-cerid.sh"
echo ""

# --- [6/7] Validate ---
echo "[6/7] Running environment validation..."
if [ -x "$CERID_ROOT/scripts/validate-env.sh" ]; then
    "$CERID_ROOT/scripts/validate-env.sh" --quick || true
fi

# --- [7/7] Done ---
echo ""
echo "=== Setup Complete ==="
echo ""
echo "Quick start:"
echo "  1. Open http://localhost:3000 in your browser"
echo "  2. Drop files into ~/cerid-archive/<domain>/ for knowledge ingestion"
echo "  3. Use the React GUI to chat with your knowledge base"
echo ""
echo "Useful commands:"
echo "  ./scripts/start-cerid.sh          # start all services"
echo "  ./scripts/start-cerid.sh --build  # rebuild after code changes"
echo "  ./scripts/validate-env.sh         # full environment validation"
echo "  ./scripts/env-lock.sh             # re-encrypt .env after edits"
echo ""

# Platform-specific notes
if [[ "$OSTYPE" == "darwin"* ]]; then
    echo "macOS notes:"
    echo "  - If LAN access fails, check System Settings > Network > Firewall"
    echo "  - Archive dir can be symlinked to Dropbox: ln -sf ~/Dropbox/cerid-archive ~/cerid-archive"
elif [[ "$OSTYPE" == "linux"* ]]; then
    echo "Linux notes:"
    echo "  - If LAN access fails, check UFW/iptables rules for ports 3000 and 8888"
    echo "  - Ensure your user is in the 'docker' group: sudo usermod -aG docker \$USER"
fi
