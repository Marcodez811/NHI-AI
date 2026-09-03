FROM node:22-alpine AS deps

WORKDIR /app

ENV CI=true

RUN corepack enable

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./

RUN pnpm install --frozen-lockfile


FROM node:22-alpine AS builder

WORKDIR /app

ENV CI=true

# Rewrites are resolved while `next build` runs, so the container hostname
# must be available at build time as well as at runtime.
ARG BACKEND_INTERNAL_URL=http://localhost:8000
ENV BACKEND_INTERNAL_URL=${BACKEND_INTERNAL_URL}

COPY --from=deps /app/node_modules ./node_modules

COPY . .

RUN ./node_modules/.bin/next build


FROM node:22-alpine AS runner

WORKDIR /app

ENV NODE_ENV=production

COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static

EXPOSE 3000

CMD ["node", "server.js"]