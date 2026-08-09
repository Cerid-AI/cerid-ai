#!/bin/sh
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

# Generate runtime environment config for the SPA.
# This allows VITE_MCP_URL and VITE_BIFROST_URL to be overridden at
# container startup without rebuilding the Docker image.
# NOTE: Pure shell — no python3 dependency (nginx:alpine doesn't have it).

CACHE_BUST=$(date +%s)
# Output paths overridable via env so tests can run the script outside the image.
HTML="${CERID_HTML_PATH:-/usr/share/nginx/html/index.html}"
ENV_JS="${CERID_ENV_JS_PATH:-/usr/share/nginx/html/env-config.js}"
VERSION_JS="${CERID_VERSION_JS_PATH:-/usr/share/nginx/html/version.json}"

# Write env config — values are JSON-escaped below before interpolation
MCP_URL="${VITE_MCP_URL:-/api/mcp}"
BIFROST_URL="${VITE_BIFROST_URL:-/api/bifrost}"
API_KEY="${VITE_CERID_API_KEY:-}"
SENTRY_DSN_WEB="${VITE_SENTRY_DSN_WEB:-}"
APP_VERSION="${VITE_APP_VERSION:-}"

# Each value lands inside a double-quoted JS string in env-config.js. Escape
# backslash + double-quote and strip CR/LF so an operator-supplied value can
# never terminate the string and inject script — it stays data, verbatim.
json_escape() {
  printf '%s' "$1" | tr -d '\r\n' | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'
}

# The API key is also written into an nginx conf include, where a quote or
# newline would inject directives. Those characters never appear in a real
# key — treat them as operator misconfiguration and refuse to start.
case "$API_KEY" in
  *'"'* | *'\'* | *[[:space:]]*)
    echo "[entrypoint] ERROR: VITE_CERID_API_KEY contains a double-quote, backslash, or whitespace — refusing to start. Fix the key and restart the container." >&2
    exit 1
    ;;
esac

# The API key is a REUSABLE credential and env-config.js is served to anyone
# who can reach this container. On a LAN bind that published the key next door
# to the API it protects, cancelling the /mcp auth gate. So:
#
#   * relative MCP_URL (the default, "/api/mcp") — the SPA is same-origin and
#     nginx adds the header itself; the browser never receives the key.
#   * absolute MCP_URL — the operator has pointed the SPA straight at the API,
#     bypassing this proxy, so the browser genuinely needs the key and keeping
#     it out would just break them. Unchanged, and now a deliberate choice.
KEY_INC="${CERID_KEY_INC_PATH:-/etc/nginx/conf.d/cerid-api-key.inc}"
case "$MCP_URL" in
  /*)
    BROWSER_API_KEY=""
    if [ -n "$API_KEY" ]; then
      printf 'proxy_set_header X-API-Key "%s";\n' "$API_KEY" > "$KEY_INC"
      echo "[entrypoint] X-API-Key injected at the proxy (not exposed to the browser)"
    else
      : > "$KEY_INC"
    fi
    ;;
  *)
    BROWSER_API_KEY="$API_KEY"
    : > "$KEY_INC"
    [ -n "$API_KEY" ] && echo "[entrypoint] WARNING: absolute VITE_MCP_URL — the API key is served to the browser in env-config.js"
    ;;
esac

cat > "$ENV_JS" <<EOF
window.__ENV__ = {VITE_MCP_URL: "$(json_escape "$MCP_URL")", VITE_BIFROST_URL: "$(json_escape "$BIFROST_URL")", VITE_CERID_API_KEY: "$(json_escape "$BROWSER_API_KEY")", VITE_SENTRY_DSN_WEB: "$(json_escape "$SENTRY_DSN_WEB")", VITE_APP_VERSION: "$(json_escape "$APP_VERSION")"};
EOF

# Write version manifest (used by stale-cache detection)
cat > "$VERSION_JS" <<EOF
{"build":"$CACHE_BUST"}
EOF

# Update env-config script tag with cache-busting query param
sed -i "s|env-config\.js[^\"]*|env-config.js?v=$CACHE_BUST|" "$HTML"

# Inject a stale-cache detector BEFORE the main bundle.
if ! grep -q "cerid-stale-check" "$HTML"; then
  DETECTOR="<script id=\"cerid-stale-check\">(function(){var b=\"$CACHE_BUST\",k=\"cerid-reload-\"+b;if(sessionStorage.getItem(k))return;fetch(\"/version.json?_=\"+Date.now(),{cache:\"no-store\"}).then(function(r){return r.json()}).then(function(d){if(d.build!==b){console.warn(\"[cerid] Stale cache detected, reloading...\");sessionStorage.setItem(k,\"1\");location.reload()}}).catch(function(){});})()</script>"
  sed -i "s|</head>|$DETECTOR</head>|" "$HTML"
fi

echo "[entrypoint] Generated env-config.js + version.json (v=$CACHE_BUST)"

# Start nginx
exec nginx -g 'daemon off;'
