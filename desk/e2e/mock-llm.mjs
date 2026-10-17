// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// LLM giả xác định, OpenAI-compatible (stream SSE). Ghi lại request ở GET /__requests.
import { createServer } from "node:http";

const port = Number(process.env.E2E_LLM_PORT);
if (!port) throw new Error("E2E_LLM_PORT chưa đặt");
const requests = [];
const hits = []; // request tới "collector" (kẻ tấn công) và /pii/reveal giả của xT

// Collector thứ hai (origin khác xT) + /pii/reveal giả trên cổng chính; ghi mọi request, CORS mở
function collector(req, res) {
  hits.push({ method: req.method, url: req.url });
  res.setHeader("access-control-allow-origin", "*");
  if (req.url.startsWith("/pii/reveal")) {
    res.writeHead(200, { "content-type": "application/json" });
    return res.end(JSON.stringify({ ok: true }));
  }
  // trang "tấn công" sau điều hướng: cố gọi tool qua bridge
  res.writeHead(200, { "content-type": "text/html" });
  res.end("<script>parent.postMessage({jsonrpc:'2.0',id:1,method:'tools/call',params:{name:'ping_tool',arguments:{n:7}}},'*')</script>");
}
const otherPort = Number(process.env.E2E_COLLECT_PORT || 0);
if (otherPort) createServer(collector).listen(otherPort, "127.0.0.1");

const REPLY = [
  "Đây là **sơ đồ tổ chức** của công ty:\n\n",
  "- **Lan**: CEO\n- **Minh**: CTO\n\n",
  "Lương CEO: [PII:ab12cd]\n\n",
  '<img src=x onerror="window.__xss=1"><script>window.__xss=1</script>[nhấn](javascript:window.__xss=1)',
].join("");

function sse(res, chunks) {
  res.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-cache" });
  for (const c of chunks) res.write(`data: ${JSON.stringify({ choices: [{ index: 0, delta: c }] })}\n\n`);
  res.write("data: [DONE]\n\n");
  res.end();
}

createServer((req, res) => {
  if (req.method === "GET" && req.url === "/__hits") {
    res.writeHead(200, { "content-type": "application/json", "access-control-allow-origin": "*" });
    return res.end(JSON.stringify(hits));
  }
  if (req.url.startsWith("/pii/reveal")) return collector(req, res);
  if (req.method === "GET" && req.url === "/__requests") {
    res.writeHead(200, { "content-type": "application/json" });
    return res.end(JSON.stringify(requests));
  }
  if (req.method === "POST" && req.url === "/v1/chat/completions") {
    let b = "";
    req.on("data", (d) => (b += d));
    req.on("end", () => {
      const body = JSON.parse(b);
      requests.push({ ...body, __auth: req.headers.authorization || null });
      const last = body.messages.findLast((message) => message.role !== "system");
      if (last.role === "tool") {
        const parts = REPLY.match(/[^ ]+ ?/g);
        return sse(res, parts.map((content) => ({ content })));
      }
      const text = String(last.content || "").toLowerCase();
      const names = new Set((body.tools || []).map((t) => t.function.name));
      let name = null;
      let args = {};
      const asked = [...names].find((n) => text.includes(`chạy ${n}`));
      if (asked) {
        name = asked;
        // "chạy <tool> {json}": tham số do test chỉ định (lấy từ nội dung gốc, không hạ chữ thường)
        const m = String(last.content || "").match(new RegExp(`chạy ${asked}\\s*(\\{.*\\})`, "is"));
        if (m) args = JSON.parse(m[1]);
      }
      else if (text.includes("sơ đồ") && names.has("show_org_chart")) name = "show_org_chart";
      else if (text.includes("phê duyệt") && names.has("show_approval_card")) (name = "show_approval_card"), (args = { task_id: "T-1" });
      if (!name) return sse(res, [{ content: "Xin chào, tôi có thể giúp gì?" }]);
      sse(res, [{ tool_calls: [{ index: 0, id: "call_mock_1", type: "function", function: { name, arguments: JSON.stringify(args) } }] }]);
    });
    return;
  }
  res.writeHead(404).end();
}).listen(port, "127.0.0.1");
