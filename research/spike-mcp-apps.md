# Spike: MCP Apps (ui://) từ MCP server Python

Ngày chạy: 2026-10-15. Chạy thật trong `/tmp/xt-spike-apps`; code + bằng chứng ở `research/spike-mcp-apps/`. Cổng dùng: 18200 (MCP server), 18201 (host), 18202 (sandbox). Đã dừng hết tiến trình.

## 1. Spec và cơ chế (đọc `ext-apps/specification/draft/apps.mdx`, SEP-1865, và `mcp/server/apps.py`)

Repo `modelcontextprotocol/ext-apps` (bản 2.0.3, clone 2026-09-25). Phiên bản giao thức UI: `2026-01-26`. Extension id: `io.modelcontextprotocol/ui`.

| Hạng mục | Giá trị |
|---|---|
| Resource | URI bắt buộc `ui://...`; `mimeType` = `text/html;profile=mcp-app`; nội dung `text` hoặc `blob` (HTML5 đầy đủ) |
| Liên kết tool | `tool._meta.ui.resourceUri = "ui://..."`. Dạng phẳng cũ `_meta["ui/resourceUri"]` bị deprecated |
| Visibility | `_meta.ui.visibility`: `["model","app"]` (mặc định); `["app"]` = tool chỉ UI gọi được, host KHÔNG đưa vào danh sách tool của model |
| Host đọc UI | Host gọi `resources/read` theo `resourceUri` (có thể prefetch/cache); resource UI-only có thể không nằm trong `resources/list` |
| Meta của resource | `_meta.ui.csp {connectDomains, resourceDomains, frameDomains, baseUriDomains}`, `permissions {camera, microphone, geolocation, clipboardWrite}`, `domain`, `prefersBorder` |
| CSP mặc định (khi không khai báo) | `default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self' data:; object-src 'none'; connect-src 'none'`. Host không được nới ngoài domain đã khai báo |
| Sandbox | Iframe bắt buộc; kiến trúc khuyến nghị cho host web là 2 lớp iframe (proxy khác origin). Basic-host dùng `sandbox="allow-scripts allow-same-origin allow-forms"` (không có `allow-downloads`) |
| Giao thức UI->host | JSON-RPC 2.0 qua `window.parent.postMessage`. Vòng đời: View gửi `ui/initialize` (request: `protocolVersion`, `appInfo`, `appCapabilities`) -> nhận kết quả (`hostCapabilities`, `hostContext`) -> gửi `ui/notifications/initialized`. Sau đó host đẩy `ui/notifications/tool-input` rồi `ui/notifications/tool-result` (có `structuredContent`) |
| UI gọi tool | Request chuẩn `tools/call` {name, arguments}; host proxy sang MCP server (cần cờ `serverTools` của host, và tool phải có `"app"` trong visibility). Cũng có `resources/read`, `tools/list`, `sampling/createMessage`, `notifications/message`, `ping` |
| Thông điệp riêng `ui/*` | View->Host: `ui/open-link`, `ui/download-file`, `ui/message` (gửi tin nhắn vào chat), `ui/update-model-context`, `ui/request-display-mode`, notification `ui/notifications/size-changed`, `ui/notifications/request-teardown`. Host->View: `ui/resource-teardown`, `ui/notifications/host-context-changed`, `tool-input-partial`, `tool-cancelled` |

SDK Python `mcp` 2.3.0 (`mcp.server.apps`): `Apps()`, `@apps.tool(resource_uri=, visibility=[...])` (đóng dấu `_meta.ui`), `apps.add_html_resource(uri, html, csp=, permissions=, domain=, prefers_border=)` (tự đặt đúng mimeType, từ chối mime khác), `MCPServer(extensions=[apps])`, `client_supports_apps(ctx)` để fallback text. Tool bind vào URI chưa đăng ký sẽ báo lỗi ngay lúc khởi tạo.

Quan hệ với ChatGPT Apps SDK (nguồn: developers.openai.com/apps-sdk/mcp-apps-in-chatgpt, đã fetch; và `ext-apps/docs/migrate_from_openai_apps.md`): ChatGPT triển khai chuẩn MCP Apps; `window.openai` là lớp tương thích riêng của ChatGPT, cộng thêm các thứ ngoài spec (checkout, upload file, modal, `widgetState`). Tương ứng: `_meta["openai/outputTemplate"]` = `_meta.ui.resourceUri`; `window.openai.callTool` = `tools/call`; `sendFollowUpMessage` = `ui/message`; `toolOutput` = `ui/notifications/tool-result`; mime cũ `text/html+skybridge` -> `text/html;profile=mcp-app`. OpenAI khuyên UI mới dùng trường/bridge chuẩn, feature-detect thay vì phân nhánh theo host. Quyết định cho xT: viết theo chuẩn MCP Apps, không phụ thuộc `window.openai`.

