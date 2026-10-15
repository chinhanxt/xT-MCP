// SPDX-License-Identifier: Apache-2.0
// Cầu nối MCP Apps tối thiểu (tự viết, không phụ thuộc): JSON-RPC 2.0 qua window.parent.postMessage.
(function () {
  let nextId = 1; const pending = new Map(); const handlers = {};
  window.addEventListener("message", (ev) => {
    if (ev.source !== window.parent) return;
    const m = ev.data; if (!m || m.jsonrpc !== "2.0") return;
    if (m.method === undefined && pending.has(m.id)) {
      const p = pending.get(m.id); pending.delete(m.id);
      m.error ? p.rej(new Error(m.error.message)) : p.res(m.result); return;
    }
    if (m.method && handlers[m.method]) handlers[m.method](m.params);
    if (m.method && m.id !== undefined) { // host request (vd ui/resource-teardown, ping): trả {}
      window.parent.postMessage({ jsonrpc: "2.0", id: m.id, result: {} }, "*");
    }
  });
  function request(method, params) {
    const id = nextId++;
    return new Promise((res, rej) => {
      pending.set(id, { res, rej });
      window.parent.postMessage({ jsonrpc: "2.0", id, method, params }, "*");
    });
  }
  function notify(method, params) { window.parent.postMessage({ jsonrpc: "2.0", method, params }, "*"); }
  window.xtBridge = {
    on(method, fn) { handlers[method] = fn; },
    async connect(name) {
      const r = await request("ui/initialize", { protocolVersion: "2026-01-26",
        appInfo: { name, version: "0.1.0" }, appCapabilities: {} });
      notify("ui/notifications/initialized", {});
      return r;
    },
    callTool(name, args) { return request("tools/call", { name, arguments: args || {} }); },
    sendSize() { notify("ui/notifications/size-changed", { width: document.documentElement.scrollWidth, height: document.documentElement.scrollHeight }); },
  };
})();
