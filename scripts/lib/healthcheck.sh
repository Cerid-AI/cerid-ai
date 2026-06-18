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
#   - Tri-state HTTP check so optional services never surface "HTTP 000"
#     when they are simply not configured.
#   - Consistent visual symbols: ✓ OK / ✗ FAIL / ⚠ DEGRADED / ⊘ SKIP.
#   - No side effects beyond stdout and the PASS/FAIL counters. Callers
#     manage their own check numbering.
#
# Usage:
#   source "$(dirname "$0")/lib/healthcheck.sh"
#   check_container ai-companion-redis
#   check_redis     ai-companion-redis "$REDIS_PASSWORD"
#   check_http      MCP "http://localhost:8888/health"
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

for _hc_key in REDIS_PASSWORD NEO4J_USER NEO4J_PASSWORD; do
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

# ── detect_conflicts ─────────────────────────────────────────────────────────
# Defensive preflight for the cross-project/cross-dir squatter class that
# cleanup_zombies MISSES — it only reaps *stopped* containers and treats every
# ai-companion-*/cerid-* as ours. A *running* container from a DIFFERENT compose
# project or working dir that holds one of our exact container_names makes
# `docker compose up` die with a raw daemon "name is already in use" error and
# zero remediation hint.
#
# Real trigger (2026-06-06): the public-mirror repo's compose (same hardcoded
# container_names + ports) was run from ~/Develop/cerid-ai, creating project
# "cerid-ai" that held the personal names while running; the personal rebuild
# (project "cerid-ai-internal") then collided.
#
# Usage: detect_conflicts <our_project> <our_dir> <name1> [name2 ...]
# Returns non-zero (caller aborts) if unresolved foreign holders remain.
#   CERID_RECLAIM=true  → stop+rm the foreign holders and continue
#   interactive TTY     → prompt to reclaim
#   non-interactive     → abort with a precise message + the one-liner fix
#                         (never auto-destroys another instance unprompted)
detect_conflicts() {
    local our_project="$1" our_dir="$2"; shift 2
    local foreign=()
    local name cid proj dir state
    for name in "$@"; do
        [ -z "$name" ] && continue
        cid=$(docker ps -aq --filter "name=^/${name}$" 2>/dev/null | head -1)
        [ -z "$cid" ] && continue
        proj=$(docker inspect "$cid" --format '{{ index .Config.Labels "com.docker.compose.project" }}' 2>/dev/null || true)
        dir=$(docker inspect "$cid" --format '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' 2>/dev/null || true)
        state=$(docker inspect "$cid" --format '{{.State.Status}}' 2>/dev/null || true)
        # Ours iff same project AND (no dir label OR same dir). Else foreign.
        if [ "$proj" = "$our_project" ] && { [ -z "$dir" ] || [ "$dir" = "$our_dir" ]; }; then
            continue
        fi
        foreign+=("${name}|${proj:-<none>}|${dir:-<none>}|${state}")
    done

    [ "${#foreign[@]}" -eq 0 ] && return 0

    {
        echo ""
        echo "[conflict] Container names needed by THIS instance are held by a"
        echo "[conflict] different instance (project/dir mismatch) — 'docker compose"
        echo "[conflict] up' would fail with a raw name-conflict. This instance:"
        echo "             project=${our_project}  dir=${our_dir}"
        local entry n p d s
        for entry in "${foreign[@]}"; do
            IFS='|' read -r n p d s <<< "$entry"
            echo "  - ${n}  ←  project=${p}  dir=${d}  (${s})"
        done
    } >&2

    local reclaim=false
    if [ "${CERID_RECLAIM:-}" = "true" ]; then
        reclaim=true
    elif [ -t 0 ]; then
        local ans=""
        read -r -p "[conflict] Reclaim these names for this instance (stop+rm them)? [y/N]: " ans </dev/tty 2>/dev/null || ans="n"
        case "${ans}" in y|Y|yes|YES) reclaim=true ;; *) reclaim=false ;; esac
    fi

    if [ "$reclaim" != "true" ]; then
        {
            echo "[conflict] Aborting — refusing to silently take over another instance's"
            echo "[conflict] containers. To take them over for this instance, re-run with:"
            echo "             CERID_RECLAIM=true \"\$0\"   (or: ./scripts/start-cerid.sh --reclaim)"
            echo "[conflict] Or stop the other instance first (e.g. \`docker compose -p <project> down\`)."
        } >&2
        return 1
    fi

    local entry n p d s
    for entry in "${foreign[@]}"; do
        IFS='|' read -r n p d s <<< "$entry"
        if docker rm -f "$n" >/dev/null 2>&1; then
            echo "[conflict] Reclaimed ${n} (removed foreign container from project=${p})." >&2
        else
            echo "[conflict] ERROR: failed to remove ${n}" >&2
            return 1
        fi
    done
    return 0
}

