# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
# Build context: gốc repo (lọc bởi desk.Dockerfile.dockerignore). Bản build hợp nhất (scripts/unified.mjs):
# Desk + Studio (/studio/) + WareTrack 3D (/waretrack/) -> tệp tĩnh phục vụ bằng nginx-unprivileged (cổng 8080, non-root).
FROM node:24-alpine@sha256:44fce696f38ba8c987c4db89954051000bbd05ffd8d8659ba15ed4a8602e937f AS build
WORKDIR /src
COPY desk/package.json desk/package-lock.json desk/
COPY imports/dify-rea/package.json imports/dify-rea/package-lock.json imports/dify-rea/
COPY imports/waretrack-3d/package.json imports/waretrack-3d/package-lock.json imports/waretrack-3d/
RUN for c in desk imports/dify-rea imports/waretrack-3d; do (cd "$c" && npm ci --no-audit --no-fund) || exit 1; done
COPY scripts/unified.mjs scripts/
COPY skills skills
COPY imports/dify-rea imports/dify-rea
COPY imports/waretrack-3d imports/waretrack-3d
COPY desk desk
ARG DESK_MCP_URL DESK_LLM_URL DESK_LLM_MODEL DESK_LLM_REASONING_EFFORT DESK_TOOL_PROFILE \
    DESK_MAX_STEPS DESK_SANDBOX_URL DESK_XT_ORIGIN DESK_AUTH_MODE DESK_APP_CALLS_PER_SEC \
    DESK_APP_CALL_BURST DESK_APP_MAX_INFLIGHT DESK_APP_MAX_ARGS_BYTES DESK_APP_GLOBAL_CALLS_PER_SEC \
    DESK_APP_GLOBAL_BURST DESK_OIDC_ISSUER DESK_OIDC_CLIENT_ID DESK_OIDC_SCOPE DESK_OIDC_ORGANIZATIONS DESK_OIDC_ORG_SCOPE
ENV DESK_HOST=0.0.0.0 DESK_PORT=5173 DESK_MCP_URL=$DESK_MCP_URL DESK_LLM_URL=$DESK_LLM_URL \
    DESK_LLM_MODEL=$DESK_LLM_MODEL DESK_LLM_REASONING_EFFORT=$DESK_LLM_REASONING_EFFORT \
    DESK_TOOL_PROFILE=$DESK_TOOL_PROFILE DESK_MAX_STEPS=$DESK_MAX_STEPS \
    DESK_SANDBOX_URL=$DESK_SANDBOX_URL DESK_XT_ORIGIN=$DESK_XT_ORIGIN DESK_AUTH_MODE=$DESK_AUTH_MODE \
    DESK_APP_CALLS_PER_SEC=$DESK_APP_CALLS_PER_SEC DESK_APP_CALL_BURST=$DESK_APP_CALL_BURST \
    DESK_APP_MAX_INFLIGHT=$DESK_APP_MAX_INFLIGHT DESK_APP_MAX_ARGS_BYTES=$DESK_APP_MAX_ARGS_BYTES \
    DESK_APP_GLOBAL_CALLS_PER_SEC=$DESK_APP_GLOBAL_CALLS_PER_SEC DESK_APP_GLOBAL_BURST=$DESK_APP_GLOBAL_BURST \
    DESK_OIDC_ISSUER=$DESK_OIDC_ISSUER DESK_OIDC_CLIENT_ID=$DESK_OIDC_CLIENT_ID DESK_OIDC_SCOPE="$DESK_OIDC_SCOPE" \
    DESK_OIDC_ORGANIZATIONS="$DESK_OIDC_ORGANIZATIONS" DESK_OIDC_ORG_SCOPE="$DESK_OIDC_ORG_SCOPE"
RUN node scripts/unified.mjs build && cd desk && node scripts/render-nginx.mjs

FROM nginxinc/nginx-unprivileged:1.28-alpine@sha256:7377697a821c131a924a7105fafbe7414db4e9fcc77a6f08f776f33f141ec3f8
COPY --from=build /src/desk/nginx.rendered.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/desk/dist /usr/share/nginx/html
EXPOSE 8080
