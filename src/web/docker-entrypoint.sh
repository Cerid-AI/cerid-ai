#!/bin/sh
# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Generate runtime environment config for the SPA.
# This allows VITE_MCP_URL and VITE_BIFROST_URL to be overridden at
# container startup without rebuilding the Docker image.

ENV_JS="/usr/share/nginx/html/env-config.js"

cat > "$ENV_JS" <<EOF
window.__ENV__ = {
  VITE_MCP_URL: "${VITE_MCP_URL:-/api/mcp}",
  VITE_BIFROST_URL: "${VITE_BIFROST_URL:-/api/bifrost}",
  VITE_CERID_API_KEY: "${VITE_CERID_API_KEY:-}"
};
EOF

echo "[entrypoint] Generated $ENV_JS"

# Start nginx
exec nginx -g 'daemon off;'