# ── detect_port_conflicts ────────────────────────────────────────────────────
# Reports host ports that are already bound by something that is NOT one of our
# containers, before `docker compose up` fails with a bind error. Best-effort:
# uses lsof (present on macOS/most Linux); silent no-op if lsof is unavailable.
# Usage: detect_port_conflicts <port1> [port2 ...]   (warn-only, never aborts)
detect_port_conflicts() {
    command -v lsof >/dev/null 2>&1 || return 0
    local port pids hits=0
    for port in "$@"; do
        [ -z "$port" ] && continue
        pids=$(lsof -nP -iTCP:"${port}" -sTCP:LISTEN -t 2>/dev/null || true)
        [ -z "$pids" ] && continue
        # If the listener is com.docker.backend / vpnkit (a published container
        # port), we can't easily attribute it here — leave conflict reclaim to
        # detect_conflicts. Only warn for clearly-foreign non-docker listeners.
        local cmd
        cmd=$(ps -o comm= -p "$(echo "$pids" | head -1)" 2>/dev/null || true)
        case "$cmd" in
            *docker*|*vpnkit*|*com.docker*) : ;;  # docker-published; handled by detect_conflicts
            *) warn "Port ${port} already in use by '${cmd:-pid $pids}' — Cerid needs it; stop that process or change the CERID_PORT_* override."; hits=$((hits+1)) ;;
        esac
    done
    return 0
}

