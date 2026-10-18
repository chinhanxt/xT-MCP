# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
# Build context: imports/dify-rea. Studio API (integrated mode) chạy Node 24 (type stripping + node:sqlite),
# user node (uid 1000), không publish cổng: chỉ xt-api gọi qua mạng compose (XT_STUDIO_URL).
# Giao diện /studio/ do ảnh Desk phục vụ (bản build hợp nhất), ảnh này chỉ chạy server.
FROM node:24-alpine@sha256:44fce696f38ba8c987c4db89954051000bbd05ffd8d8659ba15ed4a8602e937f
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund && npm cache clean --force
COPY src ./src
RUN mkdir -p /data && chown node:node /data
USER node
ENV NODE_ENV=production STUDIO_XT_MODE=1 HOST=0.0.0.0 PORT=4327 STUDIO_DATA_DIR=/data/studio LOG_LEVEL=info
EXPOSE 4327
CMD ["node", "src/server.ts"]
