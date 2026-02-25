#!/usr/bin/env bash
# Cerid AI — One-command setup for new installations
# Usage: ./scripts/setup.sh
set -euo pipefail

echo "=== Cerid AI Setup ==="
echo ""

# 1. Check prerequisites
echo "[1/6] Checking prerequisites..."
command -v docker >/dev/null 2>&1 || { echo "ERROR: Docker is required. Install from https://docs.docker.com/get-docker/"; exit 1; }
command -v docker compose version >/dev/null 2>&1 || { echo "ERROR: Docker Compose V2 is required."; exit 1; }
echo "  Docker: $(docker --version | head -1)"

# 2. Create Docker network
echo "[2/6] Creating Docker network..."
docker network create llm-network 2>/dev/null && echo "  Created llm-network" || echo "  llm-network already exists"

# 3. Environment setup
echo "[3/6] Setting up environment..."
if [ -f .env ]; then
    echo "  .env already exists"
elif [ -f .env.age ]; then
    if command -v age >/dev/null 2>&1 && [ -f "$HOME/.config/cerid/age-key.txt" ]; then
        echo "  Decrypting .env from .env.age..."
        ./scripts/env-unlock.sh
    else
        echo "  .env.age found but age key not available. Copying from .env.example..."
        cp .env.example .env
        echo "  IMPORTANT: Edit .env with your API keys before starting services."
    fi
else
    cp .env.example .env
    echo "  Created .env from template. Edit with your API keys."
fi

# 4. Create archive directory
echo "[4/6] Setting up archive directory..."
ARCHIVE_DIR="${CERID_ARCHIVE_DIR:-$HOME/cerid-archive}"
if [ -d "$ARCHIVE_DIR" ] || [ -L "$ARCHIVE_DIR" ]; then
    echo "  Archive directory exists: $ARCHIVE_DIR"
else
    mkdir -p "$ARCHIVE_DIR"/{coding,finance,projects,personal,general,inbox}
    echo "  Created $ARCHIVE_DIR with domain folders"
fi

# 5. Start infrastructure
echo "[5/6] Starting infrastructure services..."
cd stacks/infrastructure && docker compose up -d && cd ../..
echo "  Waiting for services to be healthy..."
sleep 5

# 6. Start application services
echo "[6/6] Starting application services..."
cd stacks/bifrost && docker compose up -d && cd ../..
cd src/mcp && docker compose up -d --build && cd ../..

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Services:"
echo "  React GUI:    http://localhost:3000"
echo "  MCP Server:   http://localhost:8888"
echo "  Health Check: http://localhost:8888/health"
echo ""
echo "Next steps:"
echo "  1. Edit .env with your OPENROUTER_API_KEY"
echo "  2. Place files in $ARCHIVE_DIR/<domain>/ for ingestion"
echo "  3. Run: python3 src/mcp/scripts/watch_ingest.py"
echo ""