# ── detect_datadir_conflicts ─────────────────────────────────────────────────
# THE guard against the silent data-CORRUPTION class, which detect_conflicts
# (a container-NAME guard) does not cover: two containers from DIFFERENT
# instances bind-mounting the SAME host data dir. Redis (AOF) and Neo4j are not
# multi-process-safe on one store — the second opener corrupts the first's data
# and crash-loops. The name and the mount are independent: different container
# names can still point at the same dir, so this check is separate.
#
# Real trigger (2026-06-07): the public mirror's docker-compose.yml (which had
# drifted to name: cerid-ai-internal) was run from ~/Develop/cerid-ai, mounting
# cerid-ai/stacks/.../data/redis — the SAME dir the cerid-public-sandbox redis
# already held → AOF corruption + an opaque crash-loop.
#
# Usage: detect_datadir_conflicts <our_project> <our_dir> <abs_data_dir...>
# Returns non-zero (caller aborts) if a foreign RUNNING container mounts one of
# our data dirs. Never auto-destroys — corrupting the held instance's live data
# is exactly what we're preventing, so the caller is told to stop it explicitly.
detect_datadir_conflicts() {
    local our_project="$1" our_dir="$2"; shift 2
    [ "$#" -eq 0 ] && return 0
    local clashes=()
    local cid proj wdir cname srcs src ddir
    for cid in $(docker ps -q 2>/dev/null); do
        proj=$(docker inspect "$cid" --format '{{ index .Config.Labels "com.docker.compose.project" }}' 2>/dev/null || true)
        wdir=$(docker inspect "$cid" --format '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' 2>/dev/null || true)
        # Ours iff same project AND (no dir label OR same dir) — mirror detect_conflicts.
        if [ "$proj" = "$our_project" ] && { [ -z "$wdir" ] || [ "$wdir" = "$our_dir" ]; }; then
            continue
        fi
        cname=$(docker inspect "$cid" --format '{{.Name}}' 2>/dev/null | sed 's#^/##')
        srcs=$(docker inspect "$cid" --format '{{range .Mounts}}{{if eq .Type "bind"}}{{.Source}}{{"\n"}}{{end}}{{end}}' 2>/dev/null || true)
        for ddir in "$@"; do
            [ -z "$ddir" ] && continue
            while IFS= read -r src; do
                [ -z "$src" ] && continue
                [ "$src" = "$ddir" ] && clashes+=("${ddir}|${cname}|${proj:-<none>}|${wdir:-<none>}")
            done <<< "$srcs"
        done
    done

    [ "${#clashes[@]}" -eq 0 ] && return 0

    {
        echo ""
        echo "[datadir] ABORT — a DIFFERENT running instance is already bind-mounting a"
        echo "[datadir] data directory THIS instance needs. Starting now would put two"
        echo "[datadir] processes on one store (Redis AOF / Neo4j) and CORRUPT it."
        echo "[datadir]   this instance: project=${our_project} dir=${our_dir}"
        local e dd cn p w
        for e in "${clashes[@]}"; do
            IFS='|' read -r dd cn p w <<< "$e"
            echo "  - ${dd}"
            echo "        held by: ${cn}  (project=${p}  dir=${w})"
        done
        echo "[datadir] Fix: stop the other instance first (e.g. \`docker compose -p <project> down\`)"
        echo "[datadir] or point it at its own data dir. Not auto-resolved — that would risk"
        echo "[datadir] the other instance's live data."
    } >&2
    return 1
}

# ── ensure_redis_aof_healthy ─────────────────────────────────────────────────
# Self-heal for the opaque redis crash-loop class: a corrupt/truncated AOF (from
# an unclean shutdown or a past double-mount) makes redis exit during load and
# crash-loop, which strands mcp/web in `Created` (they gate on redis health)
# with no obvious signal. Validate the AOF before compose up; on corruption,
# back up the appendonlydir and repair in place — redis-check-aof --fix truncates
# only the trailing bad record (the fix the error message itself prescribes).
#
# Uses a throwaway redis container so it works without a host redis-server.
# Usage: ensure_redis_aof_healthy <redis_data_dir>   (warn-only; never aborts)
ensure_redis_aof_healthy() {
    local data_dir="$1"
    local aof_dir="$data_dir/appendonlydir"
    # Keep this in step with the redis image pinned in docker-compose.yml.
    local redis_image="redis:7.4.8-alpine"
    [ -f "$aof_dir/appendonly.aof.manifest" ] || return 0   # no multi-part AOF yet
    command -v docker >/dev/null 2>&1 || return 0

    if docker run --rm -v "$aof_dir":/aof "$redis_image" \
        sh -c 'cd /aof && redis-check-aof appendonly.aof.manifest' >/dev/null 2>&1; then
        return 0                                            # valid → fast path
    fi

    warn "Redis AOF at $aof_dir looks corrupt — repairing before start (prevents a crash-loop)."
    local backup
    backup="${aof_dir}.bak-$(date +%Y%m%d-%H%M%S)"
    if ! cp -R "$aof_dir" "$backup" 2>/dev/null; then
        echo "[redis] WARN: could not back up the AOF; skipping repair to avoid data loss." >&2
        return 0
    fi
    echo "[redis] Backed up corrupt AOF → $backup"
    if docker run --rm -v "$aof_dir":/aof "$redis_image" \
        sh -c 'cd /aof && printf "y\n" | redis-check-aof --fix appendonly.aof.manifest' >/dev/null 2>&1; then
        pass "Redis AOF repaired (backup at $backup)"
    else
        echo "[redis] WARN: redis-check-aof --fix failed; restore from $backup if redis won't start." >&2
    fi
}
