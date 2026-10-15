<!-- SPDX-License-Identifier: Apache-2.0 -->
# Audit bảo mật xT Desk (`desk/`, chưa commit) — 2026-10-15

Người kiểm: agent Opus (tester/verifier). Phạm vi: `desk/public/sandbox.html`, `src/ToolItem.tsx`, `src/App.tsx`, `src/markdown.ts`, `src/confirm.ts`, `vite.config.ts`, cùng hành vi của `@mcp-ui/client@7.1.1` (`AppRenderer`/`AppBridge`, bundle ext-apps 1.7.5) mà Desk dùng.

Môi trường: bản sao app ở `/tmp/xt-desk-audit/app` (node_modules symlink), `vite build && vite preview`, Desk ở `http://localhost:19602`, sandbox ở `http://127.0.0.1:19602/sandbox.html`. MCP là bản chép `research/spike-mcp-apps/server.py` (mcp 2.3.0, cổng 19600) cộng thêm các App thử nghiệm và hai tool chỉ dành cho model (`secret_admin`, `decide_request` giả, `visibility:["model"]`). LLM giả ở 19601, máy nhận request ở 19603. Chromium headless qua Playwright, không đụng display thật. Đã dừng toàn bộ tiến trình sau khi chạy.

## Tổng hợp

| # | Hạng mục | Kết quả |
|---|---|---|
| 1 | Cô lập sandbox (origin, cookie, storage, parent/top, opener) | **PASS** |
| 1b | CSP áp cho HTML App | **FAIL** (BUG-2, BUG-3, BUG-7) |
| 2a | Kiểm tra source/origin của postMessage | **PASS** (có ghi chú) |
| 2b | Allowlist tools/call theo visibility | **FAIL** (BUG-1) |
| 2c | Chèn vào chat/LLM context mà host không kiểm soát | **PASS** |
| 2d | Giới hạn tần suất tool call từ UI | **FAIL** (BUG-4) |
| 2e | `ui/open-link` | **FAIL** (BUG-5) |
| 3 | XSS khi render markdown | **PASS** (1 lỗi Low: BUG-8) |
| 4 | Bearer token / `.env` / `VITE_` | **PASS** cho mã hiện tại; có rủi ro thiết kế lúc triển khai (BUG-9) |
| 5 | `confirm_url` chỉ được mở trang của xT | **FAIL** (BUG-6) |

**Kết luận: FAIL.** Desk chưa đủ an toàn để hiển thị HTML App không tin cậy. Phải sửa BUG-1, BUG-2, BUG-3 trước khi demo. BUG-4 đến BUG-7 phải sửa trước khi phát hành.

## Bằng chứng PASS

