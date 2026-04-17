#!/usr/bin/env bash
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# scripts/lib/healthcheck.sh — shared infrastructure health-probe library
#
# Sourced by scripts/start-cerid.sh and scripts/validate-env.sh as the single
# source of truth for container / service / database health detection.
#
# Design goals:
#   - Auth-aware probes (Redis password, Neo4j user/pass) driven by .env.
#   - Tri-state HTTP check so optional services (e.g. Bifrost) never surface
#     "HTTP 000" when they are simply not configured.
#   - Consistent visual symbols: ✓ OK / ✗ FAIL / ⚠ DEGRADED / ⊘ SKIP.
#   - No side effects beyond stdout and the PASS/FAIL counters. Callers
#     manage their own check numbering.
#
# Usage:
#   source "$(dirname "$0")/lib/healthcheck.sh"
#   check_container ai-companion-redis
#   check_redis     ai-companion-redis "$REDIS_PASSWORD"
#   check_http      Bifrost "${BIFROST_URL:-}"
#   check_neo4j     ai-companion-neo4j "$NEO4J_USER" "$NEO4J_PASSWORD"
#   cleanup_zombies

# ── Re-entry guard ───────────────────────────────────────────────────────────
if [ -n "${CERID_HEALTHCHECK_LIB_LOADED:-}" ]; then
    return 0 2>/dev/null || true
fi
CERID_HEALTHCHECK_LIB_LOADED=1

# ── .env ingestion ───────────────────────────────────────────────────────────
# Resolve repo root relative to this file so both start-cerid.sh and
# validate-env.sh pick up the same .env regardless of caller cwd.
_CERID_HC_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_CERID_HC_ROOT="$(cd "$_CERID_HC_LIB_DIR/../.." && pwd)"
_CERID_HC_ENV="${CERID_ENV_FILE:-$_CERID_HC_ROOT/.env}"

# Load ONLY the auth/config vars we actually need from .env so we don't
# clobber caller-side settings. Critically we do NOT source .env wholesale
# because it contains container-internal paths (CERID_SYNC_DIR=/sync,
# ARCHIVE_PATH=/archive) that would break host-side validations if exported.
_hc_load_env_var() {
    local key="$1"
    # Already exported by caller — respect it.
    if [ -n "${!key:-}" ]; then
        return 0
    fi
    [ -f "$_CERID_HC_ENV" ] || return 0
    local line val
    line=$(grep -E "^${key}=" "$_CERID_HC_ENV" 2>/dev/null | head -1 || true)
    [ -z "$line" ] && return 0
    val="${line#*=}"
    export "$key=$val"
}

for _hc_key in REDIS_PASSWORD NEO4J_USER NEO4J_PASSWORD BIFROST_URL; do
    _hc_load_env_var "$_hc_key"
done
unset _hc_key

# ── Counters (shared with caller via env) ────────────────────────────────────
# Callers may initialize PASS / FAIL before sourcing. Default to 0.
: "${PASS:=0}"
: "${FAIL:=0}"

# ── Symbol / color helpers ──────────────────────────────────────────────────
# Colors match the existing inline usage: 32=green, 31=red, 33=yellow, 36=cyan.
pass() { echo -e "\033[32m✓ OK\033[0m $1"; PASS=$((PASS + 1)); }
fail() { echo -e "\033[31m✗ FAIL\033[0m $1"; FAIL=$((FAIL + 1)); }
warn() { echo -e "\033[33m⚠ DEGRADED\033[0m $1"; }
skip() { echo -e "\033[36m⊘ SKIP\033[0m $1"; }

# ── Internal: does a container exist (any state)? ───────────────────────────
_hc_container_exists() {
    docker inspect --format '{{.Name}}' "$1" >/dev/null 2>&1
}

_hc_container_status() {
    local out
    out=$(docker inspect --format '{{.State.Status}}' "$1" 2>/dev/null) || out=""
    if [ -z "$out" ]; then
        echo "missing"
    else
        echo "$out"
    fi
}

_hc_container_health() {
    local out
    out=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$1" 2>/dev/null) || out=""
    if [ -z "$out" ]; then
        echo "missing"
    else
        echo "$out"
    fi
}

# ── check_container <name> ──────────────────────────────────────────────────
# Returns:
#   0 — container running and healthy (or no healthcheck defined)
#   1 — container missing, stopped, or unhealthy
# Side effect: prints pass / fail / warn line and increments PASS/FAIL.
check_container() {
    local name="$1"
    local status health
    status="$(_hc_container_status "$name")"
    health="$(_hc_container_health "$name")"

    if [ "$status" = "running" ]; then
        if [ "$health" = "healthy" ] || [ "$health" = "none" ]; then
            pass "Container $name is running and healthy"
            return 0
        elif [ "$health" = "starting" ]; then
            warn "Container $name is running but health check still starting"
            PASS=$((PASS + 1))
            return 0
        else
            fail "Container $name is running but unhealthy (health: $health)"
            return 1
        fi
    fi
    fail "Container $name is not running (status: $status)"
    return 1
}

