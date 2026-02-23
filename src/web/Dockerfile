# Build stage
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# In Docker, MCP is accessed directly (CORS is open), Bifrost via nginx proxy
ENV VITE_MCP_URL=http://localhost:8888
ENV VITE_BIFROST_URL=/api/bifrost
RUN npm run build

# Production stage
FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