- **Hai origin:** iframe ngoài (proxy) là `http://127.0.0.1:19602/sandbox.html`, có `sandbox="allow-scripts allow-same-origin allow-forms"` (do `@mcp-ui/client` đặt). Origin này khác `localhost:19602` của Desk, và proxy là mã tin cậy. Iframe trong (App) có `sandbox="allow-scripts allow-forms"` và không có `allow-same-origin`, nên `self.origin === "null"`.
- **Thử từ mã App:** `document.cookie`, `localStorage`, `parent.document`, `top.document`, `top.localStorage`, `top.location.href` đều ném `SecurityError`. `window.opener` là `null`. Gán `top.location` ném `SecurityError`, Desk vẫn ở `/`. `fetch` tới `localhost:19602/mcp` (có `credentials:"include"`), tới `127.0.0.1:19602/mcp` và tới origin ngoài đều bị chặn với `connect-src 'none'`. Ảnh và WebSocket ra ngoài không tạo request nào (máy nhận ghi 0 lượt).
- **CSP mặc định mà proxy dựng ra:** `default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval' blob: data: ; style-src 'unsafe-inline' ; img-src data: blob: ; font-src data: ; media-src data: blob: ; connect-src 'none'; frame-src 'none'; base-uri 'none'`.
- **postMessage:** proxy chỉ nhận lệnh từ `window.parent` có origin bằng `document.referrer`. Nó chỉ chuyển tiếp tin từ `inner.contentWindow` và gửi lên với `targetOrigin = hostOrigin`. Phía host, transport của AppBridge chỉ nhận tin có `source === proxy.contentWindow`, sau đó kiểm schema JSON-RPC bằng zod. Thử thật: App gửi thẳng `tools/call secret_admin {marker:"direct-to-top"}` lên `top`, server ghi 0 lượt gọi. Ghi chú: host gửi xuống bằng `postMessage(msg, "*")`, chấp nhận được vì đích là `contentWindow` của proxy cố định.
- **Không chèn được vào chat hay context:** `ui/message` trả `-32601 Method not found`, `ui/update-model-context` và `sampling/createMessage` trả `-32601`. Kiểm toàn bộ request gửi tới LLM giả: không có chuỗi `INJECTED`. Danh sách tool đưa cho LLM chỉ gồm tool trong hồ sơ, không có tool `visibility:["app"]`.
- **Markdown:** 21 payload, mỗi payload thử riêng (jsdom) và gộp lại (Chromium thật qua stream LLM). Bộ payload gồm `javascript:` (thường, hoa thường lẫn lộn, entity), `data:`, `vbscript:`, link tương đối, `<svg onload>`, `<img onerror>`, `<style>`, thuộc tính `style`, `<iframe>`, `<form>`, mXSS (`math/mtext/mglyph/style`, `noscript`, `svg>style`), `details ontoggle`, `<base>`, `<meta refresh>`, ảnh markdown, và chèn vào token `[PII:...]`. Kết quả: `window.__xss` không được đặt, 0 dialog, 0 phần tử hay thuộc tính nguy hiểm, mọi `href` còn lại đều là `https:`, và link luôn có `target=_blank rel="noopener noreferrer nofollow"`.
- **Token:** build với `DESK_MCP_TOKEN=SECRET-MCP-TOKEN-AUDIT-123` và `DESK_LLM_API_KEY=SECRET-LLM-KEY-AUDIT-456`, rồi `grep -rl SECRET- dist/` cho 0 tệp. Bearer chỉ được gắn ở proxy phía server (`vite.config.ts`), không có trong `__DESK_CONFIG__`, không có biến `VITE_` nào, và `.env.example` không có `VITE_`. `GET /.env` trả `index.html` (SPA fallback), không trả tệp. Trong log console của trang có 0 dòng chứa token. Proxy `/mcp` từ origin lạ: MCP SDK trả `403 Invalid Origin header`. Content-Type `text/plain` bị trả `400`. Preflight của Vite không có `Access-Control-Allow-Origin`.

## Lỗi

### BUG-1 (High): App gọi được mọi tool, kể cả tool chỉ dành cho model như `decide_request`
- **Nguyên nhân:** `ToolItem.tsx` không truyền `onCallTool` cho `AppRenderer`. Khi đó `AppBridge.connect()` (bundle `@mcp-ui/client`) tự đặt `oncalltool = client.request("tools/call", params)` và chuyển thẳng mọi tên tool tới server, không kiểm `_meta.ui.visibility`. Mọi `resources/read` cũng được chuyển tiếp.
- **Tái hiện:** App gọi `tools/call` với `secret_admin` và `decide_request` (cả hai `visibility:["model"]`, không nằm trong hồ sơ tool). Kết quả là cả hai đều `OK`, và `server-calls.log` ghi `secret_admin {marker:"from-app"}` cùng `decide_request {decision:"approve"}`.
- **Tác động:** trái spec MCP Apps (tool phải có `"app"` trong visibility) và trái `tool-contracts-v1.md` ("Thẻ MCP App: không có tool app-only để ghi"). Với catalog xT thật, App có thể âm thầm gọi `create_record`, `submit_request` hay `transition_record`. `decide_request` vẫn bị chặn ở server bằng `confirm_url`, nhưng App vẫn kích hoạt được luồng đó và nhận được `confirm_url`.
- **Sửa:** truyền `onCallTool` kiểm theo danh sách tool lấy từ `tools/list`. Chỉ cho gọi khi `_meta.ui.visibility` có `"app"` một cách tường minh (không dựa vào mặc định `["model","app"]`). Thêm denylist cứng cho các tool ghi hoặc human-only (`decide_request`, …). Nếu không đạt thì ném `McpError(InvalidParams)`. Với `onReadResource`, chỉ cho đọc URI `ui://`.

