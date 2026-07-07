#!/usr/bin/env bash
# cerid-backup.sh — One-command full logical backup + restore for the Cerid
# AI knowledge base (Neo4j, ChromaDB, BM25, Redis audit log, conversations).
#
# Usage:
#   ./scripts/cerid-backup.sh                          # full export -> backups/cerid-backup-<ts>.tar.gz
#   ./scripts/cerid-backup.sh --restore <archive>       # merge-restore an archive (prompts to confirm)
#   ./scripts/cerid-backup.sh --restore <archive> --yes # restore without the confirmation prompt
#   ./scripts/cerid-backup.sh -h | --help
set -euo pipefail

CERID_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$CERID_ROOT/.env"
BACKUP_DIR="$CERID_ROOT/backups"
PYTHON_BIN="$CERID_ROOT/.venv/bin/python"
STACK_HINT="Is the Cerid stack running? Try: ./start-cerid.sh"

usage() {
    cat <<'EOF'
cerid-backup.sh — one-command full logical backup + restore

Usage:
  ./scripts/cerid-backup.sh
      Full logical (row-level) export of Neo4j, ChromaDB, BM25, the Redis
      audit log, and user conversations to a timestamped archive under
      backups/. This is always a FULL export (not incremental).

  ./scripts/cerid-backup.sh --restore <archive> [--yes|--force]
      Extract <archive> and merge it into the live stack via
      app.sync.import_.import_all (conflict_strategy=remote_wins,
      force=False). This MERGES into the live stores — it does not wipe
      them first. Prompts for confirmation unless --yes/--force is given.

  ./scripts/cerid-backup.sh -h | --help
      Show this help and exit.

Requirements:
  - NEO4J_PASSWORD must be set in the environment or in .env at the repo
    root (same convention as start-cerid.sh / validate-env.sh).
  - The Cerid stack must be running: Neo4j on bolt://localhost:7687,
    ChromaDB on http://localhost:8001, Redis on localhost:6379.

Note — this is NOT a byte-level snapshot:
  export_all/import_all move data as rows (JSONL), not raw store files.
  For full disaster recovery of the underlying store data itself, also
  run ./scripts/backup-kb.sh, which snapshots
  stacks/infrastructure/data/{neo4j,chroma,redis} directly (with a
  container-pause quiesce dance for consistency).
EOF
}

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
MODE="backup"
RESTORE_ARCHIVE=""
ASSUME_YES="false"

while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help)
            usage
            exit 0
            ;;
        --restore)
            MODE="restore"
            RESTORE_ARCHIVE="${2:-}"
            if [ -z "$RESTORE_ARCHIVE" ]; then
                echo "ERROR: --restore requires an archive path." >&2
                exit 1
            fi
            shift 2
            ;;
        --yes|--force)
            ASSUME_YES="true"
            shift
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Env / prerequisites
# ---------------------------------------------------------------------------
require_var() {
    local name="$1" val
    val="${!name:-}"
    if [ -z "$val" ] && [ -f "$ENV_FILE" ]; then
        val="$(grep -s "^${name}=" "$ENV_FILE" | head -1 | cut -d'=' -f2-)"
    fi
    printf '%s' "$val"
}

if [ ! -x "$PYTHON_BIN" ]; then
    echo "ERROR: $PYTHON_BIN not found." >&2
    echo "  Run: python3.12 -m venv .venv && .venv/bin/pip install -r src/mcp/requirements-dev.txt -r src/mcp/requirements.txt" >&2
    exit 1
fi

NEO4J_PASSWORD="$(require_var NEO4J_PASSWORD)"
if [ -z "$NEO4J_PASSWORD" ]; then
    echo "ERROR: NEO4J_PASSWORD not set. Set it in $ENV_FILE or export it before running this script." >&2
    exit 1
fi

REDIS_PASSWORD="$(require_var REDIS_PASSWORD)"
if [ -n "$REDIS_PASSWORD" ]; then
    REDIS_URL="redis://:${REDIS_PASSWORD}@localhost:6379"
else
    REDIS_URL="redis://localhost:6379"
fi

