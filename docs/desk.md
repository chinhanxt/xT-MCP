<!-- SPDX-License-Identifier: Apache-2.0 -->
# xT Desk (`desk/`, gói `@xt/desk`)

Front chat của xT (Tầng 2, kênh demo chính theo `architecture-v2.md` D7): chat tiếng Việt với LLM tương thích OpenAI, gọi tool qua MCP Streamable HTTP, hiển thị MCP Apps inline trong luồng chat. Nền tảng chọn ở `research/spike-desk.md` (prototype B: React/Vite + `@mcp-ui/client` `AppRenderer` + `@modelcontextprotocol/sdk`). Giấy phép Apache-2.0.

Ngoài phạm vi: route `/desk/confirm/{id}` (do backend `xt-desk` render), đăng nhập Keycloak (để lại seam, xem dưới), lịch sử hội thoại.

## Cấu trúc

| Tệp | Vai trò |
|---|---|
| `src/agent.ts` | Vòng lặp agent thuần (inject LLM + MCP): lọc tool, max-steps, tool lỗi/JSON hỏng/tool ngoài hồ sơ thành `tool error` đưa lại cho model |
| `src/llm.ts` | Chat completions OpenAI-compatible, stream SSE (gộp `tool_calls` kiểu OpenAI và kiểu ollama), gửi `reasoning_effort` |
| `src/mcp.ts` | MCP client Streamable HTTP; `AuthHeaders` là seam cho Keycloak |
| `src/profiles.ts` | Hồ sơ tool: `core-10` (TCV1 §10), `all`, hoặc danh sách tên; luôn bỏ tool `visibility:["app"]` |
| `src/markdown.ts` | `marked` + `DOMPurify`: chỉ whitelist thẻ, không ảnh/form/script/style, chỉ `http(s)/mailto`, link `noopener`; token `[PII:...]` hiện thành nhãn "đã che" |
| `src/ToolItem.tsx` | Chip tool-call (mở xem tham số/kết quả), `AppRenderer` cho tool có `_meta.ui.resourceUri`, thẻ `confirmation_required` mở `confirm_url` |
| `public/sandbox.html` | Sandbox proxy của MCP Apps (xem "Sandbox") |
| `vite.config.ts` | Đọc cấu hình từ env, báo lỗi rõ nếu thiếu, proxy `/mcp` và `/llm` (không gắn bearer) |

## Cấu hình

Sao chép `desk/.env.example` thành `desk/.env`. Biến bắt buộc thiếu thì `vite dev/build/preview` dừng với lỗi nêu tên biến (đã kiểm: `make desk-build` không có `.env` báo `thiếu biến cấu hình bắt buộc: DESK_MCP_URL, DESK_LLM_URL, ...`). Không có URL/model nào được ghi cứng trong mã nguồn.

- Trình duyệt chỉ gọi cùng origin: `/mcp` và `/llm`. Proxy (vite dev/preview, hoặc APISIX khi triển khai `https://xt.freepro.online`) chỉ chuyển tiếp tới `DESK_MCP_URL`/`DESK_LLM_URL`. **Không có bearer tĩnh ở bất kỳ đâu** (cấu hình, proxy, gateway): Desk gửi `Authorization` bằng token của chính người dùng (`src/auth.ts`). Cấu hình gateway KHÔNG được gắn bearer cho `/mcp` và `/llm`; hostname sandbox không được route `/mcp`, `/llm`.
- `DESK_AUTH_MODE=oidc` (mặc định): Keycloak chưa tích hợp nên Desk **fail closed** (hiện "Chưa đăng nhập", không gọi MCP/LLM). `dev-unauthenticated` chỉ cho dev/e2e với server không đòi xác thực, có biểu ngữ cảnh báo. Khi có Keycloak, thay `getUserToken()` trong `src/auth.ts`.
- `DESK_XT_ORIGIN`: `confirm_url` chỉ hợp lệ khi đúng origin này + `/desk/confirm/{id}`; `ui/open-link` tới origin này mở thẳng.
- `DESK_APP_CALLS_PER_SEC`, `DESK_APP_CALL_BURST`, `DESK_APP_MAX_INFLIGHT`: rate limit tool call của mỗi instance MCP App.
- `DESK_LLM_REASONING_EFFORT=none`: bắt buộc với ollama `/v1` (`think:false` bị bỏ qua, xem `spike-local-llm.md`).
- `num_ctx >= 16384` cho core-10: `/v1` của ollama không nhận `num_ctx`; đặt phía server ollama (`OLLAMA_CONTEXT_LENGTH=16384`). Lúc chạy `e2e:local-llm` ollama đang ở mặc định 4096 (`/api/ps`: `context_length: 4096`), đủ cho kịch bản 2 tool, không đủ cho core-10.
- `DESK_SANDBOX_URL` phải khác origin với Desk; Desk tự từ chối nếu trùng. Khi triển khai, phục vụ cùng thư mục `dist/` (có `sandbox.html`) trên một hostname riêng.