### BUG-2 (High): bypass CSP trong `sandbox.html`
- **Nguyên nhân:** proxy chèn thẻ `<meta http-equiv=CSP>` ngay sau lần khớp đầu tiên của `/<head[^>]*>/i`. Regex này khớp cả `<header>`, khớp `<head>` nằm trong comment, và mọi script đứng trước `<head>` đều đã chạy trước khi CSP có hiệu lực. Theo HTML spec, meta CSP không nằm trong `<head>` thì bị bỏ qua.
- **Tái hiện:** 3 App, mỗi App `fetch` tới một origin không khai báo. (a) HTML bắt đầu bằng `<!--<head>-->`. (b) Body có `<header>`. (c) `<script>` đứng trước `<head>`. Kết quả: cả 3 đều `LEAK` và máy nhận ghi `/csp-comment`, `/csp-header`, `/csp-prehead`. App đối chứng (HTML chuẩn) bị `BLOCKED`.
- **Sửa:** luôn đặt meta CSP ở **đầu tuyệt đối** của srcdoc (`inner.srcdoc = meta + html`). Parser sẽ đặt nó vào head ngầm trước mọi nội dung. Tốt hơn nữa, phục vụ proxy với CSP ở HTTP header theo `?csp=` như basic-host (srcdoc kế thừa policy của proxy).

### BUG-3 (Medium-High): App điều hướng iframe ra trang ngoài, thoát CSP mà vẫn dùng được bridge
- **Nguyên nhân:** sandbox có `allow-forms`, và CSP không có `form-action` (directive này không kế thừa từ `default-src`). Iframe nào cũng tự điều hướng được chính nó. Sau khi điều hướng, tài liệu mới không còn CSP của srcdoc. Proxy vẫn chuyển tiếp tin nhắn vì nó so `ev.source === inner.contentWindow`, mà WindowProxy không đổi qua các lần điều hướng.
- **Tái hiện:** App submit form GET tới `http://127.0.0.1:19603/evilpage?d=<dữ liệu>`. Máy nhận ghi lại query chứa dữ liệu. Trang ngoài đó tiếp tục gửi `tools/call secret_admin {marker:"after-nav"}`, được trả kết quả qua bridge (log server có ghi), và `fetch` tự do (`/after-nav-fetch`).
- **Sửa:** trong proxy, đếm sự kiện `load` của iframe trong. Từ lần thứ 2 trở đi thì xóa iframe và ngừng chuyển tiếp. Thêm `form-action 'none'` vào CSP và bỏ `allow-forms` nếu không cần.

### BUG-4 (Medium): không giới hạn tần suất tool call từ UI
- **Tái hiện:** App bắn 100 `tools/call` song song: 100 OK trong 234 ms, server ghi đủ 100 lượt.
- **Sửa:** dùng token bucket theo từng App trong `onCallTool` (ví dụ 5 lượt/giây, tối đa 10 lượt dồn), và giới hạn số request đang chờ.