# ---------------------------------------------------------------------------
# Backup (full export)
# ---------------------------------------------------------------------------
run_backup() {
    local timestamp staging archive_path size

    timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
    staging="$(mktemp -d "${TMPDIR:-/tmp}/cerid-backup-staging.XXXXXX")"
    mkdir -p "$BACKUP_DIR"
    archive_path="$BACKUP_DIR/cerid-backup-${timestamp}.tar.gz"

    echo "=== Cerid full logical backup: $timestamp ==="
    echo "Staging: $staging"
    echo ""

    if ! CERID_ROOT="$CERID_ROOT" NEO4J_PASSWORD="$NEO4J_PASSWORD" REDIS_URL="$REDIS_URL" \
        "$PYTHON_BIN" - "$staging" <<'PYEOF'
import json
import os
import sys

sys.path.insert(0, os.path.join(os.environ["CERID_ROOT"], "src", "mcp"))

import httpx
import neo4j
import redis

from app.sync.export import export_all

staging_dir = sys.argv[1]

try:
    driver = neo4j.GraphDatabase.driver(
        "bolt://localhost:7687", auth=("neo4j", os.environ["NEO4J_PASSWORD"])
    )
    with driver.session() as session:
        session.run("RETURN 1")
except Exception as exc:
    print(f"ERROR: Neo4j unreachable at bolt://localhost:7687: {exc}", file=sys.stderr)
    sys.exit(1)

try:
    redis_client = redis.from_url(
        os.environ["REDIS_URL"], decode_responses=True, socket_connect_timeout=5
    )
    redis_client.ping()
except Exception as exc:
    print(f"ERROR: Redis unreachable at localhost:6379: {exc}", file=sys.stderr)
    driver.close()
    sys.exit(1)

try:
    httpx.get("http://localhost:8001/api/v2/heartbeat", timeout=10.0).raise_for_status()
except Exception as exc:
    print(f"ERROR: ChromaDB unreachable at http://localhost:8001: {exc}", file=sys.stderr)
    driver.close()
    sys.exit(1)

result = export_all(
    driver=driver,
    chroma_url="http://localhost:8001",
    redis_client=redis_client,
    sync_dir=staging_dir,
    since=None,
)
driver.close()

neo4j_r = result.get("neo4j", {})
chroma_r = result.get("chroma", {})
redis_r = result.get("redis", {})
bm25_r = result.get("bm25", {})
memories_r = result.get("memories", {})
entities_r = result.get("entities", {})
conversations_r = result.get("conversations", {})

print(json.dumps(result, default=str))
print(
    "Exported: "
    f"{neo4j_r.get('artifacts', 0)} Neo4j artifacts, "
    f"{neo4j_r.get('domains', 0)} domains, "
    f"{neo4j_r.get('relationships', 0)} relationships, "
    f"{chroma_r.get('total_chunks', 0)} Chroma chunks, "
    f"{bm25_r.get('files_copied', 0)} BM25 files, "
    f"{memories_r.get('memories', 0)} memories, "
    f"{entities_r.get('entities', 0)} entities, "
    f"{redis_r.get('entries_exported', 0)} Redis audit entries, "
    f"{conversations_r.get('conversations', 0)} conversations"
)
PYEOF
    then
        echo "" >&2
        echo "ERROR: full export failed. $STACK_HINT" >&2
        rm -rf "$staging"
        exit 1
    fi

    tar czf "$archive_path" -C "$(dirname "$staging")" "$(basename "$staging")"
    rm -rf "$staging"

    size="$(du -h "$archive_path" | awk '{print $1}')"

    echo ""
    echo "=== Backup complete ==="
    echo "Archive: $archive_path"
    echo "Size:    $size"
    echo ""
    echo "NOTE: this is a LOGICAL (row-level) export, not a byte-level"
    echo "snapshot. For full disaster recovery of the raw store data"
    echo "(stacks/infrastructure/data/{neo4j,chroma,redis}), also run:"
    echo "  ./scripts/backup-kb.sh"
    echo ""
    echo "To restore: ./scripts/cerid-backup.sh --restore $archive_path"
}

