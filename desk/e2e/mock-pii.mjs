// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// MCP giả (stateless) + /pii/reveal giả của xT trên cùng một cổng. Chỉ mock, không stack thật.
// GET /__state: số vé đã cấp/đã đổi, các request /pii/reveal. Dữ liệu thật chỉ có ở đây và trong response /pii/reveal.
import { createServer } from "node:http";
import { webcrypto } from "node:crypto";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { CallToolRequestSchema, ListToolsRequestSchema } from "@modelcontextprotocol/sdk/types.js";

const port = Number(process.env.E2E_PII_PORT);
const allowOrigin = process.env.E2E_PII_ALLOW_ORIGIN;
if (!port || !allowOrigin) throw new Error("E2E_PII_PORT / E2E_PII_ALLOW_ORIGIN chưa đặt");
const TOKEN = "⟨NV-0042.luong⟩";
const SECRET = "25000000";
const tickets = new Map();
const state = { minted: 0, redeemed: 0, rejected: 0, reveals: [] };

const tools = [
  { name: "get_employee_pii", description: "Lấy lương nhân viên (token PII)", inputSchema: { type: "object", properties: {} } },
  {
    name: "pii_reveal_ticket",
    description: "app-only",
    inputSchema: { type: "object", properties: { tokens: { type: "array", items: { type: "string" } }, pubkey_jwk: { type: "object" } }, required: ["tokens", "pubkey_jwk"] },
    _meta: { ui: { visibility: ["app"] } },
  },
];

function mcpServer() {
  const s = new Server({ name: "mock-pii", version: "0" }, { capabilities: { tools: {} } });
  s.setRequestHandler(ListToolsRequestSchema, async () => ({ tools }));
  s.setRequestHandler(CallToolRequestSchema, async ({ params }) => {
    if (params.name === "get_employee_pii") {
      return { content: [{ type: "text", text: `Lương ${TOKEN}` }], structuredContent: { ho_ten: "Nguyễn V*** A", luong: TOKEN } };
    }
    if (params.name === "pii_reveal_ticket") {
      const ticket = `tk-${++state.minted}-${Math.random().toString(36).slice(2)}`;
      tickets.set(ticket, { jwk: params.arguments.pubkey_jwk, tokens: params.arguments.tokens, used: false });
      return { content: [{ type: "text", text: "Đã cấp vé" }], _meta: { "xt/reveal": { ticket, url: `http://127.0.0.1:${port}/pii/reveal` } } };
    }
    return { isError: true, content: [{ type: "text", text: "unknown tool" }] };
  });
  return s;
}

const b64u = (s) => Uint8Array.from(Buffer.from(s, "base64url"));
async function redeem(b) {
  const t = tickets.get(b.ticket);
  if (!t || t.used) return null;
  t.used = true;
  const key = await webcrypto.subtle.importKey("jwk", t.jwk, { name: "ECDSA", namedCurve: "P-256" }, false, ["verify"]);
  const ok = await webcrypto.subtle.verify({ name: "ECDSA", hash: "SHA-256" }, key, b64u(b.sig), new TextEncoder().encode(`${b.ticket}.${b.ts}`));
  return ok ? { values: Object.fromEntries(t.tokens.map((x) => [x, SECRET])), denied: [] } : null;
}

createServer(async (req, res) => {
  if (req.url === "/__state") {
    res.writeHead(200, { "content-type": "application/json" });
    return res.end(JSON.stringify(state));
  }
  if (req.url === "/pii/reveal") {
    const cors = { "access-control-allow-origin": allowOrigin, "access-control-allow-methods": "POST", "access-control-allow-headers": "content-type", vary: "Origin", "cache-control": "no-store" };
    if (req.method === "OPTIONS") return res.writeHead(204, cors).end();
    let raw = "";
    for await (const c of req) raw += c;
    const b = JSON.parse(raw);
    state.reveals.push({ origin: req.headers.origin, credentials: !!req.headers.cookie || !!req.headers.authorization });
    const out = await redeem(b);
    if (!out) {
      state.rejected++;
      return res.writeHead(403, { ...cors, "content-type": "application/json" }).end(JSON.stringify({ error: "TICKET_INVALID" }));
    }
    state.redeemed++;
    return res.writeHead(200, { ...cors, "content-type": "application/json" }).end(JSON.stringify(out));
  }
  if (req.url === "/mcp" && req.method === "POST") {
    let raw = "";
    for await (const c of req) raw += c;
    const server = mcpServer();
    const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined });
    res.on("close", () => void transport.close());
    await server.connect(transport);
    return transport.handleRequest(req, res, JSON.parse(raw));
  }
  res.writeHead(405).end();
}).listen(port, "127.0.0.1");
