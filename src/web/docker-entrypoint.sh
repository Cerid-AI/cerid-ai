#!/bin/sh
# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Generate runtime environment config for the SPA.
# This allows VITE_MCP_URL and VITE_BIFROST_URL to be overridden at
# container startup without rebuilding the Docker image.

# Use a timestamped filename to bust browser cache on every restart
CACHE_BUST=$(date +%s)
ENV_JS="/usr/share/nginx/html/env-config.js"

cat > "$ENV_JS" <<EOF
window.__ENV__ = {
  VITE_MCP_URL: "${VITE_MCP_URL:-/api/mcp}",
  VITE_BIFROST_URL: "${VITE_BIFROST_URL:-/api/bifrost}",
  VITE_CERID_API_KEY: "${VITE_CERID_API_KEY:-}"
};
EOF

# Update the script tag in index.html with cache-busting query param
sed -i "s|env-config\.js[^\"]*|env-config.js?v=$CACHE_BUST|" /usr/share/nginx/html/index.html

echo "[entrypoint] Generated $ENV_JS (v=$CACHE_BUST)"

# Start nginx
exec nginx -g 'daemon off;'