# ---------------------------------------------------------------------------
# Restore (merge import)
# ---------------------------------------------------------------------------
run_restore() {
    local archive="$1" staging extracted manifest_dir

    if [ ! -f "$archive" ]; then
        echo "ERROR: archive not found: $archive" >&2
        exit 1
    fi

    echo "=== Cerid restore: $archive ==="
    echo ""
    echo "WARNING: this MERGES the archive's data into the LIVE Neo4j,"
    echo "ChromaDB, and Redis stores (conflict_strategy=remote_wins,"
    echo "force=false). It does not wipe existing data first, but"
    echo "conflicting records are overwritten by the archive's version."
    echo ""

    if [ "$ASSUME_YES" != "true" ]; then
        if [ -t 0 ]; then
            read -r -p "Proceed with restore into the live stack? [y/N] " reply
            case "$reply" in
                y|Y|yes|YES) ;;
                *)
                    echo "Aborted."
                    exit 1
                    ;;
            esac
        else
            echo "ERROR: not running interactively — pass --yes to confirm restore without a prompt." >&2
            exit 1
        fi
    fi

    staging="$(mktemp -d "${TMPDIR:-/tmp}/cerid-restore-staging.XXXXXX")"
    tar xzf "$archive" -C "$staging"

    manifest_dir="$(find "$staging" -maxdepth 3 -name manifest.json -print -quit)"
    if [ -z "$manifest_dir" ]; then
        echo "ERROR: no manifest.json found inside $archive — not a cerid-backup.sh archive?" >&2
        rm -rf "$staging"
        exit 1
    fi
    extracted="$(dirname "$manifest_dir")"

    echo ""
    echo "Extracted: $extracted"
    echo ""

    if ! CERID_ROOT="$CERID_ROOT" NEO4J_PASSWORD="$NEO4J_PASSWORD" REDIS_URL="$REDIS_URL" \
        "$PYTHON_BIN" - "$extracted" <<'PYEOF'
import json
import os
import sys

sys.path.insert(0, os.path.join(os.environ["CERID_ROOT"], "src", "mcp"))

import httpx
import neo4j
import redis

from app.sync.import_ import import_all

sync_dir = sys.argv[1]

try:
    driver = neo4j.GraphDatabase.driver(
        "bolt://localhost:7687", auth=("neo4j", os.environ["NEO4J_PASSWORD"])
    )
    with driver.session() as session:
        session.run("RETURN 1")
except Exception as exc:
    print(f"ERROR: Neo4j unreachable at bolt://localhost:7687: {exc}", file=sys.stderr)
    sys.exit(1)

try:
    redis_client = redis.from_url(
        os.environ["REDIS_URL"], decode_responses=True, socket_connect_timeout=5
    )
    redis_client.ping()
except Exception as exc:
    print(f"ERROR: Redis unreachable at localhost:6379: {exc}", file=sys.stderr)
    driver.close()
    sys.exit(1)

try:
    httpx.get("http://localhost:8001/api/v2/heartbeat", timeout=10.0).raise_for_status()
except Exception as exc:
    print(f"ERROR: ChromaDB unreachable at http://localhost:8001: {exc}", file=sys.stderr)
    driver.close()
    sys.exit(1)

result = import_all(
    driver=driver,
    chroma_url="http://localhost:8001",
    redis_client=redis_client,
    sync_dir=sync_dir,
    force=False,
    conflict_strategy="remote_wins",
)
driver.close()

neo4j_r = result.get("neo4j", {})
chroma_r = result.get("chroma", {})
redis_r = result.get("redis", {})
bm25_r = result.get("bm25", {})

print(json.dumps(result, default=str))
print(
    "Imported: "
    f"{neo4j_r.get('artifacts_created', 0)} Neo4j artifacts created, "
    f"{neo4j_r.get('artifacts_updated', 0)} updated, "
    f"{neo4j_r.get('artifacts_skipped', 0)} skipped, "
    f"{chroma_r.get('total_added', 0)} Chroma chunks added, "
    f"{chroma_r.get('total_skipped', 0)} skipped, "
    f"{bm25_r.get('chunks_added', 0)} BM25 chunks added, "
    f"{redis_r.get('entries_added', 0)} Redis audit entries added"
)
PYEOF
    then
        echo "" >&2
        echo "ERROR: restore failed. $STACK_HINT" >&2
        rm -rf "$staging"
        exit 1
    fi

    rm -rf "$staging"

    echo ""
    echo "=== Restore complete ==="
}

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if [ "$MODE" = "restore" ]; then
    run_restore "$RESTORE_ARCHIVE"
else
    run_backup
fi
