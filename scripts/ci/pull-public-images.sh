#!/usr/bin/env bash
# Pull the CI stack's public images without the Docker credential helper.
#
# On the self-hosted Macs the Docker CLI's default credential store is
# docker-credential-osxkeychain, and the runner's launchd session cannot open
# the keychain ("keychain cannot be accessed because the current session does
# not allow user interaction"). Every image the stack needed used to be on the
# runner already, so `compose up` never pulled and never asked. The first new
# public image (rspamd, 2026-10-05) made it pull, and the boot failed before a
# container started. This pulls them first with the same keychain-free config
# scripts/ci/docker-gate.sh uses: an explicit empty Hub entry keeps lookups on
# the file store, so pulls stay anonymous; the plugin dirs keep compose
# reachable. The current context's endpoint is carried over as DOCKER_HOST,
# because the empty config no longer names it.
set -euo pipefail

if [ "$(uname -s)" = "Darwin" ]; then
  host="$(docker context inspect --format '{{(index .Endpoints "docker").Host}}' 2>/dev/null || true)"
  if [ -n "$host" ] && [ -z "${DOCKER_HOST:-}" ]; then
    export DOCKER_HOST="$host"
  fi
  mkdir -p .ci-artifacts/docker-config
  printf '{"auths": {"https://index.docker.io/v1/": {}}, "cliPluginsExtraDirs": ["%s", "/Applications/Docker.app/Contents/Resources/cli-plugins"]}\n' \
    "$HOME/.docker/cli-plugins" > .ci-artifacts/docker-config/config.json
  export DOCKER_CONFIG="$PWD/.ci-artifacts/docker-config"
fi

# --ignore-buildable: the images compose would build (mcp-server, cerid-web)
# are tagged from the cache or built by `up`; only the public ones are pulled.
docker compose -f docker-compose.yml -f docker-compose.ci.yml pull --ignore-buildable --quiet
