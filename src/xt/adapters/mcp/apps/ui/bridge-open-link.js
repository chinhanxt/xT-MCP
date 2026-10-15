// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// Chỉ thẻ phê duyệt dùng: yêu cầu host mở liên kết (ui/open-link). Không có đường gọi tool.
(function () {
  window.xtBridge.openLink = function (url) {
    return window.xtBridge.request("ui/open-link", { url: url });
  };
})();
