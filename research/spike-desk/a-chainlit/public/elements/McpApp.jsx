// SPDX-License-Identifier: Apache-2.0
// Host MCP Apps tối thiểu: iframe srcdoc + JSON-RPC postMessage (ui/initialize, tool-result, tools/call)
import { useEffect, useRef, useState } from "react";
export default function McpApp() {
  const ref = useRef(null); const [h, setH] = useState(360);
  useEffect(() => {
    const on = async (ev) => {
      if (ev.source !== ref.current?.contentWindow) return;
      const m = ev.data; if (!m || m.jsonrpc !== "2.0") return;
      const reply = (r) => ref.current.contentWindow.postMessage({ jsonrpc: "2.0", id: m.id, ...r }, "*");
      const send = (method, params) => ref.current.contentWindow.postMessage({ jsonrpc: "2.0", method, params }, "*");
      if (m.method === "ui/initialize") {
        reply({ result: { protocolVersion: "2026-01-26", hostInfo: { name: "xt-desk", version: "0.1" }, hostCapabilities: { serverTools: {} }, hostContext: {} } });
      } else if (m.method === "ui/notifications/initialized") {
        send("ui/notifications/tool-input", { arguments: props.toolInput || {} });
        send("ui/notifications/tool-result", props.toolResult);
      } else if (m.method === "ui/notifications/size-changed") { setH(Math.min(m.params.height + 24, 600));
      } else if (m.method === "tools/call") {
        try { const r = await callAction({ name: "mcp_call", payload: { name: m.params.name, arguments: m.params.arguments } });
          reply({ result: r.response });
        } catch (e) { reply({ error: { code: -32000, message: String(e) } }); }
      } else if (m.id !== undefined) reply({ result: {} });
    };
    window.addEventListener("message", on); return () => window.removeEventListener("message", on);
  }, []);
  return <iframe ref={ref} sandbox="allow-scripts" srcDoc={props.html} style={{ width: "100%", height: h, border: "1px solid #ccc", borderRadius: 8 }} />;
}
