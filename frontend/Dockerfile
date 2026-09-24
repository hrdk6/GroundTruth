# Frontend image. Node 22 to match the toolchain the app is developed against.
#
# Two ways out: the `dev` stage runs `next dev` against a mounted source tree
# (docker-compose.yml), and the final stage -- what a plain `docker build`
# produces -- is the production server, with no dev compiler or dev overlay.

FROM node:22-alpine AS deps
WORKDIR /app
# Dependency layer first: it changes far less often than the source.
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM deps AS dev
COPY . .
EXPOSE 3000
CMD ["npm", "run", "dev"]

FROM deps AS build
# `next build` compiles the API address into the rewrite table
# (.next/routes-manifest.json), so it is a build argument: setting it on the
# running container changes nothing. The default is the Compose service name.
ARG NEXT_PUBLIC_API_URL=http://backend:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_TELEMETRY_DISABLED=1
COPY . .
RUN npx next build

FROM node:22-alpine AS runner
WORKDIR /app
# HOSTNAME: the standalone server binds to it, and the container's own
# hostname is not reachable from the published port. PORT is honoured, so a
# host such as Render can set it.
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
USER node
EXPOSE 3000
CMD ["node", "server.js"]
