// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// Proxy chỉ chuyển tiếp: KHÔNG gắn bearer tĩnh. Header Authorization của người dùng (từ trình duyệt) đi nguyên vẹn tới upstream.
import { defineConfig, loadEnv, type ProxyOptions } from "vite";
import react from "@vitejs/plugin-react";
import { readFileSync } from "node:fs";
import path from "node:path";

const REQUIRED = [
  "DESK_MCP_URL",
  "DESK_LLM_URL",
  "DESK_LLM_MODEL",
  "DESK_TOOL_PROFILE",
  "DESK_MAX_STEPS",
  "DESK_SANDBOX_URL",
  "DESK_HOST",
  "DESK_PORT",
  "DESK_XT_ORIGIN",
  "DESK_OIDC_ISSUER",
  "DESK_OIDC_CLIENT_ID",
  "DESK_OIDC_SCOPE",
  "DESK_APP_CALLS_PER_SEC",
  "DESK_APP_CALL_BURST",
  "DESK_APP_MAX_INFLIGHT",
  "DESK_APP_MAX_ARGS_BYTES",
  "DESK_APP_GLOBAL_CALLS_PER_SEC",
  "DESK_APP_GLOBAL_BURST",
] as const;

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  if (mode === "test") return { plugins: [react()], test: { environment: "node", include: ["test/**/*.test.ts", "tests/**/*.test.ts"] } };

  const missing = REQUIRED.filter((k) => !env[k]);
  if (missing.length) {
    throw new Error(`xT Desk: thiếu biến cấu hình bắt buộc: ${missing.join(", ")} (xem desk/.env.example)`);
  }
  const mcp = new URL(env.DESK_MCP_URL);
  const llm = new URL(env.DESK_LLM_URL);
  const sandbox = new URL(env.DESK_SANDBOX_URL);
  const posInt = (k: (typeof REQUIRED)[number]) => {
    const n = Number(env[k]);
    if (!Number.isInteger(n) || n < 1) throw new Error(`xT Desk: ${k} phải là số nguyên >= 1`);
    return n;
  };
  const maxSteps = posInt("DESK_MAX_STEPS");
  const coreSkill = readFileSync(path.resolve(process.cwd(), "../skills/xt-core/SKILL.md"), "utf8")
    .replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n/, "").replace(/<!--[\s\S]*?-->/g, "").trim();
  const xtOrigin = new URL(env.DESK_XT_ORIGIN).origin;
  new URL(env.DESK_OIDC_ISSUER); // phải là URL hợp lệ
  if (!env.DESK_OIDC_SCOPE.split(/\s+/).includes("openid")) throw new Error("xT Desk: DESK_OIDC_SCOPE phải có openid");

  const llmBase = llm.pathname.replace(/\/$/, "");
  const proxy: Record<string, ProxyOptions> = {
    "/studio-api": {
      target: mcp.origin,
      changeOrigin: true,
    },
    "/mcp": {
      target: mcp.origin,
      changeOrigin: true,
      rewrite: () => mcp.pathname,
    },
    "/llm": {
      target: llm.origin,
      changeOrigin: true,
      rewrite: (p) => llmBase + p.replace(/^\/llm/, ""),
    },
  };
  const server = { host: env.DESK_HOST, port: Number(env.DESK_PORT), strictPort: true, proxy };

  return {
    plugins: [react()],
    define: {
      __DESK_CONFIG__: JSON.stringify({
        model: env.DESK_LLM_MODEL,
        coreSkill,
        reasoningEffort: env.DESK_LLM_REASONING_EFFORT || "",
        toolProfile: env.DESK_TOOL_PROFILE,
        maxSteps,
        sandboxUrl: sandbox.href,
        xtOrigin,
        oidcIssuer: env.DESK_OIDC_ISSUER.replace(/\/$/, ""),
        oidcClientId: env.DESK_OIDC_CLIENT_ID,
        oidcScope: env.DESK_OIDC_SCOPE,
        oidcOrgParam: env.DESK_OIDC_ORG_PARAM || "",
        oidcOrgScope: env.DESK_OIDC_ORG_SCOPE || "",
        oidcOrganizations: [...new Set((env.DESK_OIDC_ORGANIZATIONS || "").split(",").map(alias => alias.trim()).filter(Boolean))],
        appCallsPerSec: posInt("DESK_APP_CALLS_PER_SEC"),
        appCallBurst: posInt("DESK_APP_CALL_BURST"),
        appMaxInflight: posInt("DESK_APP_MAX_INFLIGHT"),
        appMaxArgsBytes: posInt("DESK_APP_MAX_ARGS_BYTES"),
        appGlobalCallsPerSec: posInt("DESK_APP_GLOBAL_CALLS_PER_SEC"),
        appGlobalBurst: posInt("DESK_APP_GLOBAL_BURST"),
      }),
    },
    server,
    preview: server,
    test: { environment: "node", include: ["test/**/*.test.ts", "tests/**/*.test.ts"] },
  };
});
