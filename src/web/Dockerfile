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

# Production stage
FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80