## Sandbox và bridge (sau audit bảo mật)

`@modelcontextprotocol/ext-apps@2.0.3` và `@mcp-ui/client@7.1.1` **không phát hành** sandbox proxy (chỉ có phía host `AppBridge`), nên `public/sandbox.html` là bản viết theo giao thức, không chép `sandbox.ts` (gói duy nhất có proxy là `@copilotkit/mcp-apps-renderer`, bị loại vì kéo nhiều dep và CSP lỏng). Chủ dự án cần chốt giữ bản này hay đợi ext-apps.

Mô hình 2 lớp iframe: Desk (origin A) > proxy `sandbox.html` (origin B, mã tin cậy) > iframe App `sandbox="allow-scripts"` (origin mờ). Điểm chính:
- CSP là thẻ meta đặt vào CHÍNH tài liệu proxy trước khi tạo iframe App; iframe `srcdoc` kế thừa policy, nên áp trước mọi script của App và không phân tích HTML không tin cậy. `frame-src 'none'` và `form-action 'none'` chặn điều hướng iframe; bộ đếm `load` bỏ iframe nếu bị nạp lại.
- Host không truyền `client` cho `AppRenderer` (vì `AppBridge.connect()` sẽ ghi đè mọi handler bằng proxy thẳng tới server). Mọi yêu cầu của App đi qua `src/bridge.ts`: `tools/call` chỉ khi tool khai `visibility` có `"app"` tường minh, ngoài denylist (`decide_request`, tool ghi...), tham số qua JSON Schema (`ajv`), rate limit token bucket + trần request đang chờ theo instance; `resources/read` chỉ `ui://`; list resource/prompt trả rỗng.
- Desk tự đọc resource `ui://`, lấy `_meta.ui.csp` và chỉ giữ `connectDomains` trùng đúng `DESK_XT_ORIGIN` (kênh `/pii/reveal`); domain khác, wildcard, resource/frame/base-uri bị bỏ.
- `ui/open-link`: cần user activation và tiêu điểm đang ở trong khung App; origin xT mở thẳng, origin khác hiện hộp thoại với URL đầy đủ; bị từ chối thì trả `isError`.
- Vòng 2 (Low, đã sửa): schema thiếu `additionalProperties` coi như `false` cho lời gọi từ App và `arguments` bị cap `DESK_APP_MAX_ARGS_BYTES` (mặc định 32768 byte UTF-8); thêm rate limit CHUNG theo tên tool cho mọi App (`DESK_APP_GLOBAL_CALLS_PER_SEC`/`DESK_APP_GLOBAL_BURST`) ngoài rate limit từng instance; hộp thoại open-link hiện origin (host punycode) nổi bật cùng URL chuẩn hóa một dòng, bỏ ký tự điều khiển/bidi/zero-width, cắt 300 ký tự; activation chỉ tính khi tiêu điểm nằm trong iframe của CHÍNH khung App yêu cầu; `visibility` phải là mảng chuỗi thuộc `model|app`, kiểu khác thì App bị từ chối và model không thấy tool. Test: `tests/regression-round2.test.ts`.
- Hạn chế còn lại: chưa có CSP qua HTTP header (`?csp=`); `AppBridge` (thư viện) vẫn `console.debug` nội dung bridge.

## Lệnh

```bash
make desk-setup        # npm ci + playwright install chromium
make desk-test         # vitest (95 test, gồm hồi quy audit vòng 1-2) + e2e Playwright headless (9 test), LLM giả
make desk-build        # tsc + vite build (cần desk/.env)
make desk-e2e-local    # e2e với ollama thật (đọc LLM từ desk/.env.example)
cd desk && npm run dev # dev server theo desk/.env
```

`make check` tự chạy `desk-test` khi `desk/node_modules` tồn tại (mất ~11s); CI có job `desk` riêng trong `.github/workflows/ci.yml`.

## Kết quả thật (2026-10-15, sau khi sửa theo audit)

`npm test` (vitest): 8 tệp, 95 test pass. `npm run test:audit` (`tests/audit/`, test của người kiểm): 4/4 pass (trước khi sửa: 4/4 FAIL).

