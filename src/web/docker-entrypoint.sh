#!/bin/sh
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Generate runtime environment config for the SPA.
# This allows VITE_MCP_URL and VITE_BIFROST_URL to be overridden at
# container startup without rebuilding the Docker image.

CACHE_BUST=$(date +%s)
HTML="/usr/share/nginx/html/index.html"
ENV_JS="/usr/share/nginx/html/env-config.js"
VERSION_JS="/usr/share/nginx/html/version.json"

# Write env config
cat > "$ENV_JS" <<EOF
window.__ENV__ = {
  VITE_MCP_URL: "${VITE_MCP_URL:-/api/mcp}",
  VITE_BIFROST_URL: "${VITE_BIFROST_URL:-/api/bifrost}",
  VITE_CERID_API_KEY: "${VITE_CERID_API_KEY:-}"
};
EOF

# Write version manifest (used by stale-cache detection)
cat > "$VERSION_JS" <<EOF
{"build":"$CACHE_BUST"}
EOF

# Update env-config script tag with cache-busting query param
sed -i "s|env-config\.js[^\"]*|env-config.js?v=$CACHE_BUST|" "$HTML"

# Inject a stale-cache detector BEFORE the main bundle.
# If the browser serves a cached index.html with old JS hashes,
# this inline script detects the mismatch and forces a reload.
if ! grep -q "cerid-stale-check" "$HTML"; then
  DETECTOR="<script id=\"cerid-stale-check\">(function(){var b=\"$CACHE_BUST\";fetch(\"/version.json?_=\"+Date.now(),{cache:\"no-store\"}).then(function(r){return r.json()}).then(function(d){if(d.build!==b){console.warn(\"[cerid] Stale cache detected, reloading...\");location.reload()}}).catch(function(){});})()</script>"
  sed -i "s|</head>|$DETECTOR</head>|" "$HTML"
fi

echo "[entrypoint] Generated env-config.js + version.json (v=$CACHE_BUST)"

# Start nginx
exec nginx -g 'daemon off;'