Client hỗ trợ MCP Apps (đã kiểm chứng bằng web, nguồn thứ cấp):
- Claude (claude.ai, Desktop): có từ 2026-01-26 (alpic.ai/blog/mcp-apps-goes-official-claude-chatgpt-support; docs ext-apps `testing-mcp-apps.md` cũng liệt kê claude.ai). Bài alpic nói Claude yêu cầu "domain signing" gắn với URL server: chưa kiểm chứng chi tiết, không thử được ở đây.
- VS Code Insiders, Goose: có từ 2026-01-26 (alpic + docs ext-apps). VS Code thiếu fullscreen/PiP.
- ChatGPT: hỗ trợ (docs OpenAI). Bài alpic (tháng 1) nói lúc đó ChatGPT chưa hỗ trợ tool-call từ UI, còn docs OpenAI hiện tại mô tả `tools/call` từ UI là được: **mâu thuẫn, chưa kiểm chứng**, cần test trên ChatGPT thật (cần tài khoản + tunnel HTTPS).
- Chưa test trên host thương mại nào trong spike này (không có tài khoản/tunnel); kết quả e2e dưới đây là trên host tham chiếu `basic-host`.

## 2. Server Python

`server.py` (SDK `mcp` 2.3.0, Streamable HTTP, không auth), `bridge.js` (cầu nối postMessage tự viết ~35 dòng, được nhúng inline vào HTML), `orgchart.html` (SVG thuần), `approval.html`. Không dùng thư viện JS nào.

```python
# SPDX-License-Identifier: Apache-2.0
"""Spike MCP Apps: server Python (SDK `mcp` 2.x) + Streamable HTTP, không auth."""
import json, pathlib, sys, time
from typing import Any
from mcp.server.apps import Apps, client_supports_apps
from mcp.server.mcpserver import MCPServer, Context

D = pathlib.Path(__file__).parent
BRIDGE = (D / "bridge.js").read_text()
def page(name): return (D / name).read_text().replace("/*BRIDGE*/", BRIDGE)
LOG = D.parent / "server-calls.log"
def log(tool, args):
    with LOG.open("a") as f: f.write(json.dumps({"ts": time.time(), "tool": tool, "args": args}) + "\n")

EMP = [
 {"id": 1, "name": "Lan", "title": "CEO", "manager_id": None, "email": "lan@xt.vn"},
 {"id": 2, "name": "Minh", "title": "CTO", "manager_id": 1, "email": "minh@xt.vn"},
 {"id": 3, "name": "Hoa", "title": "CFO", "manager_id": 1, "email": "hoa@xt.vn"},
 {"id": 4, "name": "Tuan", "title": "Dev Lead", "manager_id": 2, "email": "tuan@xt.vn"},
 {"id": 5, "name": "Nga", "title": "Kế toán", "manager_id": 3, "email": "nga@xt.vn"},
]
TASKS = {"T-1": {"task_id": "T-1", "title": "Duyệt mua laptop", "requester": "Tuan", "description": "5 laptop dev", "amount": 150000000, "status": "pending"}}

apps = Apps()
ORG_URI, APPROVAL_URI = "ui://xt/org-chart.html", "ui://xt/approval-card.html"
apps.add_html_resource(ORG_URI, page("orgchart.html"), name="org_chart", description="Org chart SVG", prefers_border=True)
apps.add_html_resource(APPROVAL_URI, page("approval.html"), name="approval_card", description="Approval card", prefers_border=True)

@apps.tool(resource_uri=ORG_URI, description="Hiển thị sơ đồ tổ chức")
def show_org_chart(ctx: Context) -> dict[str, Any]:
    log("show_org_chart", {"client_supports_apps": client_supports_apps(ctx)})
    return {"employees": EMP}

@apps.tool(resource_uri=ORG_URI, visibility=["app"], description="Chi tiết nhân viên (UI gọi)")
def get_employee(id: int) -> dict[str, Any]:
    log("get_employee", {"id": id})
    e = next(e for e in EMP if e["id"] == id)
    m = next((x for x in EMP if x["id"] == e["manager_id"]), None)
    return {**e, "manager": m["name"] if m else None}

@apps.tool(resource_uri=APPROVAL_URI, description="Hiển thị thẻ phê duyệt")
def show_approval_card(task_id: str = "T-1") -> dict[str, Any]:
    log("show_approval_card", {"task_id": task_id}); return TASKS[task_id]

@apps.tool(resource_uri=APPROVAL_URI, visibility=["app"], description="Duyệt/từ chối task (UI gọi)")
def act_on_task(task_id: str, decision: str) -> dict[str, Any]:
    log("act_on_task", {"task_id": task_id, "decision": decision})
    TASKS[task_id]["status"] = "approved" if decision == "approve" else "rejected"
    return {"task_id": task_id, "status": TASKS[task_id]["status"]}

mcp = MCPServer("xt-apps-spike", extensions=[apps])
if __name__ == "__main__":
    import uvicorn
    from starlette.middleware.cors import CORSMiddleware
    app = mcp.streamable_http_app(streamable_http_path="/mcp")
    # Host chạy trên browser (basic-host) gọi thẳng từ origin khác -> bắt buộc CORS + expose Mcp-Session-Id
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:18201"], allow_methods=["*"],
                       allow_headers=["*"], expose_headers=["Mcp-Session-Id", "Mcp-Protocol-Version"])
    uvicorn.run(app, host="127.0.0.1", port=18200, log_level="warning")

```

