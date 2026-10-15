// SPDX-License-Identifier: Apache-2.0
import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { AppRenderer } from "@mcp-ui/client";

const SANDBOX = new URL("http://127.0.0.1:" + location.port + "/sandbox.html"); // origin khác với host (localhost)
const SYSTEM = "Bạn là trợ lý xT Desk. Dùng tool khi cần. Trả lời ngắn gọn bằng tiếng Việt.";

async function connect() {
  const c = new Client({ name: "xt-desk", version: "0.1.0" });
  await c.connect(new StreamableHTTPClientTransport(new URL("/mcp", location.origin)));
  const { tools } = await c.listTools();
  return { c, tools };
}
const appOnly = (t) => (t._meta?.ui?.visibility || ["model", "app"]).indexOf("model") < 0;

function App() {
  const [mcp, setMcp] = useState(null);
  const [msgs, setMsgs] = useState([]); // {role, text} | {role:'tool', name, args, result}
  const [busy, setBusy] = useState(false);
  const [input, setInput] = useState("");
  const hist = useRef([{ role: "system", content: SYSTEM }]);
  useEffect(() => { connect().then(setMcp).catch((e) => setMsgs([{ role: "assistant", text: "MCP lỗi: " + e.message }])); }, []);

  async function send(text) {
    if (!mcp || !text.trim()) return;
    setBusy(true); setInput("");
    setMsgs((m) => [...m, { role: "user", text }]);
    hist.current.push({ role: "user", content: text });
    const tools = mcp.tools.filter((t) => !appOnly(t)).map((t) => ({ type: "function", function: { name: t.name, description: t.description, parameters: t.inputSchema } }));
    for (let i = 0; i < 4; i++) {
      const r = await fetch("/ollama/v1/chat/completions", { method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ model: "qwen3.5:4b", messages: hist.current, tools, reasoning_effort: "none" }) });
      const msg = (await r.json()).choices[0].message;
      hist.current.push({ role: "assistant", content: msg.content || "", tool_calls: msg.tool_calls });
      if (msg.content) setMsgs((m) => [...m, { role: "assistant", text: msg.content }]);
      if (!msg.tool_calls?.length) break;
      for (const tc of msg.tool_calls) {
        const args = JSON.parse(tc.function.arguments || "{}");
        const result = await mcp.c.callTool({ name: tc.function.name, arguments: args });
        const hasUi = !!mcp.tools.find((t) => t.name === tc.function.name)?._meta?.ui?.resourceUri;
        setMsgs((m) => [...m, { role: "tool", name: tc.function.name, args, result, hasUi }]);
        hist.current.push({ role: "tool", tool_call_id: tc.id, content: JSON.stringify(result.structuredContent ?? result.content) });
      }
    }
    setBusy(false);
  }
  return (
    <div style={{ font: "15px system-ui", maxWidth: 760, margin: "0 auto", padding: 12 }}>
      <h2>xT Desk (spike B: React + @mcp-ui/client)</h2>
      <div id="chat">
        {msgs.map((m, i) => m.role === "tool" ? (
          <div key={i} className="toolmsg" style={{ margin: "8px 0" }}>
            <small>🔧 {m.name}({JSON.stringify(m.args)})</small>
            {m.hasUi && <div style={{ border: "1px solid #ccc", borderRadius: 8, height: 380 }}>
              <AppRenderer client={mcp.c} toolName={m.name} sandbox={{ url: SANDBOX }} toolInput={m.args} toolResult={m.result}
                onError={(e) => console.error("AppRenderer", e)} /></div>}
          </div>
        ) : (
          <div key={i} className={"msg " + m.role} style={{ margin: "8px 0", textAlign: m.role === "user" ? "right" : "left" }}>
            <span style={{ background: m.role === "user" ? "#dbe4ff" : "#f1f3f5", padding: "6px 10px", borderRadius: 8, display: "inline-block" }}>{m.text}</span></div>
        ))}
        {busy && <i>đang nghĩ...</i>}
      </div>
      <form onSubmit={(e) => { e.preventDefault(); send(input); }} style={{ display: "flex", gap: 8, marginTop: 12 }}>
        <input id="box" value={input} onChange={(e) => setInput(e.target.value)} style={{ flex: 1, padding: 8 }} placeholder="Nhập yêu cầu..." disabled={!mcp} />
        <button id="send" disabled={busy || !mcp}>Gửi</button>
      </form>
    </div>
  );
}
createRoot(document.getElementById("root")).render(<App />);