### BUG-5 (Medium): `ui/open-link` mở mọi URL http(s), không cần người dùng thao tác
- **Nguyên nhân:** `App.openLink` chỉ kiểm `^https?://` rồi gọi `window.open`.
- **Tái hiện:** App gọi `ui/open-link https://evil.example/...` ngay lúc nạp và gọi lại khi bấm nút. Cả hai lần đều được `ACCEPTED`, Playwright ghi 2 popup.
- **Tác động:** phishing, ví dụ trang "xác nhận xT" giả.
- **Sửa:** cho phép origin xT đã cấu hình. Origin khác thì hiện hộp thoại của host hiển thị URL đầy đủ để người dùng tự xác nhận. Từ chối khi không có user activation.

### BUG-6 (Medium): `confirm_url` không kiểm origin
- **Nguyên nhân:** `confirm.ts` chấp nhận mọi `https?://`. Chưa có biến cấu hình nào cho origin xT.
- **Tái hiện:** tool trả `confirmation_required` với `confirm_url: https://evil.example/desk/confirm/cf_1`. Desk hiện thẻ "Mở trang xác nhận" có `href` trỏ tới evil.example.
- **Test FAIL kèm theo:** `desk/tests/audit/confirm-origin.test.ts`, chạy bằng `npx vitest run -c tests/audit/vitest.audit.config.ts`: 4/4 FAIL.
- **Sửa:** thêm `DESK_XT_ORIGIN` bắt buộc. Chỉ nhận `new URL(u).origin === DESK_XT_ORIGIN` và `pathname` khớp `^/desk/confirm/[\w-]+$`.

### BUG-7 (Medium): không truyền `_meta.ui.csp` và `permissions` của resource cho sandbox
- **Nguyên nhân:** `AppRenderer` được gọi với `sandbox={{ url }}` nên `sendSandboxResourceReady({ html, csp: undefined })`.
- **Tái hiện:** App khai báo `connectDomains:["http://127.0.0.1:19603"]` nhưng `fetch` tới đó vẫn bị `BLOCKED`.
- **Tác động:** CSP chặt hơn spec nên không gây hở bảo mật, nhưng kênh xem PII thật (`spike-pii-lane.md`: iframe `fetch /pii/reveal` qua `connectDomains`) không chạy được trên Desk.
- **Sửa:** đọc `_meta.ui` của resource rồi truyền `sandbox.csp`. Proxy phải từ chối `*` và scheme khác `https:` trong domain: regex hiện tại cho phép `*`, nên một server khai `*` sẽ thành `connect-src *`.

### BUG-8 (Low): markdown cho phép thuộc tính `class`
- **Tái hiện:** LLM xuất `<a class="confirm-card" href="https://evil.example">Mở trang xác nhận</a>` và link này giữ nguyên class (giả giao diện thẻ xác nhận).
- **Sửa:** bỏ `class` khỏi `ALLOWED_ATTR`. Thêm class `pii` sau khi sanitize, hoặc qua hook chỉ áp cho span PII.

### BUG-9 (Rủi ro triển khai, High nếu đưa lên mạng nguyên trạng)
- `docs/desk.md` mô tả APISIX gắn bearer cố định cho `/mcp` và `/llm` tại `xt.freepro.online`. Như vậy bất kỳ ai truy cập được hostname đều dùng được quyền của token đó, vì không có đăng nhập. Phải có phiên Keycloak (`AuthHeaders`) trước khi công khai.
- Ở dev/preview, proxy `/llm` gắn API key cho mọi request, không kiểm Origin. Chưa kiểm chứng với upstream thật.
- Hostname sandbox không được route `/mcp` và `/llm`.
- Phụ: `AppBridge` `console.debug` toàn bộ tin nhắn bridge, kể cả kết quả tool. Nếu dữ liệu chưa che PII thì PII lọt vào console.

## Ghi chú về test
- Test FAIL đã thêm (chỉ tạo tệp mới, có SPDX): `desk/tests/audit/confirm-origin.test.ts` và `desk/tests/audit/vitest.audit.config.ts`. Không sửa mã nguồn app. `npm test` vẫn pass 20/20.
- BUG-1 đến BUG-5 và BUG-7 đã tái hiện bằng Playwright headless trên bản sao ở `/tmp/xt-desk-audit`, như mô tả ở trên. Bộ App thử nghiệm không được đưa vào repo.