`bridge.js`:
```javascript
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

```

`orgchart.html`:
```html
<!DOCTYPE html><html><head><meta charset="utf-8"><title>Org chart</title>
<style>body{font:14px system-ui;margin:8px}.node{cursor:pointer}.node rect{fill:#eef3ff;stroke:#3b5bdb;rx:6}.node:hover rect{fill:#d0ddff}
#detail{margin-top:8px;padding:8px;border:1px solid #ccc;border-radius:6px;min-height:40px}</style></head>
<body><h3 id="t">Sơ đồ tổ chức</h3><svg id="svg" width="600" height="260"></svg><div id="detail">Bấm vào một nút.</div>
<script>/*BRIDGE*/</script>
<script>
const B = window.xtBridge, NS = "http://www.w3.org/2000/svg";
function render(data) {
  const svg = document.getElementById("svg"); svg.innerHTML = "";
  const byParent = {}; data.employees.forEach(e => (byParent[e.manager_id ?? "root"] ||= []).push(e));
  const pos = {}; let leaf = 0;
  (function lay(id, depth) { const kids = byParent[id] || [];
    kids.forEach(k => { const ch = byParent[k.id] || [];
      lay(k.id, depth + 1);
      pos[k.id] = { y: 20 + depth * 90, x: ch.length ? (pos[ch[0].id].x + pos[ch[ch.length-1].id].x) / 2 : 20 + (leaf++) * 140 }; }); })("root", 0);
  data.employees.forEach(e => { if (e.manager_id && pos[e.manager_id]) { const a = pos[e.manager_id], b = pos[e.id];
    const l = document.createElementNS(NS, "line"); [["x1",a.x+60],["y1",a.y+40],["x2",b.x+60],["y2",b.y]].forEach(([k,v])=>l.setAttribute(k,v)); l.setAttribute("stroke","#888"); svg.appendChild(l); } });
  data.employees.forEach(e => { const p = pos[e.id]; const g = document.createElementNS(NS, "g"); g.setAttribute("class","node"); g.setAttribute("data-id", e.id);
    g.innerHTML = `<rect x="${p.x}" y="${p.y}" width="120" height="40"/><text x="${p.x+60}" y="${p.y+17}" text-anchor="middle" font-weight="600">${e.name}</text><text x="${p.x+60}" y="${p.y+32}" text-anchor="middle" font-size="11">${e.title}</text>`;
    g.onclick = async () => { document.getElementById("detail").textContent = "Đang tải...";
      try { const r = await B.callTool("get_employee", { id: e.id });
        const d = r.structuredContent || JSON.parse(r.content[0].text);
        document.getElementById("detail").textContent = `get_employee -> ${d.name} | ${d.title} | ${d.email} | báo cáo cho: ${d.manager ?? "-"}`;
      } catch (err) { document.getElementById("detail").textContent = "Lỗi: " + err.message; } };
    svg.appendChild(g); });
  B.sendSize();
}
B.on("ui/notifications/tool-result", (p) => { if (p.structuredContent) render(p.structuredContent); });
B.connect("xt-orgchart").then(() => { document.title = "ready"; });
</script></body></html>

```