E2E headless `npm run e2e` (chromium; server MCP spike chép vào thư mục tạm cổng 19500; server MCP "App thử nghiệm" `e2e/evil_server.py` cổng 19503; LLM giả 19501; Desk 19502 và 19504; sandbox ở `127.0.0.1`, Desk ở `localhost`):

```
✓ [desk] chat: tool-call chip, MCP App inline, markdown an toàn, UI->tool qua bridge
✓ [desk] chat thường không gọi tool
✓ [security] BUG-2a/2b/2c: CSP áp trước mọi script, không bị regex lừa (3 test)
✓ [security] BUG-3: App không điều hướng được iframe (form + location), bridge không chuyển tiếp sau điều hướng
✓ [security] BUG-1 + BUG-4: chỉ tool app-only, validate tham số, ui:// only, rate limit
✓ [security] BUG-5: ui/open-link cần user activation; origin ngoài xT phải qua hộp thoại có URL đầy đủ
✓ [security] BUG-7: _meta.ui.csp chỉ cho connect tới origin xT
9 passed (15.1s)
```

Đã kiểm đột biến: gỡ `frame-src 'none'`, `form-action 'none'` và bộ đếm `load` khỏi `sandbox.html` thì test BUG-3 FAIL. Burst 100 lời gọi song song: <= 10 tới server, >= 90 bị từ chối.

`e2e:local-llm` (ollama `qwen3.5:4b`, `reasoning_effort=none`, chạy lại sau sửa): pass, `render_seconds=7.5 total_seconds=11.6`. Câu trả lời cuối lần này diễn giải quan hệ chưa chuẩn ("Minh (CTO) -> có nhân viên Tuan" đúng, kèm câu thừa "Thiếu thông tin"), rủi ro 4B đã biết.

Ảnh: `docs/img/desk-1-start.png`, `desk-2-orgchart-inline.png`, `desk-3-ui-tool-call.png` (e2e giả LLM), `desk-4-local-llm.png` (ollama thật).

![Desk bắt đầu](img/desk-1-start.png)
![Sơ đồ inline](img/desk-2-orgchart-inline.png)
![Click nút gọi get_employee](img/desk-3-ui-tool-call.png)
![Local LLM thật](img/desk-4-local-llm.png)

## Hạn chế chấp nhận của mô hình iframe: rò rỉ qua WebRTC / DNS prefetch

App nằm trong iframe `sandbox` không chặn được `RTCPeerConnection` (ICE/STUN/TURN gửi UDP ra ngoài, không qua `connect-src`), và DNS prefetch/resolve là kênh rò hẹp. Đây là giới hạn chung của mô hình MCP Apps (spec và basic-host cũng vậy), được chấp nhận ở v0.1.

Đã thử thực nghiệm trong Chromium headless (Playwright 1.64.1): trang có CSP `default-src 'none'; script-src 'unsafe-inline'`, dựng `RTCPeerConnection` với `stun:127.0.0.1:19999`, `createOffer` rồi đếm ICE candidate. Không CSP: `candidates=1`. Thêm directive `webrtc 'block'`: trình duyệt in `Unrecognized Content-Security-Policy directive 'webrtc'` và vẫn `candidates=1`, tức directive này chưa dùng được trong Chromium này, nên Desk KHÔNG thêm nó (không có tác dụng). `Permissions-Policy` không có feature nào cho WebRTC.

Giảm thiểu: (1) không đưa PII thô hay bí mật vào kết quả tool gửi cho App (mô hình vé `spike-pii-lane.md`; dữ liệu `[PII:token]` đã che); (2) chỉ hiển thị App từ server xT tin cậy; (3) khi Chromium hỗ trợ CSP `webrtc 'block'` thì thêm vào `buildCsp` của `public/sandbox.html` và thêm test (đổi `candidates` thành 0); (4) khi cần chặt hơn, chạy sandbox ở trình duyệt đặt policy doanh nghiệp `WebRtcIPHandling`/`WebRtcUdpPortRange`.

## Chưa làm / hạn chế

- Chưa thử với catalog xT thật (chưa có server): hồ sơ `core-10` đã có test đơn vị nhưng e2e dùng server spike nên đặt `DESK_TOOL_PROFILE=show_org_chart,show_approval_card`.
- Chưa có lịch sử hội thoại, đăng nhập Keycloak (mode `oidc` hiện fail closed), route `/desk/confirm/{id}`; thẻ `confirmation_required` chỉ có test đơn vị (`test/confirm.test.ts`, `tests/regression-bridge.test.ts`), chưa có e2e.
- Bundle 896 kB (251 kB gzip), chưa tách chunk.
- Tool `show_approval_card`/`act_on_task` của server spike chưa có e2e riêng.