## Phản hồi tác giả (2026-10-15)

Đã sửa trong `desk/`, mỗi lỗi có test hồi quy; kết quả ở `docs/desk.md` mục "Kết quả thật". `npm test` 63/63, `npm run test:audit` 4/4, e2e headless 9/9 (gồm 7 test bảo mật dùng "App thử nghiệm" trong `desk/e2e/evil_server.py`).

| Lỗi | Cách sửa | Test |
|---|---|---|
| BUG-1 | `AppRenderer` không còn nhận `client` (vì `AppBridge.connect()` ghi đè handler); mọi `tools/call` đi qua `onCallTool` ở `src/bridge.ts`: yêu cầu `visibility` có `"app"` tường minh, denylist (`decide_request`...), validate `ajv` theo `inputSchema`; `resources/read` chỉ `ui://`; list resource/prompt rỗng | `tests/regression-bridge.test.ts`, e2e BUG-1 |
| BUG-2 | CSP là thẻ meta đặt vào tài liệu proxy trước khi tạo iframe; `srcdoc` giữ nguyên HTML và kế thừa policy (không regex) | e2e BUG-2a/b/c, `tests/regression-misc.test.ts` |
| BUG-3 | `frame-src 'none'` + `form-action 'none'` ở proxy, bỏ `allow-forms`, bỏ iframe khi `load` lần 2, chỉ nhận resource một lần | e2e BUG-3 (đã kiểm đột biến: gỡ lớp bảo vệ thì FAIL) |
| BUG-4 | Token bucket + trần request đang chờ theo instance App (`DESK_APP_*`) | unit + e2e (100 song song, <= 10 tới server) |
| BUG-5 | Cần user activation và tiêu điểm trong khung App; origin xT mở thẳng, origin khác hiện `confirm` với URL đầy đủ; từ chối trả `isError` | unit + e2e BUG-5 |
| BUG-6 | `DESK_XT_ORIGIN` bắt buộc; `confirm_url` phải đúng origin + `/desk/confirm/{id}`, không credentials/query; thiếu cấu hình thì từ chối | `tests/audit/confirm-origin.test.ts`, unit |
| BUG-7 | Desk đọc resource, truyền `_meta.ui.csp` đã lọc: chỉ `connectDomains` trùng origin xT (kênh `/pii/reveal`); proxy từ chối wildcard/định dạng lạ | unit + e2e BUG-7 (xT `OK`, origin khác `BLOCKED`) |
| BUG-8 | Markdown chỉ giữ `href/target/rel`; nhãn PII dựng bằng DOM sau sanitize | `tests/regression-misc.test.ts` |
| BUG-9 | Bỏ `DESK_MCP_TOKEN`/`DESK_LLM_API_KEY` và mọi header bearer ở proxy; `src/auth.ts` dùng token người dùng, mode `oidc` fail closed khi chưa đăng nhập (Keycloak chưa tích hợp), `dev-unauthenticated` chỉ cho dev/e2e có biểu ngữ | unit (không fetch khi chưa đăng nhập; cấu hình không chứa bearer) |

Còn lại: CSP qua HTTP header (`?csp=`) chưa có (proxy dùng meta kế thừa); `console.debug` của `AppBridge` (thư viện) vẫn in nội dung bridge; sandbox proxy vẫn là bản viết tay vì gói npm không phát hành.

## Vòng 2 (2026-10-15, kiểm lại bản sửa)

Môi trường: bản sao `desk/` ở `/tmp/xt-desk-audit2`, cổng e2e đổi từ 195xx sang 196xx, Chromium headless, server "độc" của tác giả (`e2e/evil_server.py`). Xong thì dừng toàn bộ tiến trình và xóa thư mục tạm.

