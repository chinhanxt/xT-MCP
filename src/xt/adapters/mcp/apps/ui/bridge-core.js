// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// Cầu nối MCP Apps tối thiểu (tự viết, không thư viện): JSON-RPC 2.0 qua window.parent.postMessage.
(function () {
  var nextId = 1, pending = {}, handlers = {};
  window.addEventListener("message", function (ev) {
    if (ev.source !== window.parent) return;
    var m = ev.data;
    if (!m || m.jsonrpc !== "2.0") return;
    if (m.method === undefined && Object.prototype.hasOwnProperty.call(pending, m.id)) {
      var p = pending[m.id];
      delete pending[m.id];
      if (m.error) p.rej(new Error(m.error.message || "lỗi"));
      else p.res(m.result);
      return;
    }
    if (m.method && Object.prototype.hasOwnProperty.call(handlers, m.method)) handlers[m.method](m.params || {});
    if (m.method && m.id !== undefined) window.parent.postMessage({ jsonrpc: "2.0", id: m.id, result: {} }, "*");
  });
  function request(method, params) {
    var id = nextId++;
    return new Promise(function (res, rej) {
      pending[id] = { res: res, rej: rej };
      window.parent.postMessage({ jsonrpc: "2.0", id: id, method: method, params: params }, "*");
    });
  }
  function notify(method, params) {
    window.parent.postMessage({ jsonrpc: "2.0", method: method, params: params }, "*");
  }
  window.xtBridge = {
    on: function (method, fn) { handlers[method] = fn; },
    connect: function (name) {
      return request("ui/initialize", { protocolVersion: "2026-01-26", appInfo: { name: name, version: "0.1.0" }, appCapabilities: {} })
        .then(function (r) { notify("ui/notifications/initialized", {}); return r; });
    },
    sendSize: function () {
      notify("ui/notifications/size-changed", { width: document.documentElement.scrollWidth, height: document.documentElement.scrollHeight });
    },
    /* Kết quả tool -> envelope xT: structuredContent, hoặc JSON trong content[0].text (lỗi không có structuredContent). */
    payload: function (r) {
      var sc = r && r.structuredContent;
      if (!sc && r && r.content && r.content[0] && typeof r.content[0].text === "string") {
        try { sc = JSON.parse(r.content[0].text); } catch (e) { sc = null; }
      }
      return sc || null;
    },
    request: request
  };
})();
