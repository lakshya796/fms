# MAIR Voucher Portal UI.
#
# next.config.ts sets output: "standalone", so the build emits a server bundle
# with only the node_modules it actually uses. The runtime stage copies that
# and never installs dependencies again.
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
# npm ci when a lockfile is present (reproducible), npm install when it isn't.
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

FROM node:22-alpine AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# Baked in at build time: Next inlines NEXT_PUBLIC_* into the bundle, so this
# must be the API URL for the environment this image is built for. Rebuild the
# image to point it somewhere else - changing the task definition alone will
# not move it.
ARG NEXT_PUBLIC_FMS_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_FMS_API_URL=$NEXT_PUBLIC_FMS_API_URL
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:22-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000
RUN apk add --no-cache curl && addgroup -g 10001 -S app && adduser -S -u 10001 -G app app
COPY --from=build --chown=app:app /app/.next/standalone ./
COPY --from=build --chown=app:app /app/.next/static ./.next/static
COPY --from=build --chown=app:app /app/public ./public
USER app
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://127.0.0.1:3000/voucher-portal || exit 1
CMD ["node", "server.js"]
