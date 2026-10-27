# The site, built and served by nginx. Build from the repository root (the build renders ../docs into the documentation viewer):
#   docker build -f frontend/Dockerfile -t legacylift-frontend .
FROM node:24-alpine AS build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
COPY docs /app/docs
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.29-alpine
# Operating system packages are brought up to the versions available at build time (the scan of this image fails on a fixable HIGH or CRITICAL finding).
USER root
RUN apk upgrade --no-cache
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/frontend/dist /usr/share/nginx/html
# The base image already runs as the unprivileged nginx user; saying so here keeps it true if the base image changes.
USER 101:101
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD wget -qO- http://127.0.0.1:8080/ >/dev/null || exit 1
