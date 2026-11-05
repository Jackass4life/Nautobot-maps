# The web/ service (#577). Build from web/: docker build -t web web/
FROM node:22-slim AS build
WORKDIR /src
COPY package.json package-lock.json ./
# Install scripts don't run; `nuxt prepare` makes the generated types.
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY . .
RUN npx nuxt prepare && npm run build

FROM node:22-slim
ENV NODE_ENV=production \
    HOST=0.0.0.0 \
    PORT=3000
WORKDIR /app
COPY --from=build /src/.output ./.output
USER node
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD node -e "fetch('http://127.0.0.1:' + (process.env.PORT || 3000) + '/healthz').then(r => process.exit(r.ok ? 0 : 1), () => process.exit(1))"
CMD ["node", ".output/server/index.mjs"]