Đã chạy:
1. Bộ test của tác giả: `npm test` 63/63, `test:audit` 4/4, e2e 9/9. Bộ e2e gồm BUG-2a/b/c (CSP qua comment, `<header>`, script trước `<head>`), BUG-3 (form và `location`), BUG-1+4, BUG-5, BUG-7.
2. Bộ kiểm biên độc lập của tôi, 30 ca, gọi trực tiếp `isXtConfirmUrl`, `checkOpenLink`, `sanitizeUiCsp`, `createAppBridgeHandlers`. Bộ này chỉ dùng tạm, không đưa vào repo.

Các vector sandbox runtime mới (about:blank, javascript:, window.open, `<base>`, meta refresh, WebRTC) được **đánh giá bằng đọc mã và theo ngữ nghĩa trình duyệt**. Tôi không viết lại App thử nghiệm riêng cho các vector này (xem lý do ở vòng 1).

| Hạng mục | Kết quả | Bằng chứng |
|---|---|---|
| BUG-1: visibility | **PASS** | `Model_Only` có `_meta.UI` (sai hoa thường) bị từ chối. `DECIDE_REQUEST`, `__proto__`, `constructor`, `toString` cho kết quả "không tồn tại". `decide_request` khai `["app"]` vẫn bị denylist chặn. Không có race với `tools/list`: danh sách lấy 1 lần lúc connect, không nghe `list_changed`, và `AppRenderer` không nhận `client` |
| BUG-1: schema | **PASS** (Low) | `{id:"1"}` và `{}` bị từ chối. `$ref` trỏ ra ngoài làm compile lỗi nên từ chối (fail closed). 20 lần validate payload 20 MB sai kiểu mất 14 ms. Còn lại Low: thuộc tính thừa lọt qua khi schema của server không có `additionalProperties:false` (server vẫn tự validate), và không có trần kích thước `arguments` |
| BUG-2: CSP | **PASS** | e2e 2a/b/c pass. Meta được chèn vào tài liệu proxy trước khi tạo iframe, và srcdoc kế thừa policy container. Meta CSP mà App tự thêm chỉ siết thêm, không nới được. `base-uri 'none'` |
| BUG-3: điều hướng | **PASS** | e2e pass. `frame-src 'none'` của tài liệu cha chặn điều hướng ra http(s), data: và blob:. about:blank, reload và javascript: tạo tài liệu mới thì `load` lần 2 làm proxy bỏ iframe (tài liệu từ javascript: còn kế thừa CSP). Không có `allow-popups` nên `window.open` bị chặn, không có `allow-forms` nên form bị chặn, không có `allow-top-navigation` nên không điều hướng được top |
| BUG-4: rate limit | **PASS** (Low) | Mỗi instance chỉ được 3/3 lần ở mức burst, server nhận 3 lần. Lời gọi sai không tốn token nhưng cũng không tới server. Còn lại Low: mỗi instance App có bucket riêng nên N instance được N×burst. Số instance chỉ tăng khi LLM gọi tool, và bị giới hạn bởi `DESK_MAX_STEPS` |
| BUG-5: open-link | **PASS** (Low) | Không có activation thì từ chối. Sự kiện giả lập (`isTrusted=false`) không tạo được user activation. Origin giả mạo xT (`xt…online.evil.example`, `…@evil.example`, IDN) đều bị chuyển sang `confirm`. `javascript:`, `data:`, `file:`, `blob:` bị từ chối. Còn lại Low: (a) `confirm()` in URL thô, nên URL chứa `\n` có thể chèn dòng chữ giả vào hộp thoại; nên hiển thị `new URL(u).origin` tách riêng. (b) Activation và focus được tính cho mọi `.app-frame`, nên App A dùng được cú bấm của người dùng vào App B |
| BUG-6: confirm_url | **PASS** | 19 dạng giả mạo bị từ chối: userinfo@, `user:pw@`, IDN (chữ і Cyrillic), dấu chấm cuối, cổng 8443, `../`, `%2e%2e%2f`, `\@`, `//evil`, http, query, hash, id rỗng, CRLF, `javascript:`. Chỉ nhận các dạng chuẩn hóa tương đương (`:443`, hoa thường, `/./`, `\n` bị URL parser bỏ, mà `<a href>` cũng chuẩn hóa y hệt). Thiếu `DESK_XT_ORIGIN` thì từ chối |
| BUG-7: connectDomains | **PASS** | Chỉ nhận đúng origin xT. Các dạng `/path`, `:443`, `*.`, `"xt evil"`, `;script-src *`, dấu chấm cuối, http đều cho `{}`. e2e BUG-7 pass |
| BUG-8: markdown | **PASS** | Chỉ còn `href/target/rel`. Span PII dựng bằng DOM sau khi sanitize |
| BUG-9: token | **PASS** | Không còn bearer tĩnh trong `vite.config.ts` hay cấu hình. `oidc` fail closed (có unit test). `dev-unauthenticated` có biểu ngữ |
| Rủi ro còn lại (chưa kiểm chứng thực nghiệm) | **Medium, chấp nhận** | Chromium không có directive CSP cho WebRTC, và `sandbox` không chặn `RTCPeerConnection`, nên App có thể gửi dữ liệu ra ngoài qua STUN/TURN. DNS prefetch cũng là kênh rò hẹp. Spec MCP Apps và basic-host đều có giới hạn này. Giảm thiểu: không đưa PII thô vào kết quả tool gửi cho App (đúng mô hình vé ở `spike-pii-lane.md`) |

