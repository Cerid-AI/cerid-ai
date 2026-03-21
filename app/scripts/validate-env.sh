#!/bin/bash
# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Cerid AI - Environment Validation
# Run before starting work to verify the stack is ready.
#
# Usage:
#   ./scripts/validate-env.sh          # full validation
#   ./scripts/validate-env.sh --quick  # containers only
#   ./scripts/validate-env.sh --fix    # auto-start missing infrastructure

CERID_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$CERID_ROOT/.env"
AGE_KEY="${CERID_AGE_KEY:-$HOME/.config/cerid/age-key.txt}"

QUICK=false
FIX=false

for arg in "$@"; do
    case "$arg" in
        --quick) QUICK=true ;;
        --fix)   FIX=true ;;
        *)
            echo "Unknown option: $arg"
            echo "Usage: $0 [--quick] [--fix]"
            exit 1
            ;;
    esac
done

PASS=0
FAIL=0

pass() { echo -e "\033[32m✓\033[0m $1"; PASS=$((PASS + 1)); }
fail() { echo -e "\033[31m✗\033[0m $1"; FAIL=$((FAIL + 1)); }
warn() { echo -e "\033[33m!\033[0m $1"; }

echo "=== Cerid AI Environment Validation ==="
echo ""

# ── Check 1: Docker daemon ────────────────────────────────────────────────────
if docker info > /dev/null 2>&1; then
    pass "Docker daemon is running"
else
    fail "Docker daemon is not running — start Docker Desktop or 'sudo systemctl start docker'"
fi

# ── Check 2: llm-network ──────────────────────────────────────────────────────
if docker network inspect llm-network > /dev/null 2>&1; then
    pass "Docker network 'llm-network' exists"
else
    if [ "$FIX" = true ]; then
        if docker network create llm-network > /dev/null 2>&1; then
            pass "Docker network 'llm-network' created (--fix)"
        else
            fail "Docker network 'llm-network' could not be created"
        fi
    else
        fail "Docker network 'llm-network' not found — run: docker network create llm-network"
    fi
fi

