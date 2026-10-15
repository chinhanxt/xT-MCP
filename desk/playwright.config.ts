// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { defineConfig } from "@playwright/test";

const MCP = 19500;
const LLM = 19501; // LLM giả; cũng là "origin xT" giả (/pii/reveal) của project security
const DESK = 19502;
const EVIL = 19503;
const DESK_SEC = 19504;
const COLLECT = 19505; // origin khác xT (kẻ tấn công)
const OIDC = 19506; // Keycloak giả
const local = process.env.DESK_E2E_LOCAL_LLM === "1";

function need(n: string): string {
  const v = process.env[n];
  if (!v) throw new Error(`e2e:local-llm cần biến ${n} (xem desk/.env.example)`);
  return v;
}

const common = {
  DESK_MAX_STEPS: "4",
  DESK_HOST: "127.0.0.1",
  DESK_OIDC_ISSUER: `http://127.0.0.1:${OIDC}/realms/xt`, // Keycloak giả (e2e/mock-oidc.mjs)
  DESK_OIDC_CLIENT_ID: "xt-desk-agent",
  DESK_OIDC_SCOPE: "openid mcp-xt",
  DESK_APP_CALLS_PER_SEC: "5",
  DESK_APP_CALL_BURST: "10",
  DESK_APP_MAX_INFLIGHT: "4",
  DESK_APP_MAX_ARGS_BYTES: "32768",
  DESK_APP_GLOBAL_CALLS_PER_SEC: "10",
  DESK_APP_GLOBAL_BURST: "20",
};

const deskEnv: Record<string, string> = {
  ...common,
  DESK_MCP_URL: `http://127.0.0.1:${MCP}/mcp`,
  DESK_LLM_URL: local ? need("DESK_LLM_URL") : `http://127.0.0.1:${LLM}/v1`,
  DESK_LLM_MODEL: local ? need("DESK_LLM_MODEL") : "mock-model",
  DESK_LLM_REASONING_EFFORT: local ? (process.env.DESK_LLM_REASONING_EFFORT ?? "") : "none",
  DESK_TOOL_PROFILE: "show_org_chart,show_approval_card", // server spike không có core-10
  DESK_SANDBOX_URL: `http://127.0.0.1:${DESK}/sandbox.html`, // origin khác "localhost"
  DESK_XT_ORIGIN: "https://xt.freepro.online",
  DESK_PORT: String(DESK),
};

const secEnv: Record<string, string> = {
  ...common,
  DESK_MCP_URL: `http://127.0.0.1:${EVIL}/mcp`,
  DESK_LLM_URL: `http://127.0.0.1:${LLM}/v1`,
  DESK_LLM_MODEL: "mock-model",
  DESK_LLM_REASONING_EFFORT: "none",
  DESK_TOOL_PROFILE: "app_csp_comment,app_csp_header,app_csp_prehead,app_nav,app_bridge,app_open_link,app_connect",
  DESK_SANDBOX_URL: `http://127.0.0.1:${DESK_SEC}/sandbox.html`,
  DESK_XT_ORIGIN: `http://127.0.0.1:${LLM}`,
  DESK_PORT: String(DESK_SEC),
};

export default defineConfig({
  testDir: "e2e",
  timeout: local ? 240_000 : 60_000,
  workers: 1,
  reporter: [["list"]],
  use: { headless: true, viewport: { width: 1000, height: 900 } },
  projects: local
    ? [{ name: "local-llm", testMatch: /local-llm\.spec\.ts/, use: { baseURL: `http://localhost:${DESK}` } }]
    : [
        { name: "desk", testMatch: /(desk|auth)\.spec\.ts/, use: { baseURL: `http://localhost:${DESK}` } },
        { name: "security", testMatch: /security\.spec\.ts/, use: { baseURL: `http://localhost:${DESK_SEC}` } },
      ],
  webServer: [
    { command: "node e2e/mock-oidc.mjs", env: { E2E_OIDC_PORT: String(OIDC) }, port: OIDC, reuseExistingServer: false },
    { command: "node e2e/start-mcp.mjs", env: { E2E_MCP_PORT: String(MCP) }, port: MCP, timeout: 180_000, reuseExistingServer: false },
    ...(local
      ? []
      : [
          {
            command: "node e2e/start-evil.mjs",
            env: { E2E_EVIL_PORT: String(EVIL), E2E_COLLECT_XT: `http://127.0.0.1:${LLM}`, E2E_COLLECT_OTHER: `http://127.0.0.1:${COLLECT}` },
            port: EVIL,
            timeout: 180_000,
            reuseExistingServer: false,
          },
          { command: "node e2e/mock-llm.mjs", env: { E2E_LLM_PORT: String(LLM), E2E_COLLECT_PORT: String(COLLECT) }, port: LLM, reuseExistingServer: false },
          { command: "npx vite build --outDir node_modules/.cache/dist-sec && npx vite preview --outDir node_modules/.cache/dist-sec", env: secEnv, port: DESK_SEC, timeout: 120_000, reuseExistingServer: false },
        ]),
    { command: "npx vite build && npx vite preview", env: deskEnv, port: DESK, timeout: 120_000, reuseExistingServer: false },
  ],
});
