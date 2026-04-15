#!/usr/bin/env bash
# Copyright (c) 2026 Cerid AI. All rights reserved.
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

# Counters are shared with the healthcheck library (which increments them
# via pass/fail/warn). Initialize BEFORE sourcing.
PASS=0
FAIL=0

# Shared health-check library — single source of truth for container,
# HTTP, Redis (auth-aware), and Neo4j (Cypher) probes.
export CERID_ENV_FILE="$ENV_FILE"
# shellcheck source=lib/healthcheck.sh
source "$CERID_ROOT/scripts/lib/healthcheck.sh"

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

# Cross-platform readlink — GNU readlink -f works on Linux, BSD readlink on macOS
resolve_link() {
    if readlink -f "$1" &>/dev/null; then
        readlink -f "$1"  # GNU readlink (Linux)
    else
        readlink "$1"      # BSD readlink (macOS)
    fi
}

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

    # ── Check 4: age installed + key file (only relevant when encrypted .env.age exists) ──
    # Preserved from Charlie's guard — dev machines without encrypted backup
    # don't fail just because `age` is absent.
    if [ -f "$CERID_ROOT/.env.age" ]; then
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
    fi

fi  # end !QUICK for checks 3–4

# ── Check 5: Infrastructure containers ───────────────────────────────────────
# Container state + health (neo4j, chroma, redis). Uses the shared library so
# validate-env and start-cerid agree on what "healthy" means.
INFRA_COMPOSE="$CERID_ROOT/stacks/infrastructure/docker-compose.yml"
INFRA_CONTAINERS=(ai-companion-neo4j ai-companion-chroma ai-companion-redis)

for container in "${INFRA_CONTAINERS[@]}"; do
    if check_container "$container"; then
        continue
    fi
    if [ "$FIX" = true ]; then
        warn "Container $container not healthy — attempting auto-start (--fix)..."
        if docker compose -f "$INFRA_COMPOSE" --env-file "$ENV_FILE" up -d > /dev/null 2>&1; then
            pass "Infrastructure stack started via --fix (verify health in ~30s)"
            break  # siblings come up as a group
        else
            fail "Failed to auto-start infrastructure via $INFRA_COMPOSE"
            break
        fi
    else
        warn "Remediation: ./scripts/start-cerid.sh  (or re-run with --fix)"
    fi
done

# Authenticated Redis PING — catches the case where the container is healthy
# but REDIS_PASSWORD is wrong / stale. This is the check that used to be wrong
# in start-cerid.sh (reported UNREACHABLE). Now both scripts run the same probe.
if [ "$(_hc_container_status ai-companion-redis)" = "running" ]; then
    check_redis ai-companion-redis "${REDIS_PASSWORD:-}" || true
fi

# Authenticated Neo4j Cypher probe — same smoke test deps.py runs on startup.
if [ "$(_hc_container_status ai-companion-neo4j)" = "running" ]; then
    check_neo4j ai-companion-neo4j "${NEO4J_USER:-neo4j}" "${NEO4J_PASSWORD:-}" || true
fi

# ── Check 6: MCP container ────────────────────────────────────────────────────
if ! check_container ai-companion-mcp; then
    if [ "$FIX" = true ]; then
        warn "Container ai-companion-mcp not healthy — attempting auto-start (--fix)..."
        if docker compose -f "$CERID_ROOT/src/mcp/docker-compose.yml" --env-file "$ENV_FILE" up -d > /dev/null 2>&1; then
            pass "MCP stack started via --fix"
        else
            fail "Failed to auto-start MCP stack"
        fi
    else
        warn "Remediation: ./scripts/start-cerid.sh"
    fi
fi

# ── Optional: Ollama check ─────────────────────────────────────────────────
OLLAMA_ENABLED_VAL=$(grep -s '^OLLAMA_ENABLED=true' "$ENV_FILE" 2>/dev/null || echo "")
if [ -n "$OLLAMA_ENABLED_VAL" ]; then
    ollama_container_status="$(_hc_container_status cerid-ollama)"
    if [ "$ollama_container_status" = "running" ]; then
        check_container cerid-ollama || true
    elif [ "$ollama_container_status" = "missing" ]; then
        # Native Ollama on macOS lives outside Docker — probe the HTTP API.
        OLLAMA_URL_VAL=$(grep -s '^OLLAMA_URL=' "$ENV_FILE" 2>/dev/null | cut -d'=' -f2- || echo "http://localhost:11434")
        if curl -sf "$OLLAMA_URL_VAL/api/tags" >/dev/null 2>&1; then
            pass "Ollama (native) is reachable at $OLLAMA_URL_VAL"
        else
            warn "Ollama enabled but not running — start with: ollama serve (native) or docker compose --profile ollama up -d"
        fi
    else
        warn "Container cerid-ollama is $ollama_container_status"
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
        LINK_TARGET="$(resolve_link "$ARCHIVE_DIR")"
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
