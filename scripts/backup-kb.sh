#!/usr/bin/env bash
# backup-kb.sh — Snapshot Neo4j, ChromaDB, and Redis data to a timestamped backup directory.
# Usage:
#   ./scripts/backup-kb.sh              # backup to ./backups/YYYY-MM-DD_HH-MM-SS/
#   ./scripts/backup-kb.sh /path/to/dir # backup to specific directory
set -euo pipefail

CERID_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INFRA_DATA="$CERID_ROOT/stacks/infrastructure/data"
BACKUP_BASE="${1:-$CERID_ROOT/backups}"
TIMESTAMP="$(date +%Y-%m-%d_%H-%M-%S)"
BACKUP_DIR="$BACKUP_BASE/$TIMESTAMP"

echo "=== Cerid KB Backup: $TIMESTAMP ==="
echo "Source: $INFRA_DATA"
echo "Dest:   $BACKUP_DIR"
echo ""

# Verify source data exists
for dir in neo4j chroma redis; do
    if [ ! -d "$INFRA_DATA/$dir" ]; then
        echo "ERROR: $INFRA_DATA/$dir not found. Is the stack running?"
        exit 1
    fi
done

mkdir -p "$BACKUP_DIR"

# --- Neo4j ---
echo "[1/3] Backing up Neo4j..."
docker pause ai-companion-neo4j 2>/dev/null || echo "  (neo4j not running — copying as-is)"
cp -r "$INFRA_DATA/neo4j" "$BACKUP_DIR/neo4j"
cp -r "$INFRA_DATA/neo4j-logs" "$BACKUP_DIR/neo4j-logs" 2>/dev/null || true
docker unpause ai-companion-neo4j 2>/dev/null || true
echo "  ✅ Neo4j backed up ($(du -sh "$BACKUP_DIR/neo4j" | cut -f1))"

# --- ChromaDB ---
echo "[2/3] Backing up ChromaDB..."
docker pause ai-companion-chroma 2>/dev/null || echo "  (chroma not running — copying as-is)"
cp -r "$INFRA_DATA/chroma" "$BACKUP_DIR/chroma"
docker unpause ai-companion-chroma 2>/dev/null || true
echo "  ✅ ChromaDB backed up ($(du -sh "$BACKUP_DIR/chroma" | cut -f1))"

# --- Redis ---
echo "[3/3] Backing up Redis..."
docker exec ai-companion-redis redis-cli -a "${REDIS_PASSWORD:-cerid-dev}" BGSAVE 2>/dev/null || echo "  (redis exec failed — copying as-is)"
sleep 2
cp -r "$INFRA_DATA/redis" "$BACKUP_DIR/redis"
echo "  ✅ Redis backed up ($(du -sh "$BACKUP_DIR/redis" | cut -f1))"

# --- Summary ---
TOTAL_SIZE=$(du -sh "$BACKUP_DIR" | cut -f1)
echo ""
echo "=== Backup complete ==="
echo "Location: $BACKUP_DIR"
echo "Total size: $TOTAL_SIZE"
echo ""
echo "To restore: ./scripts/restore-kb.sh $BACKUP_DIR"
