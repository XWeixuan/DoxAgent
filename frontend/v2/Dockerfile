FROM node:24-alpine AS build
WORKDIR /app/frontend/v2
RUN corepack enable
COPY frontend/v2/package.json frontend/v2/pnpm-lock.yaml frontend/v2/pnpm-workspace.yaml frontend/v2/.npmrc ./
RUN corepack pnpm install --frozen-lockfile
COPY dev_plan/workflow_v2/api_contract /app/dev_plan/workflow_v2/api_contract
COPY frontend/v2/ ./
RUN corepack pnpm build

FROM nginx:1.28-alpine
COPY frontend/v2/deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/frontend/v2/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=15s --timeout=3s --retries=3 CMD wget -q -O /dev/null http://127.0.0.1/healthz || exit 1
