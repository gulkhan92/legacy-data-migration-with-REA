# Isolated static analysis runner for uploaded software.
#
# The pinned REA command line tool (see package.json and package-lock.json) is installed without
# running package install scripts, so the runtime capture dependencies are never set up. The container
# is started by the platform with no network, a read only root filesystem and a read only mount of the
# file to analyse (see docker-compose.yml). It never executes the analysed file.
FROM node:24-bookworm-slim

# REA checks process ownership with ps on Linux, which the slim base image lacks.
RUN apt-get update && apt-get install -y --no-install-recommends procps && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 10001 rea && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin rea

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts --omit=dev --no-audit --no-fund && npm cache clean --force
COPY wrapper.mjs ./

ENV HOME=/tmp REA_TIMEOUT_SECONDS=120 REA_ALLOWED_ROOTS=/work NODE_ENV=production
USER 10001:10001
WORKDIR /work
ENTRYPOINT ["node", "/app/wrapper.mjs"]