`approval.html`:
```html
<!DOCTYPE html><html><head><meta charset="utf-8"><title>Approval</title>
<style>body{font:14px system-ui;margin:8px}.card{border:1px solid #bbb;border-radius:8px;padding:12px;max-width:380px}
button{padding:6px 14px;margin-right:8px;border:0;border-radius:4px;color:#fff;cursor:pointer}#ok{background:#2f9e44}#no{background:#e03131}</style></head>
<body><div class="card"><b id="title">...</b><p id="desc"></p><div id="btns"><button id="ok">Approve</button><button id="no">Reject</button></div><p id="status"></p></div>
<script>/*BRIDGE*/</script>
<script>
const B = window.xtBridge; let task = null;
B.on("ui/notifications/tool-result", (p) => { task = p.structuredContent; document.getElementById("title").textContent = task.title;
  document.getElementById("desc").textContent = `${task.requester} đề xuất: ${task.description} (${task.amount} VND)`; B.sendSize(); });
async function act(decision) { document.getElementById("status").textContent = "Đang gửi...";
  try { const r = await B.callTool("act_on_task", { task_id: task.task_id, decision });
    const d = r.structuredContent; document.getElementById("status").textContent = `act_on_task -> ${d.task_id}: ${d.status}`;
    document.getElementById("btns").style.display = "none";
  } catch (e) { document.getElementById("status").textContent = "Lỗi: " + e.message; } }
document.getElementById("ok").onclick = () => act("approve"); document.getElementById("no").onclick = () => act("reject");
B.connect("xt-approval");
</script></body></html>

```

Lưu ý khi viết:
- Tool trả `dict` thuần thì SDK chỉ trả `content` text, **không có `structuredContent`**. Phải khai báo kiểu trả về `dict[str, Any]` thì mới có `structuredContent` (host chuyển nguyên vào `ui/notifications/tool-result`).
- Host chạy trên trình duyệt (basic-host) gọi thẳng MCP server từ origin khác nên **bắt buộc CORS** (`allow_headers=*`, `expose_headers=Mcp-Session-Id`). Thiếu CORS thì host báo "Failed to connect to any servers". Host thương mại gọi từ backend nên không cần, nhưng dev với host web thì cần.

## 3. Test thật

Chạy:
```bash
cd /tmp/xt-spike-apps && uv venv && uv pip install mcp playwright uvicorn
python src/server.py &                       # :18200
git clone --depth 1 https://github.com/modelcontextprotocol/ext-apps
cd ext-apps/examples/basic-host && npm install && \
  sed -i 's/localhost:8081/localhost:18202/' src/implementation.ts && npm run build   # port sandbox bị hard-code 8081
HOST_PORT=18201 SANDBOX_PORT=18202 SERVERS='["http://localhost:18200/mcp"]' npx tsx serve.ts &   # không có bun nên dùng tsx
python src/probe.py                          # kiểm tra giao thức
python src/e2e.py research/spike-mcp-apps    # Playwright headless
```

### 3.1 Kiểm tra giao thức (`probe.py`, MCP client SDK)
```
TOOL show_org_chart {"ui": {"resourceUri": "ui://xt/org-chart.html"}}
TOOL get_employee {"ui": {"resourceUri": "ui://xt/org-chart.html", "visibility": ["app"]}}
TOOL show_approval_card {"ui": {"resourceUri": "ui://xt/approval-card.html"}}
TOOL act_on_task {"ui": {"resourceUri": "ui://xt/approval-card.html", "visibility": ["app"]}}
RES ui://xt/org-chart.html text/html;profile=mcp-app {'ui': {'prefersBorder': True}}
RES ui://xt/approval-card.html text/html;profile=mcp-app {'ui': {'prefersBorder': True}}
READ ui://xt/org-chart.html text/html;profile=mcp-app 4250 <!DOCTYPE html><html><head><meta charset
```

### 3.2 End-to-end trên basic-host (Playwright headless Chromium)
Kết quả in ra:
```
DETAIL: get_employee -> Tuan | Dev Lead | tuan@xt.vn | báo cáo cho: Minh
STATUS: act_on_task -> T-1: approved
```
Nhật ký phía server (`server-calls.log`), chứng minh lời gọi do UI khởi tạo (qua postMessage -> host -> MCP) đã tới server:
```
{"tool": "show_org_chart", "args": {"client_supports_apps": false}}
{"tool": "get_employee", "args": {"id": 4}}
{"tool": "show_approval_card", "args": {"task_id": "T-1"}}
{"tool": "act_on_task", "args": {"task_id": "T-1", "decision": "approve"}}
```
`get_employee` và `act_on_task` chỉ có `visibility: ["app"]` và chỉ được gọi từ iframe (Playwright click vào nút SVG / nút Approve). Log console host (`e2e-host-console.log`): `Reading UI resource` -> `Loading sandbox proxy` -> `MCP App initialized` -> `Sending tool call result to MCP App`.

