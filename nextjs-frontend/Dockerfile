FROM node:25-bookworm

WORKDIR /app

# pnpm at the version pinned in package.json#packageManager
RUN corepack enable

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

USER node

COPY . .

EXPOSE 3000

# Dev server plus the watcher that regenerates the API client when openapi.json changes
CMD ["./start.sh"]
