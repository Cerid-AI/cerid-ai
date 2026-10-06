#!/bin/bash
# Beta harness target — the one place the harness knows which stack it is
# talking to. Source it before lib/assert.sh:
#   source "${SCRIPT_DIR}/lib/target.sh" || exit 2
#
#   BETA_TARGET=live      (default) the personal stack scripts/start-cerid.sh
#                         boots: ai-companion-* on the external llm-network.
#   BETA_TARGET=isolated  the sandbox overlay (scripts/start-sandbox.sh +
#                         docker-compose.sandbox.yml): ai-companion-*-sandbox
#                         on its own bridge, host ports offset by +10.
#
# Host ports honour the CERID_PORT_* overrides docker-compose.yml and the start
# scripts read, so a stack started with CERID_PORT_MCP=18888 is reached by
# exporting the same variable before run.sh. The in-network URL (BETA_MCP_BASE,
# used by the tiers that run inside docker) is the same on every stack: each
# overlay aliases its MCP container as ai-companion-mcp on its own bridge, and
# the container port never moves.
#
# tests/beta/lib/target.py is the Python twin for the conftests;
# scripts/tests/test_beta_target.py holds the two to the same values.

BETA_TARGET="${BETA_TARGET:-live}"

case "$BETA_TARGET" in
  live)
    BETA_CONTAINER_SUFFIX=""
    BETA_DOCKER_NETWORK="llm-network"
    BETA_START_HINT="scripts/start-cerid.sh"
    BETA_MCP_PORT="${CERID_PORT_MCP:-8888}"
    BETA_GUI_PORT="${CERID_PORT_GUI:-3000}"
    BETA_NEO4J_PORT="${CERID_PORT_NEO4J:-7474}"
    BETA_CHROMA_PORT="${CERID_PORT_CHROMA:-8001}"
    BETA_REDIS_PORT="${CERID_PORT_REDIS:-6379}"
    ;;
  isolated)
    BETA_CONTAINER_SUFFIX="-sandbox"
    BETA_DOCKER_NETWORK="cerid-sandbox-llm-network"
    BETA_START_HINT="scripts/start-sandbox.sh"
    BETA_MCP_PORT="${CERID_PORT_MCP:-8898}"
    BETA_GUI_PORT="${CERID_PORT_GUI:-3010}"
    BETA_NEO4J_PORT="${CERID_PORT_NEO4J:-7484}"
    BETA_CHROMA_PORT="${CERID_PORT_CHROMA:-8011}"
    BETA_REDIS_PORT="${CERID_PORT_REDIS:-6389}"
    # The sandbox is its own compose project; assert.sh keeps a project that
    # is already set instead of deriving the checkout's basename.
    export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-${CERID_SANDBOX_PROJECT:-cerid-sandbox}}"
    ;;
  *)
    echo "target.sh: unknown BETA_TARGET '${BETA_TARGET}' (expected live or isolated)" >&2
    return 1
    ;;
esac

BETA_MCP_CONTAINER="ai-companion-mcp${BETA_CONTAINER_SUFFIX}"
BETA_NEO4J_CONTAINER="ai-companion-neo4j${BETA_CONTAINER_SUFFIX}"
BETA_CHROMA_CONTAINER="ai-companion-chroma${BETA_CONTAINER_SUFFIX}"
BETA_REDIS_CONTAINER="ai-companion-redis${BETA_CONTAINER_SUFFIX}"
BETA_WEB_CONTAINER="cerid-web${BETA_CONTAINER_SUFFIX}"

BETA_MCP_URL="http://localhost:${BETA_MCP_PORT}"
BETA_GUI_URL="http://localhost:${BETA_GUI_PORT}"
BETA_CHROMA_URL="http://localhost:${BETA_CHROMA_PORT}"
BETA_NEO4J_URL="http://localhost:${BETA_NEO4J_PORT}"
BETA_MCP_BASE="${BETA_MCP_BASE:-http://ai-companion-mcp:8888}"

export BETA_TARGET BETA_CONTAINER_SUFFIX BETA_DOCKER_NETWORK BETA_START_HINT
export BETA_MCP_CONTAINER BETA_NEO4J_CONTAINER BETA_CHROMA_CONTAINER BETA_REDIS_CONTAINER BETA_WEB_CONTAINER
export BETA_MCP_PORT BETA_GUI_PORT BETA_NEO4J_PORT BETA_CHROMA_PORT BETA_REDIS_PORT
export BETA_MCP_URL BETA_GUI_URL BETA_CHROMA_URL BETA_NEO4J_URL BETA_MCP_BASE