Ảnh: `research/spike-mcp-apps/1-orgchart.png`, `2-orgchart-clicked.png` (nút Tuan được chọn, panel hiện kết quả `get_employee`), `3-approval.png`, `4-approval-approved.png`.

### 3.3 Phát hiện đáng chú ý
1. `client_supports_apps(ctx)` trả **False** với basic-host (host không khai báo extension `io.modelcontextprotocol/ui` trong client capabilities), dù host vẫn render UI. Nghĩa là host tham chiếu không đi qua đường negotiation; khi dùng `client_supports_apps` để quyết định trả UI hay text thì phải xử lý host không khai báo (mặc định vẫn trả `structuredContent` + text, UI chỉ là phần thêm). Với Claude/ChatGPT/VS Code: chưa kiểm chứng họ có khai báo hay không.
2. Với handshake `protocolVersion 2025-11-25`, phản hồi `initialize` của server **không chứa** `capabilities.extensions` dù `lowlevel.extensions` có `io.modelcontextprotocol/ui` (gọi `get_capabilities` trực tiếp thì có). Có thể do SDK chỉ phát extensions ở phiên bản giao thức mới hơn: chưa điều tra sâu, chưa thử handshake bản mới. Không chặn việc render vì host dựa vào `_meta.ui.resourceUri` của tool.
3. Basic-host hard-code cổng sandbox 8081 (phải sửa `implementation.ts`), `npm start` dùng `bun` (không có thì chạy `npx tsx serve.ts` sau `npm run build`).
4. Mô hình bảo mật thấy rõ khi chạy: sandbox ở origin khác (18202), CSP set qua HTTP header theo `?csp=` từ `_meta.ui.csp`. SVG/JS inline hoạt động dưới CSP mặc định; mọi `fetch` từ UI ra ngoài sẽ bị chặn trừ khi khai báo `connectDomains` (nên mọi truy cập dữ liệu đi qua `tools/call`, đúng ý xT).

## 4. Giấy phép
- Không dùng thư viện JS nào trong UI (SVG thuần + `bridge.js` tự viết, Apache-2.0). Không có rủi ro watermark/BSL.
- `ext-apps` (repo, `basic-host`, packages): MIT; LICENSE ghi đang chuyển dần sang Apache-2.0 cho đóng góp mới. Cả hai là OSI. Chỉ dùng basic-host để test, không đóng gói vào xT.
- `mcp` 2.3.0: MIT. Playwright (dev/test): Apache-2.0. uvicorn/starlette: BSD-3.
- Nếu sau này dùng gói npm `@modelcontextprotocol/ext-apps` (lớp `App`) thì bundle vào HTML bằng vite-plugin-singlefile: giấy phép MIT/Apache, vẫn OK; spike chọn tự viết bridge để zero dependency.

## 5. Kết luận
- **Chạy được end-to-end** với server Python thuần SDK `mcp` 2.x: 2 tool có UI, HTML SVG tương tác, UI gọi lại tool app-only qua postMessage `tools/call`, tới server, hiển thị kết quả. Bằng chứng: ảnh + log server.
- Hoạt động ở: host tham chiếu `basic-host` (đã chạy thật). Theo tài liệu/web (chưa tự test): Claude, VS Code Insiders, Goose, ChatGPT. ChatGPT: `tools/call` từ UI còn mâu thuẫn giữa các nguồn, cần test thật.
- Đề xuất cho xT: (a) tool hiển thị trả `dict[str, Any]` (có `structuredContent`) + text mô tả để host không hỗ trợ Apps vẫn dùng được; (b) tool thao tác của UI để `visibility=["app"]` (model không thấy, giảm ~15 tool hiển thị cho agent); nhưng hành động ghi (duyệt/từ chối) phải kiểm quyền ở server bằng token người dùng chứ không tin UI; (c) bridge viết tay là đủ cho Tầng 2, hoặc bundle `App` chính thức nếu cần `ui/message`, `ui/update-model-context`; (d) việc cần làm tiếp: test trên Claude.ai/ChatGPT thật qua tunnel HTTPS, kiểm tra domain signing của Claude, ghép với OAuth ở `spike-mcp-auth.md` (UI `tools/call` đi qua host nên mang token của kết nối).