if [ "$QUICK" = false ]; then

    # ── Check 3: .env file and required vars ──────────────────────────────────
    if [ -f "$ENV_FILE" ]; then
        pass ".env file present at repo root"
        MISSING_VARS=()
        for var in NEO4J_PASSWORD OPENROUTER_API_KEY; do
            if ! grep -qE "^${var}=.+" "$ENV_FILE" 2>/dev/null; then
                MISSING_VARS+=("$var")
            fi
        done
        if [ ${#MISSING_VARS[@]} -eq 0 ]; then
            pass ".env contains required vars (NEO4J_PASSWORD, OPENROUTER_API_KEY)"
        else
            fail ".env is missing or has empty required vars: ${MISSING_VARS[*]}"
        fi
    else
        if [ -f "$CERID_ROOT/.env.age" ]; then
            warn ".env not found but .env.age exists — run: ./scripts/env-unlock.sh"
            fail ".env file not present (encrypted backup exists)"
        else
            fail ".env file not found at $ENV_FILE — copy .env.example and fill in values"
        fi
    fi

    # ── Check 4: age installed + key file ─────────────────────────────────────
    if command -v age > /dev/null 2>&1; then
        pass "'age' encryption tool is installed"
    else
        warn "'age' is not installed — secret encryption unavailable (brew install age)"
    fi

    if [ -f "$AGE_KEY" ]; then
        pass "age key file exists at $AGE_KEY"
    else
        warn "age key not found at $AGE_KEY — run: age-keygen -o $AGE_KEY"
    fi

fi  # end !QUICK for checks 3–4

# ── Check 5: Infrastructure containers ───────────────────────────────────────
INFRA_COMPOSE="$CERID_ROOT/stacks/infrastructure/docker-compose.yml"
INFRA_CONTAINERS=(ai-companion-neo4j ai-companion-chroma ai-companion-redis)

for container in "${INFRA_CONTAINERS[@]}"; do
    status="$(docker inspect --format '{{.State.Status}}' "$container" 2>/dev/null || echo "missing")"
    health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container" 2>/dev/null || echo "missing")"

    if [ "$status" = "running" ]; then
        if [ "$health" = "healthy" ] || [ "$health" = "none" ]; then
            pass "Container $container is running and healthy"
        elif [ "$health" = "starting" ]; then
            warn "Container $container is running but health check still starting"
            PASS=$((PASS + 1))
        else
            fail "Container $container is running but unhealthy (health: $health)"
        fi
    else
        if [ "$FIX" = true ]; then
            warn "Container $container not running — attempting auto-start (--fix)..."
            if docker compose -f "$INFRA_COMPOSE" --env-file "$ENV_FILE" up -d > /dev/null 2>&1; then
                pass "Infrastructure stack started via --fix (verify health in ~30s)"
                # Skip remaining infra containers — they all started together
                break
            else
                fail "Failed to auto-start infrastructure via $INFRA_COMPOSE"
                break
            fi
        else
            fail "Container $container is not running (status: $status) — run: ./scripts/start-cerid.sh"
        fi
    fi
done

# ── Check 6: MCP container ────────────────────────────────────────────────────
mcp_status="$(docker inspect --format '{{.State.Status}}' ai-companion-mcp 2>/dev/null || echo "missing")"
mcp_health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' ai-companion-mcp 2>/dev/null || echo "missing")"

if [ "$mcp_status" = "running" ]; then
    if [ "$mcp_health" = "healthy" ] || [ "$mcp_health" = "none" ]; then
        pass "Container ai-companion-mcp is running and healthy"
    elif [ "$mcp_health" = "starting" ]; then
        warn "Container ai-companion-mcp is running but health check still starting"
        PASS=$((PASS + 1))
    else
        fail "Container ai-companion-mcp is running but unhealthy (health: $mcp_health)"
    fi
else
    if [ "$FIX" = true ]; then
        warn "Container ai-companion-mcp not running — attempting auto-start (--fix)..."
        if docker compose -f "$CERID_ROOT/src/mcp/docker-compose.yml" --env-file "$ENV_FILE" up -d > /dev/null 2>&1; then
            pass "MCP stack started via --fix"
        else
            fail "Failed to auto-start MCP stack"
        fi
    else
        fail "Container ai-companion-mcp is not running (status: $mcp_status) — run: ./scripts/start-cerid.sh"
    fi
fi

if [ "$QUICK" = false ]; then

    # ── Check 7: Data directories ─────────────────────────────────────────────
    DATA_ROOT="$CERID_ROOT/stacks/infrastructure/data"
    if [ -d "$DATA_ROOT" ]; then
        pass "Data directory exists at stacks/infrastructure/data/"
        MISSING_SUBDIRS=()
        for subdir in neo4j neo4j-logs chroma redis; do
            if [ ! -d "$DATA_ROOT/$subdir" ]; then
                MISSING_SUBDIRS+=("$subdir")
            fi
        done
        if [ ${#MISSING_SUBDIRS[@]} -eq 0 ]; then
            pass "All data subdirectories present (neo4j, neo4j-logs, chroma, redis)"
        else
            fail "Missing data subdirectories: ${MISSING_SUBDIRS[*]} — they will be created on first compose up"
        fi
    else
        fail "Data directory not found at $DATA_ROOT — run 'mkdir -p $DATA_ROOT/{neo4j,neo4j-logs,chroma,redis}'"
    fi

    # ── Check 8: Knowledge archive symlink ────────────────────────────────────
    ARCHIVE_DIR="$HOME/cerid-archive"
    DROPBOX_ARCHIVE="$HOME/Dropbox/cerid-archive"

    if [ -L "$ARCHIVE_DIR" ]; then
        LINK_TARGET="$(readlink "$ARCHIVE_DIR")"
        if [ -d "$ARCHIVE_DIR" ]; then
            FILE_COUNT="$(find -L "$ARCHIVE_DIR" -type f 2>/dev/null | wc -l | tr -d ' ')"
            pass "Archive symlink: $ARCHIVE_DIR → $LINK_TARGET ($FILE_COUNT files)"
        else
            fail "Archive symlink exists but target is missing: $ARCHIVE_DIR → $LINK_TARGET"
        fi
    elif [ -d "$ARCHIVE_DIR" ]; then
        if [ -d "$DROPBOX_ARCHIVE" ]; then
            fail "~/cerid-archive is a regular directory, not a symlink — run: rm -rf ~/cerid-archive && ln -s ~/Dropbox/cerid-archive ~/cerid-archive"
        else
            warn "~/cerid-archive exists as a directory (no Dropbox sync) — KB won't sync across machines"
            PASS=$((PASS + 1))
        fi
    else
        if [ -d "$DROPBOX_ARCHIVE" ]; then
            fail "Archive directory missing — run: ln -s ~/Dropbox/cerid-archive ~/cerid-archive"
        else
            fail "Archive directory missing — create ~/cerid-archive with domain subdirectories (coding, finance, general, inbox, personal, projects)"
        fi
    fi

    # ── Check 9: Sync directory ───────────────────────────────────────────────
    SYNC_DIR="${CERID_SYNC_DIR:-}"
    DEFAULT_SYNC="$HOME/Dropbox/cerid-sync"

    if [ -n "$SYNC_DIR" ]; then
        if [ -d "$SYNC_DIR" ]; then
            pass "Sync directory accessible at \$CERID_SYNC_DIR ($SYNC_DIR)"
        else
            fail "Sync directory \$CERID_SYNC_DIR set but not accessible: $SYNC_DIR"
        fi
    elif [ -d "$DEFAULT_SYNC" ]; then
        pass "Sync directory accessible at $DEFAULT_SYNC"
    else
        warn "No sync directory found (\$CERID_SYNC_DIR unset; $DEFAULT_SYNC does not exist) — set CERID_SYNC_DIR if needed"
    fi

    # ── Check 10: Sync directory writable ────────────────────────────────────
    SYNC_TEST_DIR="${CERID_SYNC_DIR:-$HOME/Dropbox/cerid-sync}"
    if [ -d "$SYNC_TEST_DIR" ]; then
        if [ -w "$SYNC_TEST_DIR" ]; then
            pass "Sync directory is writable: $SYNC_TEST_DIR"
        else
            fail "Sync directory exists but is not writable: $SYNC_TEST_DIR"
        fi
    fi

fi  # end !QUICK for checks 7–10

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "=== Validation Summary ==="
TOTAL=$((PASS + FAIL))
echo -e "\033[32m$PASS passed\033[0m, \033[31m$FAIL failed\033[0m (of $TOTAL checks)"

if [ "$FAIL" -gt 0 ]; then
    echo ""
    echo "Stack may not be ready. Review the failures above or run:"
    echo "  ./scripts/validate-env.sh --fix   # auto-start missing infrastructure"
    echo "  ./scripts/start-cerid.sh          # full stack startup"
    exit 1
else
    echo ""
    echo "All checks passed. Stack looks ready."
    exit 0
fi