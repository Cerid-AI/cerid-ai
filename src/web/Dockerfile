# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Build stage
FROM node:22-alpine3.21 AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# In Docker, MCP is accessed directly (CORS is open), Bifrost via nginx proxy
ENV VITE_MCP_URL=http://localhost:8888
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
EXPOSE 80
ENTRYPOINT ["/docker-entrypoint.sh"]