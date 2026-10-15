#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
# E2E Desk <-> Keycloak (client xt-desk-agent). Cần stack đang chạy: `make up-minimal && make provision-realm`.
#   deploy/tests/desk_oidc_e2e.sh
# Headless (Chromium của Playwright, không display). Không sửa /etc/hosts. Build Desk vào thư mục tạm.
set -eu
T="$(cd "$(dirname "$0")" && pwd)"; R="$T/../.."
. "$HOME/.nvm/nvm.sh" >/dev/null 2>&1 || true
set -a; . "$R/deploy/.env"; set +a
DIST="$(mktemp -d)"; OUT="$(mktemp)"
USERJSON="$(python3 "$T/desk_oidc_e2e.py" create-user)"
U="$(echo "$USERJSON" | python3 -c 'import sys,json;print(json.load(sys.stdin)["username"])')"
P="$(echo "$USERJSON" | python3 -c 'import sys,json;print(json.load(sys.stdin)["password"])')"
trap 'python3 "$T/desk_oidc_e2e.py" delete-user "$U" || true; rm -rf "$DIST" "$OUT"' EXIT
echo "== Keycloak: cấu hình client ==" ; python3 "$T/desk_oidc_e2e.py" check
echo "== Build Desk (DESK_OIDC_* trỏ Keycloak thật qua APISIX) =="
( cd "$R/desk" && env DESK_MCP_URL="https://$XT_DOMAIN_MCP/mcp" DESK_LLM_URL=http://127.0.0.1:11434/v1 DESK_LLM_MODEL=qwen3.5:4b \
    DESK_TOOL_PROFILE=core-10 DESK_MAX_STEPS=6 DESK_SANDBOX_URL="https://$XT_DOMAIN_API/sandbox.html" DESK_HOST=127.0.0.1 DESK_PORT=5173 \
    DESK_XT_ORIGIN="https://$XT_DOMAIN_DESK" DESK_OIDC_ISSUER="https://$XT_DOMAIN_AUTH/realms/$XT_REALM" DESK_OIDC_CLIENT_ID="${XT_DESK_AGENT_CLIENT_ID:-xt-desk-agent}" \
    DESK_OIDC_SCOPE="openid mcp-xt" DESK_APP_CALLS_PER_SEC=5 DESK_APP_CALL_BURST=10 DESK_APP_MAX_INFLIGHT=4 DESK_APP_MAX_ARGS_BYTES=32768 \
    DESK_APP_GLOBAL_CALLS_PER_SEC=10 DESK_APP_GLOBAL_BURST=20 npx vite build --outDir "$DIST" --emptyOutDir ) 2>&1 | tail -4
echo "== Headless browser flow =="
node "$T/desk_oidc_e2e.mjs" "$DIST" "$U" "$P"
