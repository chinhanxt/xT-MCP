// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// Chỉ view có tool app-only dùng (sơ đồ tổ chức, bảng bản ghi).
(function () {
  window.xtBridge.callTool = function (name, args) {
    return window.xtBridge.request("tools/call", { name: name, arguments: args || {} });
  };
})();
