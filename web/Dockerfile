# The pages on their own (#672): Nuxt's server on bun, port 3000. Built from this folder:
#
#   docker build -t nautobot-maps-web .
#
# A single-page app (ssr: false): every page route answers the same shell, rendered at
# build time without inline scripts, with the same security headers the API sets
# (server/). At run time:
#   MAP_TILE_URL  the tile server, as the API's: the CSP lets the map load its tiles
#   API_URL       optional (e.g. http://api:5000): the API's paths are forwarded there,
#                 for the image on its own; the reverse proxy sends them to the API itself
#   PORT          3000
FROM oven/bun:1.4.4-slim@sha256:1846adf22496bd711036aae8be147e57bdebbaea2e5264e3e0b5af1db187d9a4 AS build
WORKDIR /web
COPY package.json bun.lock ./
# The lock file pins every package with its hash; no install scripts run.
RUN bun install --frozen-lockfile --ignore-scripts
COPY . .
# --bun: the build's tools run on bun too; the image has no Node.
ENV NITRO_PRESET=bun
RUN bun --bun run build

FROM oven/bun:1.4.4-slim@sha256:1846adf22496bd711036aae8be147e57bdebbaea2e5264e3e0b5af1db187d9a4
# Debian security fixes released after the base image was built (#246, as the API's
# image); the Trivy scan in CI fails on fixed HIGH/CRITICAL ones.
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ENV NODE_ENV=production \
    PORT=3000
# The server and the files it serves; nothing else (no sources, no node_modules).
COPY --from=build /web/.output ./.output
# The base image's unprivileged user; the files stay root-owned (read-only for it).
USER bun
EXPOSE 3000
# Liveness probe: the page shell answers.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["bun", "-e", "fetch('http://127.0.0.1:' + (process.env.PORT || 3000) + '/').then((r) => process.exit(r.ok ? 0 : 1), () => process.exit(1))"]
CMD ["bun", ".output/server/index.mjs"]