Đề xuất thêm (không chặn phát hành): hiển thị origin đã chuẩn hóa trong hộp thoại open-link và gắn activation với đúng iframe đang yêu cầu; dùng một rate limit chung cho mọi App của phiên; đặt trần kích thước `arguments`.

**Kết luận vòng 2: PASS có điều kiện.** 9/9 lỗi vòng 1 đã được sửa và có test hồi quy. Không tìm được cách bypass mức High hay Medium nào trong các vector được giao. Còn 5 điểm Low và rủi ro WebRTC/DNS vốn có của mô hình iframe. Desk đủ điều kiện để demo App từ server xT.


## Phản hồi tác giả, vòng 2 (2026-10-15)

Đã sửa cả 5 điểm Low, test ở `desk/tests/regression-round2.test.ts` (vitest 95/95, `test:audit` 4/4, e2e 9/9):
1. `closeSchema`: thiếu `additionalProperties` coi như `false` (đệ quy), `arguments` bị cap `DESK_APP_MAX_ARGS_BYTES` (32768 byte UTF-8).
2. Rate limit chung theo tên tool cho mọi instance (`DESK_APP_GLOBAL_*`); test: 4 instance x 10 lời gọi chỉ 5 tới server; slot instance được nhả khi bị chặn ở mức chung.
3. `formatOpenLinkPrompt`: origin punycode nổi bật, URL chuẩn hóa một dòng, bỏ điều khiển/bidi/zero-width, cắt 300 ký tự.
4. `isActivationInFrame`: tiêu điểm phải nằm trong iframe của đúng khung App yêu cầu (App A không mượn cú bấm vào App B).
5. `normalizeVisibility`: chỉ mảng chuỗi thuộc `model|app`; chuỗi, object, null, giá trị lạ đều bị từ chối (App) và ẩn (model); thiếu `visibility` thì App không gọi được.

WebRTC/DNS prefetch: ghi là hạn chế chấp nhận ở `docs/desk.md`. Thử thực nghiệm: Chromium headless không hỗ trợ CSP `webrtc 'block'` (cảnh báo directive lạ, vẫn `candidates=1`), nên không thêm vào proxy.
