# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Build stage
FROM node:25-alpine3.21 AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# In Docker, use nginx proxy paths (env-config.js can override at runtime)
ENV VITE_MCP_URL=/api/mcp
ENV VITE_BIFROST_URL=/api/bifrost
ARG VITE_CERID_API_KEY=""
ENV VITE_CERID_API_KEY=${VITE_CERID_API_KEY}
RUN npm run build

# Production stage — Trivy scans in CI catch base-image CVEs
FROM nginx:1.29-alpine
RUN apk upgrade --no-cache
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh
# Inject env-config.js before other scripts so window.__ENV__ is available at runtime
RUN sed -i 's|<head>|<head><script src="/env-config.js"></script>|' /usr/share/nginx/html/index.html
# Ensure nginx can write to required dirs when running as non-root
RUN chown -R nginx:nginx /var/cache/nginx /var/log/nginx /etc/nginx/conf.d \
    /usr/share/nginx/html \
    && touch /var/run/nginx.pid && chown nginx:nginx /var/run/nginx.pid
EXPOSE 80
USER nginx
ENTRYPOINT ["/docker-entrypoint.sh"]