# ── check_http <name> <url> [expected_code] ─────────────────────────────────
# Tri-state:
#   0 — URL returned 200 (or expected code)
#   1 — URL returned a non-expected status
#   2 — URL is empty/unset → skip (not configured)
# Prints pass / fail / skip and increments PASS/FAIL only on pass/fail.
# Skip is informational and does NOT count against totals.
check_http() {
    local name="$1" url="${2:-}" expected="${3:-200}"

    # Unset or literal empty → not configured
    if [ -z "$url" ]; then
        skip "$name — not configured"
        return 2
    fi

    if ! command -v curl >/dev/null 2>&1; then
        warn "$name — curl unavailable, cannot probe $url"
        return 1
    fi

    local code
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "$url" 2>/dev/null || echo "000")

    if [ "$code" = "$expected" ]; then
        pass "$name reachable at $url (HTTP $code)"
        return 0
    fi

    if [ "$code" = "000" ]; then
        fail "$name not reachable at $url (no response)"
    else
        fail "$name at $url returned HTTP $code (expected $expected)"
    fi
    return 1
}

# ── check_redis <container> <password> ──────────────────────────────────────
# Uses `docker exec` against the running container so we don't need redis-cli
# on the host. AUTH is only passed when a password is configured — this is
# what validate-env.sh got right and what start-cerid.sh got wrong.
check_redis() {
    local container="${1:-ai-companion-redis}"
    local password="${2:-${REDIS_PASSWORD:-}}"

    if ! _hc_container_exists "$container"; then
        fail "Redis container $container not found"
        return 1
    fi
    if [ "$(_hc_container_status "$container")" != "running" ]; then
        fail "Redis container $container is not running"
        return 1
    fi

    # REDISCLI_AUTH is the documented env-var alternative to -a <password>;
    # it never appears in the container's process list (ps/audit logs/docker
    # inspect), so using it instead of the flag eliminates the leak risk if
    # the container's stdout or `docker exec` invocations are ever captured.
    local out
    if [ -n "$password" ]; then
        out=$(docker exec -e "REDISCLI_AUTH=$password" "$container" \
              redis-cli --no-auth-warning ping 2>/dev/null || echo "")
    else
        out=$(docker exec "$container" redis-cli ping 2>/dev/null || echo "")
    fi

    if [ "$out" = "PONG" ]; then
        pass "Redis ($container) responding to authenticated PING"
        return 0
    fi
    fail "Redis ($container) did not respond to PING (check REDIS_PASSWORD)"
    return 1
}

# ── check_neo4j <container> <user> <pass> ───────────────────────────────────
# Runs an authenticated `RETURN 1` Cypher probe inside the container — the
# same smoke test the MCP backend uses in deps.py.
check_neo4j() {
    local container="${1:-ai-companion-neo4j}"
    local user="${2:-${NEO4J_USER:-neo4j}}"
    local password="${3:-${NEO4J_PASSWORD:-}}"

    if ! _hc_container_exists "$container"; then
        fail "Neo4j container $container not found"
        return 1
    fi
    if [ "$(_hc_container_status "$container")" != "running" ]; then
        fail "Neo4j container $container is not running"
        return 1
    fi
    if [ -z "$password" ]; then
        fail "Neo4j ($container) — NEO4J_PASSWORD is empty"
        return 1
    fi

    # cypher-shell exits 0 on a successful query, non-zero on auth or syntax failure.
    if docker exec "$container" \
        cypher-shell -u "$user" -p "$password" --format plain "RETURN 1 AS ok;" \
        >/dev/null 2>&1; then
        pass "Neo4j ($container) authenticated Cypher probe succeeded"
        return 0
    fi
    fail "Neo4j ($container) authenticated Cypher probe failed (check NEO4J_USER/PASSWORD)"
    return 1
}

# ── cleanup_zombies ─────────────────────────────────────────────────────────
# Detects containers whose names match our project prefixes but are in a
# non-running state (exited, dead, created). Docker reserves the name so a
# subsequent `docker compose up` fails with a raw name-conflict error and no
# hint at remediation. This function either prompts (TTY) or auto-removes
# (non-interactive), logging each action.
#
# Prefixes: ai-companion-* and cerid-*  (matches MCP/infra + GUI/ollama).
cleanup_zombies() {
    # Pull the list of stopped containers matching our prefixes. `docker ps -a`
    # with a status filter is the simplest cross-platform approach.
    local zombies
    zombies=$(docker ps -a \
        --filter "status=exited" \
        --filter "status=dead" \
        --filter "status=created" \
        --format '{{.Names}}' 2>/dev/null \
        | grep -E '^(ai-companion-|cerid-)' || true)

    if [ -z "$zombies" ]; then
        return 0
    fi

    echo ""
    echo "[cleanup] Found stopped containers that will block 'docker compose up':"
    while IFS= read -r z; do
        echo "  - $z"
    done <<< "$zombies"

    local auto_remove=false
    # Auto-remove when non-interactive OR when explicitly requested.
    if [ "${CERID_AUTO_CLEANUP:-}" = "true" ] || [ ! -t 0 ]; then
        auto_remove=true
    else
        local answer=""
        # Read from /dev/tty so we work under `bash -c` wrappers too.
        read -r -p "[cleanup] Force-remove these containers? [Y/n]: " answer </dev/tty 2>/dev/null || answer="y"
        case "${answer:-y}" in
            n|N|no|NO) auto_remove=false ;;
            *)         auto_remove=true ;;
        esac
    fi

    if [ "$auto_remove" != "true" ]; then
        warn "Skipping zombie cleanup — 'docker compose up' may fail with name conflicts"
        return 0
    fi

    while IFS= read -r z; do
        if docker rm -f "$z" >/dev/null 2>&1; then
            echo "[cleanup] Removed $z"
        else
            echo "[cleanup] WARN: failed to remove $z" >&2
        fi
    done <<< "$zombies"
}
