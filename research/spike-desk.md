# Spike: xT Desk — chọn nền tảng web chat để TÁI SỬ DỤNG

Ngày chạy: 2026-10-15, `/tmp/xt-spike-desk`, cổng 19100 (MCP server), 19101 (proto B), 19103 (proto A). Code giữ ở `research/spike-desk/`. Toàn bộ test là Playwright headless (không đụng display thật). LLM: `qwen3.5:4b` qua ollama `/v1` với `reasoning_effort:"none"` (xem `spike-local-llm.md`). Server MCP: copy `spike-mcp-apps/server.py` đổi cổng 19100, CORS `*`. Đã dừng tiến trình và xóa venv/node_modules.

## 1. Bảng ứng viên

Giấy phép: đọc LICENSE thật trên raw.githubusercontent.com và/hoặc GitHub API (API bị rate-limit 60 lần/giờ nên một số repo chỉ có LICENSE thô).

| Ứng viên | Giấy phép (đã xác minh) | MCP client? | Render MCP Apps (ui://)? | Ngôn ngữ | Nặng | Trạng thái |
|---|---|---|---|---|---|---|
| **`@mcp-ui/client`** (repo `MCP-UI-Org/mcp-ui`) | Apache-2.0 (API + LICENSE + npm) | Không tự có; dùng `@modelcontextprotocol/sdk` Client (MIT) | **Có**: `<AppRenderer>` bọc `AppBridge` của ext-apps, gọi lại tool qua `client` hoặc `onCallTool` | JS/React | npm 111 gói, `node_modules` 75MB; bundle prod 771KB (211KB gzip) | **Prototype B: chạy** |
| **Chainlit** 2.12.0 | Apache-2.0 (API + LICENSE; dòng "All rights reserved" đứng trên văn bản Apache) | Có (UI cắm MCP server, `[features.mcp]`), nhưng tôi dùng SDK `mcp` trong `app.py` cho dễ kiểm soát | **Không** có ui:// (grep 0 kết quả); phải tự viết CustomElement `McpApp.jsx` | Python (+ React front có sẵn) | `.venv` 139MB, 132 gói, RSS ~140MB | **Prototype A: chạy** |
| ext-apps `basic-host` / AppBridge | `LICENSE`: đang chuyển MIT -> Apache-2.0 (API trả NOASSERTION vì file tùy biến); npm ghi MIT. Cả hai OSI | Dùng `@modelcontextprotocol/sdk` | Có (host tham chiếu, đã dùng ở `spike-mcp-apps`); là ví dụ chứ không phải thư viện chat; `AppBridge` là phần `@mcp-ui/client` dùng bên trong | TS | 35MB clone | Dùng gián tiếp qua mcp-ui; lấy `sandbox.html/.ts` |
| assistant-ui | MIT (API + LICENSE) | Không (UI chat, nối AI SDK/LangGraph...) | Không thấy hỗ trợ ui:// trong tài liệu gói (chưa kiểm sâu) | TS/React | npm gói `@assistant-ui/react` 0.15.28, 10 deps | Không prototype: phải tự ghép MCP + AppRenderer, hơn B không đáng kể |
| Vercel `ai-chatbot` (nay `vercel/chatbot`) | Apache-2.0 theo LICENSE thô (API trả NOASSERTION do header "Copyright 2024 Vercel" đứng trước văn bản) | Không sẵn | Không | Next.js | Next + Postgres + auth.js + Vercel-isms | Bỏ: nặng, gắn Vercel AI Gateway |
| LibreChat | MIT (LICENSE thô; API redirect do repo đổi tên) | Có (mạnh, có OAuth) | Chưa kiểm chứng | Node + MongoDB | Rất nặng (MongoDB, Meili...), ổ `/` 8GB | Bỏ (như `spike-local-llm.md`) |
| Goose (`aaif-goose/goose`) | Apache-2.0 (API) | Có | Có ở Desktop (Experimental, không chạy được ở máy này) | Rust/Electron | 307MB | Không phải web app tự host |
| CopilotKit | MIT (API) | Có qua AG-UI | Không kiểm | TS | nặng | Không xét sâu |
| Open WebUI | Giấy phép riêng "Open WebUI License" (có điều khoản branding) | Có | - | - | - | **Loại** theo yêu cầu |

## 2. Kết quả thật

### Prototype B — React + `@mcp-ui/client` (đạt đủ mục tiêu)
Gõ "Cho tôi xem sơ đồ tổ chức" -> qwen3.5:4b gọi `show_org_chart` -> `AppRenderer` đọc `ui://xt/org-chart.html`, nạp vào sandbox iframe khác origin (`127.0.0.1` vs `localhost`) -> sơ đồ SVG hiện INLINE trong luồng chat; click nút Tuan -> `tools/call get_employee` (tool `visibility:["app"]`, model không thấy) đi qua `client` MCP của host tới server.

```
RENDER_SECONDS 11.2 frame ok: True     (gồm load model lần đầu)
DETAIL: get_employee -> Tuan | Dev Lead | tuan@xt.vn | báo cáo cho: Minh
server-calls.log: show_org_chart, rồi get_employee {"id": 4}
console: AppRenderer Fetching resource URI -> Reading resource HTML -> App initialized -> Sending tool result -> tools/call
```
Ảnh: `research/spike-desk/B-1-start.png`, `B-2-orgchart-inline.png`, `B-3-clicked.png`. Chạy ngay lần đầu, không phải vá `@mcp-ui/client`.

### Prototype A — Chainlit + CustomElement tự viết (đạt, nhưng phải tự làm host)
Cùng kịch bản: sơ đồ hiện inline, click Tuan -> `get_employee` tới server (qua `cl.action_callback` -> MCP). Render 3.4s (model đã nóng). Ảnh: `A-1-start.png`, `A-2-orgchart-inline.png`, `A-3-clicked.png`; log `server-calls.log`.
Phát hiện: (1) Chainlit **không** render ui://; `McpApp.jsx` (27 dòng) tự cài host JSON-RPC: `ui/initialize`, `tool-input`, `tool-result`, `size-changed`, `tools/call`. (2) Khi tin nhắn cuối của LLM tới, Chainlit mount lại phần tử -> iframe reload, mất trạng thái UI (panel chi tiết); test phải đợi LLM xong mới click. Muốn giữ trạng thái phải cache theo message id. (3) iframe `sandbox="allow-scripts"` (origin mờ, không có proxy 2 lớp và không áp CSP từ `_meta.ui.csp`) -> kém an toàn hơn AppBridge. (4) Có sẵn: đăng nhập OAuth **Keycloak** (`KeycloakOAuthProvider` trong `oauth_providers.py`), lịch sử chat, UI đẹp, dark mode, upload.

## 3. Đo đạc

| | B: React + mcp-ui | A: Chainlit |
|---|---|---|
| Glue code ta sở hữu | `main.jsx` 74 dòng + `vite.config.js` 9 + `index.html` 2 = **85 dòng** (+ `sandbox.ts` 137 dòng/`sandbox.html` 39 dòng **copy nguyên** từ ext-apps) | `app.py` 52 + `McpApp.jsx` 27 = **79 dòng** |
| Gồm | MCP client, vòng lặp LLM tool-call, lọc tool app-only, render AppRenderer, UI chat tối giản (chưa có style/lịch sử/markdown) | tương tự, nhưng UI chat/markdown/lịch sử có sẵn; host MCP Apps tự viết |
| Phụ thuộc | 111 gói npm, 75MB | 132 gói Python, 139MB |
| RAM | dev-server vite 470MB (chỉ dev); bản build là tĩnh (~0 RAM) + server MCP | Chainlit ~140MB RSS |
| Model | ollama qwen3.5:4b ~1.5GB RSS llama-server (VRAM ~3GB) | như nhau |

Lưu ý công bằng: B chưa có markdown, lịch sử, auth, style; "85 dòng" sẽ phình lên khi thêm các thứ đó. A có sẵn nhưng phần rủi ro nhất (host MCP Apps) lại là phần ta tự viết và tự chịu bảo mật.

## 4. Khuyến nghị xT Desk

**Chọn B làm lõi: React (Vite) + `@mcp-ui/client` `AppRenderer` + `@modelcontextprotocol/sdk` (client, trình duyệt) + sandbox proxy `sandbox.html` của ext-apps.** Lý do: phần khó và nhạy cảm nhất (AppBridge, sandbox 2 lớp, CSP theo `_meta.ui.csp`, proxy `tools/call`, `ui/message`, `ui/open-link`) là thư viện OSI có người bảo trì và đúng chuẩn MCP Apps; ta chỉ viết vòng chat. Đạt đúng tinh thần "không xây từ đầu".

Việc kế tiếp:
1. Backend nhỏ (Python, Apache-2.0, có thể là Starlette trong chính repo xT) làm: proxy ollama + proxy `/mcp` (gắn token) + serve static + sandbox trên origin/cổng khác kèm header CSP từ `?csp=` như `basic-host/serve.ts`. Không để trình duyệt giữ token dài hạn.
2. Trang xác nhận phê duyệt: route React riêng, gọi tool `decide_task` bằng token của người dùng; hành động ghi luôn kiểm quyền ở server (không tin UI).
3. Keycloak: `keycloak-js` (Apache-2.0) hoặc OIDC code flow ở backend; token gắn vào proxy `/mcp` để UI `tools/call` mang token của người dùng. Đây là phần Chainlit có sẵn nhưng B phải tự làm.
4. Nếu cần chat đẹp nhanh (markdown, danh sách hội thoại, stream), thêm `assistant-ui` (MIT) làm khung và đặt `AppRenderer` vào renderer của tool-call; chưa thử trong spike này.
5. Phương án B-dự-phòng: Chainlit nếu cần UI hoàn chỉnh trong vài giờ (auth Keycloak sẵn); chấp nhận tự giữ `McpApp.jsx` và sửa lỗi remount iframe, và nâng cấp sandbox lên chuẩn proxy 2 lớp.

## 5. Chưa kiểm chứng / hạn chế
- assistant-ui, CopilotKit, Vercel chatbot, LibreChat không chạy thật (chỉ đọc giấy phép + metadata). Hỗ trợ ui:// của chúng là "chưa thấy", không phải "chắc chắn không".
- `sandbox.ts` của basic-host giới hạn referrer về `localhost/127.0.0.1`; khi lên domain thật phải sửa `ALLOWED_REFERRER_PATTERN`. Ở B dev-server không gắn header CSP (basic-host `serve.ts` có); chưa kiểm CSP thực thi.
- qwen3.5:4b gọi đúng `show_org_chart` 2/2 lần chạy (B và A), mẫu nhỏ; câu tóm tắt sau tool đôi lúc sai nhẹ chiều quan hệ (đã biết từ `spike-local-llm.md`).
- GitHub API bị rate-limit (60/giờ) giữa chừng; Vercel/LibreChat dựa vào LICENSE thô. `@mcp-ui/client` phụ thuộc ext-apps `^1.2.0` (không phải 2.0.3) — theo dõi lệch phiên bản protocol.
