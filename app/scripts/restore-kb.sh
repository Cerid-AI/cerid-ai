#!/usr/bin/env bash
# restore-kb.sh — Restore Neo4j, ChromaDB, and Redis from a backup directory.
# Usage: ./scripts/restore-kb.sh ./backups/2026-03-15_10-00-00
set -euo pipefail

CERID_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INFRA_DATA="$CERID_ROOT/stacks/infrastructure/data"
BACKUP_DIR="${1:-}"

if [ -z "$BACKUP_DIR" ] || [ ! -d "$BACKUP_DIR" ]; then
    echo "Usage: $0 <backup-dir>"
    echo "Example: $0 ./backups/2026-03-15_10-00-00"
    exit 1
fi

echo "=== Cerid KB Restore ==="
echo "From: $BACKUP_DIR"
echo "To:   $INFRA_DATA"
echo ""
echo "⚠️  WARNING: This will STOP all services and overwrite live data."
read -r -p "Continue? [y/N] " confirm
if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 0
fi

echo ""
echo "[1/4] Stopping infrastructure containers..."
docker compose -f "$CERID_ROOT/stacks/infrastructure/docker-compose.yml" down 2>/dev/null || true

echo "[2/4] Restoring Neo4j..."
rm -rf "$INFRA_DATA/neo4j" "$INFRA_DATA/neo4j-logs"
cp -r "$BACKUP_DIR/neo4j" "$INFRA_DATA/neo4j"
[ -d "$BACKUP_DIR/neo4j-logs" ] && cp -r "$BACKUP_DIR/neo4j-logs" "$INFRA_DATA/neo4j-logs"
echo "  ✅ Neo4j restored"

echo "[3/4] Restoring ChromaDB..."
rm -rf "$INFRA_DATA/chroma"
cp -r "$BACKUP_DIR/chroma" "$INFRA_DATA/chroma"
echo "  ✅ ChromaDB restored"

echo "[4/4] Restoring Redis..."
rm -rf "$INFRA_DATA/redis"
cp -r "$BACKUP_DIR/redis" "$INFRA_DATA/redis"
echo "  ✅ Redis restored"

echo ""
echo "=== Restore complete. Restart services with: ==="
echo "  cd $CERID_ROOT && ./scripts/start-cerid.sh"
