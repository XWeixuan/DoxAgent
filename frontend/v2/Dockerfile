FROM node:24-alpine AS build
WORKDIR /app/frontend/v2
RUN corepack enable
COPY frontend/v2/package.json frontend/v2/pnpm-lock.yaml frontend/v2/pnpm-workspace.yaml frontend/v2/.npmrc ./
RUN corepack pnpm install --frozen-lockfile
COPY dev_plan/workflow_v2/api_contract /app/dev_plan/workflow_v2/api_contract
COPY frontend/v2/ ./
RUN corepack pnpm build

FROM nginx:1.28-alpine
ARG DOXAGENT_BUILD_COMMIT=unversioned
LABEL org.opencontainers.image.revision=${DOXAGENT_BUILD_COMMIT}
COPY frontend/v2/deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/frontend/v2/dist /usr/share/nginx/html
RUN find /usr/share/nginx/html -type d -exec chmod 755 {} + \
 && find /usr/share/nginx/html -type f -exec chmod 644 {} +
COPY --chmod=0755 frontend/v2/deploy/healthcheck.sh /usr/local/bin/doxagent-web-healthcheck
EXPOSE 80
HEALTHCHECK --interval=15s --timeout=5s --retries=3 CMD /usr/local/bin/doxagent-web-healthcheck
