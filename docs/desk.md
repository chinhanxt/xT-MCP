<!-- SPDX-License-Identifier: Apache-2.0 -->
# xT Desk (`desk/`, gói `@xt/desk`)

Front chat của xT (Tầng 2, kênh demo chính theo `architecture-v2.md` D7): chat tiếng Việt với LLM tương thích OpenAI, gọi tool qua MCP Streamable HTTP, hiển thị MCP Apps inline trong luồng chat. Nền tảng chọn ở `research/spike-desk.md` (prototype B: React/Vite + `@mcp-ui/client` `AppRenderer` + `@modelcontextprotocol/sdk`). Giấy phép Apache-2.0.

Route `/desk/confirm/{id}` do backend render. Desk đăng nhập Keycloak thật; hội thoại chỉ giữ trong bộ nhớ của phiên kết nối và được xóa khi đăng xuất, chưa lưu lịch sử bền.

## Cấu trúc

| Tệp | Vai trò |
|---|---|
| `src/agent.ts` | Vòng lặp agent thuần (inject LLM + MCP): lọc tool, max-steps, tool lỗi/JSON hỏng/tool ngoài hồ sơ thành `tool error` đưa lại cho model |
| `src/llm.ts` | Chat completions OpenAI-compatible, stream SSE (gộp `tool_calls` kiểu OpenAI và kiểu ollama), gửi `reasoning_effort` |
| `src/mcp.ts` | MCP client Streamable HTTP; `AuthHeaders({method, url})` là seam cho Keycloak (mỗi request một DPoP proof mới) |
| `src/dpop.ts` | DPoP (RFC 9449): khóa ECDSA P-256 non-extractable trong IndexedDB (`xt-desk`/`keys`/`dpop`, lưu nguyên `CryptoKey`, không export khóa riêng), `proof()` ES256 `dpop+jwt`, `thumbprint()` RFC 7638 |
| `src/profiles.ts` | Hồ sơ tool: `standard` (mặc định: `inspect_workspace`, `describe_model` + core-10), `core-10` (TCV1 §10), `all`, hoặc danh sách tên; luôn bỏ tool `visibility:["app"]` |
| `src/markdown.ts` | `marked` + `DOMPurify`: chỉ whitelist thẻ, không ảnh/form/script/style, chỉ `http(s)/mailto`, link `noopener`; token `[PII:...]` hiện thành nhãn "đã che" |
| `src/ToolItem.tsx` | Chip tool-call (mở xem tham số/kết quả), `AppRenderer` cho tool có `_meta.ui.resourceUri`, khóa mục tool-call là `t-{lượt}-{tool_call_id}` (`src/items.ts`; LLM/server có thể dùng lại cùng `tool_call_id` ở nhiều lượt, trước đây lượt sau ghi đè thẻ xác nhận và iframe App của lượt trước), nên thẻ xác nhận và iframe App của các lượt cũ luôn còn trong lịch sử và không bị remount; thẻ `confirmation_required` mở `confirm_url` (`src/confirm.ts` đọc `structuredContent`, nếu không có thì parse JSON an toàn từ `content[0].text` như lỗi xT thật `isError`; `confirm_url` phải đúng `DESK_XT_ORIGIN`, và khớp `confirmation_id` nếu có) |
| `public/sandbox.html` | Sandbox proxy của MCP Apps (xem "Sandbox") |
| `vite.config.ts` | Đọc cấu hình từ env, báo lỗi rõ nếu thiếu, proxy `/mcp` và `/llm` (không gắn bearer) |

## Giao diện v2 (theo mockup đã duyệt `docs/mockups/desk/`)

Desk v2 chỉ đổi lớp trình bày: phông Be Vietnam Pro 17px (`@fontsource/be-vietnam-pro`, OFL-1.1), icon Lucide (`lucide-react`, ISC), token màu và thành phần (panel, tab, chip trạng thái ok/held/block/neutral, thẻ xác nhận) chép từ `docs/mockups/desk/src/desk.html` vào `src/styles.css`. Logic `mcp.ts`, `auth.ts`, `oidc.ts`, `dpop.ts`, `bridge.ts`, `confirm.ts`, `pii.ts`, `portal-*.ts`, `agent.ts`, `llm.ts`, `config.ts` không đổi. Hash route giữ nguyên: Trợ lý = Desk (không hash), Con người = `#/H`, Quy trình = `#/P`, Dữ liệu = `#/D`, Trí tuệ = `#/I`, Ứng dụng = `#/T3` (`#/Kho` cũ vẫn về `#/T3`). Nút điều hướng giữ tên truy cập `H/P/D/I/T3/Desk` (nhãn tiếng Việt và icon là phần hiển thị).

### Kiểm kê: mọi năng lực cũ và chỗ của nó trong v2 (không bỏ năng lực nào)

| Năng lực | Mã nguồn | Vị trí v2 |
|---|---|---|
| Đăng nhập Keycloak (PKCE + DPoP), trạng thái đang đăng nhập, lỗi đăng nhập | `App.tsx`, `auth.ts`, `oidc.ts`, `dpop.ts` | Màn đăng nhập riêng (thẻ giữa trang, logo xT) |
| Chọn doanh nghiệp (đổi tenant, đăng nhập lại theo tổ chức) | `App.tsx` (`leaveSession`) | Thanh trên, ô "Doanh nghiệp" có icon tòa nhà; một tổ chức thì hiện tên tổ chức |
| Đăng xuất (xóa phiên, khung nhúng, cầu nối) | `App.tsx` | Chân thanh bên trái |
| Thông tin model · hồ sơ tool · số công cụ | `App.tsx` (`header .meta`) | Thanh trên, chữ nhỏ bên phải |
| Điều hướng 6 không gian, Quay lại/Tiến của trình duyệt | `portal-nav.ts` | Thanh bên trái: Trợ lý, Con người, Quy trình, Dữ liệu, Trí tuệ, Ứng dụng |
| Ô tìm người | mới, dùng lại `find_people {query, limit:50}` của Con người | Thanh trên: Enter mở Con người và tìm đúng như ô tìm nhân sự |
| Chuông | mới, chỉ điều hướng | Thanh trên: mở Quy trình (hộp duyệt); không có chấm đếm vì không gọi thêm tool |
| Trợ lý Desk dùng LLM local: chat, stream, gọi tool, max-steps, ghi chú lỗi | `App.tsx`, `agent.ts`, `llm.ts`, `items.ts`, `markdown.ts` | Trợ lý: khung hội thoại (bong bóng người dùng/trợ lý, icon bot), ô soạn + nút gửi |
| Gợi ý câu hỏi | mới, chỉ điền sẵn câu hỏi | Trợ lý: hàng gợi ý dưới hội thoại |
| Chip tool-call + xem tham số/kết quả JSON | `ToolItem.tsx` | Trợ lý (và kết quả ghi ở Kho): chip có icon theo trạng thái |
| MCP Apps (`AppRenderer`, sandbox, CSP, rate limit, open-link có xác nhận) | `ToolItem.tsx`, `bridge.ts`, `App.tsx` (`openLink`) | Trợ lý, khung ứng dụng viền panel |
| Thẻ xác nhận (`confirmation_required`, `confirm_url` đúng origin xT, mật khẩu + TOTP trên trang xT) | `ToolItem.tsx`, `confirm.ts` | Thẻ kiểu hộp thoại xác nhận của mockup (icon vân tay, khối tóm tắt, nút "Mở trang xác nhận"); vẫn mở trang xT, Desk không nhận mật khẩu/OTP |
| PII "Hiện" (DPoP), tự ẩn, "Ẩn" | `PiiReveal.tsx`, `pii.ts` | Dưới chip tool, nút có icon mắt |
| Ghi chú thiếu tool của hồ sơ, sai cấu hình sandbox, lỗi kết nối MCP | `App.tsx` | Trợ lý (ghi chú) và màn chờ của từng không gian |
| Lỗi theo loại (thiếu quyền / hết phiên / cần xác nhận / lỗi), cô lập lỗi từng không gian + Thử lại, màn đang kết nối | `PortalAlert.tsx`, `PortalBoundary.tsx`, `portal-state.ts` | Mọi không gian, khối thông báo có viền màu theo loại |
| Con người: tìm nhân sự, Quản lý của tôi, Nhóm của tôi, Việc của tôi, thẻ doanh nghiệp/người dùng | `Portal.tsx` (H) | Con người · tab Nhân viên |
| Phòng ban (từ `find_people`, phân trang 5×100, ghi chú danh bạ lớn) | `DepartmentsPanel.tsx` | Con người · tab Phòng ban |
| Tài liệu theo quyền (loại, tìm, tải thêm, chi tiết) | `DocumentsPanel.tsx` | Con người · tab Tài liệu |
| Thành viên và quyền: vai trò của tôi, tiếp nhận nhân viên (HR), đề xuất cấp quyền SSO (IT), bước quy trình, thu hồi quyền, tải thêm | `MembersPanel.tsx` | Con người · tab Cấp quyền |
| Quy trình: Chờ tôi duyệt, Toàn bộ inbox, lọc theo loại yêu cầu, danh sách quy trình | `Portal.tsx` (P) | Quy trình: các nút lọc dạng tab, danh sách kết quả |
| Mở bản ghi, Làm mới bản ghi, Lịch sử, Duyệt, Từ chối + lý do, Thử lại sau khi xác nhận | `Portal.tsx` | Quy trình / Kho: Duyệt (nút xanh), Từ chối (nút đỏ viền), Lịch sử (nút phụ) |
| Tạo yêu cầu theo `describe_model` (create_record → submit_request, khóa an toàn, Gửi lại, Làm lại từ đầu, lỗi theo trường) | `RequestForm.tsx` | Quy trình · panel Tạo yêu cầu |
| Dữ liệu: loại dữ liệu, tìm bản ghi, danh mục dữ liệu, làm mới danh mục | `Portal.tsx` (D) | Dữ liệu · panel Bản ghi |
| Báo cáo `aggregate_records` (gợi ý, chỉ số, nhóm, kỳ, nguồn số liệu, bản ghi nguồn, lịch sử bản ghi) | `ReportPanel.tsx`, `report-presets.ts` | Dữ liệu · panel Báo cáo |
| Studio (iframe `/studio/?integrated=1`, cầu nối `request` riêng) + nút mở trợ lý Desk | `Portal.tsx` (I), `portal.ts` | Trí tuệ: khung Studio trong panel |
| Tầng 3: 4 nhóm ứng dụng, Đã bật/Đã tắt, quản trị thấy ứng dụng tắt + lệnh CLI bật/tắt | `Tier3.tsx`, `tier3.ts` | Ứng dụng: lưới ứng dụng theo nhóm, chip trạng thái; lệnh CLI giữ trong mục mở rộng (không có công tắc giả vì bật/tắt chỉ chạy bằng CLI) |
| Kiểu hiển thị ứng dụng (Sơ đồ 3D / Danh sách), module `/apps/<id>/` | `Tier3.tsx` | Ứng dụng · trang ứng dụng: tab kiểu hiển thị |
| Ứng dụng chung: Dữ liệu/Báo cáo, danh sách, tạo bản ghi | `GenericApp.tsx` | Ứng dụng · trang ứng dụng · Danh sách |
| Kho: danh sách theo loại (Kho, Vị trí, Hàng hóa, Phiếu, Tồn, Biến động), Thêm danh mục, Phiếu nhập/xuất, Gửi phiếu đi duyệt | `Portal.tsx` (Kho) | Ứng dụng · Kho hàng (WMS) · Sơ đồ 3D: panel Phiếu, panel Thêm danh mục, panel Phiếu nhập/xuất |
| WareTrack 3D (iframe, cầu nối chỉ đọc) + link mở tab mới | `Portal.tsx` (Kho), `portal.ts` | Ứng dụng · Kho hàng (WMS) · tab Sơ đồ 3D: panel Sơ đồ kho 3D |

### Để đợt 2 (mockup có nhưng chưa có backend thật, nên không hiển thị)

Theo quy tắc không dùng dữ liệu giả: Nhân sự AI (danh sách agent, Tuyển agent, Hồ sơ agent, Sự cố, hạn mức), Duyệt JD agent, dải số liệu thống kê, huy hiệu "Chuỗi nhật ký toàn vẹn", tab Nhật ký toàn tenant (chưa có tool đọc nhật ký chung; lịch sử từng bản ghi vẫn có), "Tôi đã gửi", số đếm trên menu và chấm đỏ trên chuông (cần gọi thêm `get_inbox` ngay khi mở Desk; fixture e2e hiện không xử lý lời gọi này), công tắc bật/tắt ứng dụng (vòng đời chỉ qua CLI có người duyệt), tab Thêm ứng dụng, Studio dạng gốc (Ứng dụng AI, Tri thức, Công cụ, Nhật ký chạy vẫn nằm trong iframe Studio), hộp thoại mật khẩu + OTP ngay trên Desk (xác nhận phải ở origin xT).

## Cấu hình

Sao chép `desk/.env.example` thành `desk/.env`. Biến bắt buộc thiếu thì `vite dev/build/preview` dừng với lỗi nêu tên biến (đã kiểm: `make desk-build` không có `.env` báo `thiếu biến cấu hình bắt buộc: DESK_MCP_URL, DESK_LLM_URL, ...`). Không có URL/model nào được ghi cứng trong mã nguồn.

- Trình duyệt chỉ gọi cùng origin: `/mcp` và `/llm`. Vite dev/preview chuyển tiếp tới `DESK_MCP_URL`/`DESK_LLM_URL`. Khi triển khai, APISIX chuyển `/mcp` và `/llm/chat/completions` tới xt-api; xt-api xác thực proxy LLM rồi mới gọi upstream cố định `XT_DESK_LLM_URL` (mặc định lấy `DESK_LLM_URL`). **Không có bearer tĩnh ở bất kỳ đâu** (cấu hình, proxy, gateway): Desk gửi `Authorization` bằng token của chính người dùng (`src/auth.ts`). Cấu hình gateway KHÔNG được gắn bearer cho `/mcp` và `/llm`; hostname sandbox không được route `/mcp`, `/llm`.
- **Đăng nhập Keycloak thật** (`src/oidc.ts`, `src/auth.ts`): OIDC auth-code + PKCE S256, client public **`xt-desk-agent`** (`DESK_OIDC_ISSUER`, `DESK_OIDC_CLIENT_ID`, `DESK_OIDC_SCOPE=openid mcp-xt`, redirect `<origin Desk>/auth/callback`). Chat Desk là AGENT thay mặt người dùng, nên MCP/LLM chỉ nhận token của client này (client public: tối đa tier masked, LLM chỉ nhận token PII, theo `decisions.md` "Tier PII của Desk chat"; tier local chỉ cho client confidential phía server). Desk KHÔNG dùng `xt-desk` (phiên người) hay `xt-desk-confirm` cho MCP; luồng xác nhận mở `confirm_url` (`/desk/confirm/{id}` do backend render, client `xt-desk-confirm` + step-up OTP) trong tab top-level mới (`target=_blank`).
- **DPoP (RFC 9449, `decisions.md` "DPoP cho Desk trước v1.0")**: mọi token request (auth-code và refresh) gửi header `DPoP` (`htm=POST`, `htu`=token endpoint). Keycloak trả `token_type: DPoP` và access token có `cnf.jkt` = thumbprint khóa của trình duyệt (khác thì đăng nhập lỗi). Khi đã gắn khóa, mọi request `/mcp` gửi `Authorization: DPoP <token>` + `DPoP` proof mới (`jti` 128 bit, `htm`, `htu` = URL tuyệt đối mà trình duyệt gọi, bỏ query/fragment, `iat`, `ath` = base64url(SHA-256(token))). `/llm/chat/completions` cũng ký proof mới theo đúng URL của request; xt-api kiểm DPoP trước khi gọi LLM và bỏ cả Authorization/DPoP khi chuyển tiếp tới model. Token chưa gắn khóa (Keycloak không bật) thì Desk dùng `Bearer` như cũ. Không có IndexedDB thì khóa chỉ sống theo trang (token cũng chỉ ở bộ nhớ). Desk không xử lý `DPoP-Nonce` (Keycloak và xT không phát nonce). Đăng xuất (DPA-03): xóa token, xóa cặp khóa khỏi IndexedDB (`KeyStore.clear`, lần sau sinh khóa mới → người dùng sau có `jkt` khác) (lượt sinh khóa đang chạy lúc đăng xuất không được ghi khóa vào kho: bộ đếm thế hệ + ghi-sau-kiểm, DPA-08) rồi RP-initiated logout tới `end_session_endpoint` (cùng origin issuer) với `id_token_hint`, `client_id`, `post_logout_redirect_uri=<origin Desk>/` (đăng ký ở `post.logout.redirect.uris` của `xt-desk-agent` trong `provision.py`). Server: `docs/identity.md` mục "DPoP".
- Token chỉ trong bộ nhớ (không localStorage/cookie; sessionStorage chỉ giữ `state/verifier/nonce` tạm qua lần chuyển hướng và bị xóa ngay khi xử lý callback). Reload trang = phải đăng nhập lại (SSO Keycloak làm bước này gần như tức thì). Làm mới bằng refresh token rotation (realm đặt `revokeRefreshToken`, `refreshTokenMaxReuse=0`), single-flight để nhiều request đồng thời không dùng lại refresh token cũ; làm mới khi còn <30s hạn. Refresh thất bại thì đăng xuất. id_token được kiểm `iss/aud/nonce`; discovery phải trùng issuer và endpoint cùng origin.
- Proxy LLM production chỉ nhận client `XT_DESK_LLM_CLIENT_ID` (mặc định `xt-desk-agent`), một tenant duy nhất và người đang hoạt động; kiểm thu hồi token như thao tác L2. Chỉ model `XT_DESK_LLM_MODEL` đã cấu hình được gọi. JSON tối đa 1 MiB, `max_tokens` tối đa 4096, tối đa 4 request đồng thời mỗi tiến trình và 420 giây tuyệt đối cho mỗi lượt (kể cả stream và trình duyệt đọc chậm). Upstream không redirect, không nhận token/cookie người dùng; lỗi upstream trả lỗi tổng quát. Profile minimal cần operator cấu hình LLM đang chạy; profile full dùng service Ollama.
- Fail closed: thiếu `DESK_OIDC_*` thì dev/build dừng với lỗi nêu tên biến; chưa đăng nhập thì Desk hiện màn hình đăng nhập và không gửi request nào tới `/mcp` hay `/llm`. Không còn chế độ không xác thực.
- Triển khai: static host phải trả `index.html` cho `/auth/callback` (SPA fallback); client `xt-desk-agent` phải có redirect URI này và `webOrigins` của Desk (CORS cho token endpoint). `deploy/keycloak/provision.py` tạo client (public, PKCE S256, `mcp-xt` là optional scope, redirect `https://xt.freepro.online/auth/callback`; `localhost:5173`/`127.0.0.1:5173` chỉ có khi provision với `XT_PROFILE=dev`, realm production không có redirect loopback).
- `DESK_XT_ORIGIN`: `confirm_url` chỉ hợp lệ khi đúng origin này + `/desk/confirm/{id}`; `ui/open-link` tới origin này mở thẳng.
- `DESK_APP_CALLS_PER_SEC`, `DESK_APP_CALL_BURST`, `DESK_APP_MAX_INFLIGHT`: rate limit tool call của mỗi instance MCP App.
- Desk cấp UUID cho `idempotency_key` khi tool khai trường này; alias model dùng chỉ có hiệu lực trong hội thoại đang xác thực. Lịch sử/chip ghi khóa thật, alias và khóa thật cùng ánh xạ để retry hoặc gọi lại sau xác nhận giữ nguyên thao tác. Đăng xuất/kết nối mới tạo kho ánh xạ mới, tránh hội thoại độc lập nhận lại kết quả ghi cũ.
- `standard` dùng 12 tool để model khám phá workspace/schema trước khi ghi; core-10 vẫn giữ nguyên 10 tool. Mặc định tối đa 10 bước, temperature 0. Build nhúng nội dung chuẩn `skills/xt-core/SKILL.md` (bỏ YAML/comment) qua Docker context `xt-skills`; hệ thống gửi skill, hướng dẫn MCP, thời gian hiện tại và danh sách tool khả dụng cho model. Kết quả tool một khối JSON xT được giải gói trước khi đưa lại model, tránh JSON lồng chuỗi. Sau mỗi bước tool, request kế tiếp nhắc lại mục Quy tắc từ cùng skill chuẩn bằng thông điệp system tin cậy; không tích lũy các bản nhắc trong lịch sử và không tự chọn/gọi tool thay model.
- `DESK_LLM_REASONING_EFFORT=none`: bắt buộc với ollama `/v1` (`think:false` bị bỏ qua, xem `spike-local-llm.md`).
- `num_ctx >= 16384` cho core-10: `/v1` của ollama không nhận `num_ctx`; đặt phía server ollama (`OLLAMA_CONTEXT_LENGTH=16384`). Lúc chạy `e2e:local-llm` ollama đang ở mặc định 4096 (`/api/ps`: `context_length: 4096`), đủ cho kịch bản 2 tool, không đủ cho core-10.
- `DESK_SANDBOX_URL` phải khác origin với Desk; Desk tự từ chối nếu trùng. Khi triển khai, phục vụ cùng thư mục `dist/` (có `sandbox.html`) trên một hostname riêng.

## Xem giá trị thật PII (nút "Hiện")

LLM chỉ nhận token `⟨MÃ.trường⟩`. Khi kết quả tool (chip trong chat) có token, `ToolItem` hiện nút **Hiện** (`src/PiiReveal.tsx`, `src/pii.ts`) kèm tooltip tiếng Việt giải thích giá trị chỉ hiển thị cho người, không gửi cho AI, tự ẩn khi tải lại/chuyển trang. Phiên gắn DPoP: Desk gọi `pii_reveal_ticket` chỉ với `{tokens}` (không `pubkey_jwk`; vé gắn `jkt` của access token), rồi `POST {DESK_XT_ORIGIN}/pii/reveal` body `{ticket}` + header `DPoP` (`htu` = URL đó, `ath` = base64url(SHA-256(vé))) ký bằng chính khóa DPoP. Phiên chưa gắn khóa (chỉ khi tắt tường minh `XT_DPOP_REQUIRED=false`; triển khai mặc định bật) dùng luồng cũ theo `research/spike-pii-lane.md` §3.7: khóa ECDSA P-256 non-extractable cho khung đó -> Desk gọi tool app-only `pii_reveal_ticket` bằng client MCP (không qua `runAgent`, không vào history) -> vé chỉ trong `_meta["xt/reveal"]` -> `POST {DESK_XT_ORIGIN}/pii/reveal` `{ticket, ts, sig}` -> render giá trị vào DOM. Giá trị chỉ ở state của component: không vào history LLM, không log, không storage; xóa khi `pagehide/popstate/hashchange/pageshow`, khi gỡ component, nút "Ẩn" hoặc sau 60 s. Desk từ chối gửi vé tới URL khác `DESK_XT_ORIGIN`. Hợp đồng và điều kiện phía server: `integration-notes.md` mục "Desk ↔ /pii/reveal". Test: `test/pii.test.ts` (vitest), `e2e/pii.spec.ts` (Playwright với `e2e/mock-pii.mjs`, project `pii`).

## Sandbox và bridge (sau audit bảo mật)

`@modelcontextprotocol/ext-apps@2.0.3` và `@mcp-ui/client@7.1.1` **không phát hành** sandbox proxy (chỉ có phía host `AppBridge`), nên `public/sandbox.html` là bản viết theo giao thức, không chép `sandbox.ts` (gói duy nhất có proxy là `@copilotkit/mcp-apps-renderer`, bị loại vì kéo nhiều dep và CSP lỏng). Chủ dự án cần chốt giữ bản này hay đợi ext-apps.

Mô hình 2 lớp iframe: Desk (origin A) > proxy `sandbox.html` (origin B, mã tin cậy) > iframe App `sandbox="allow-scripts"` (origin mờ). Điểm chính:
- CSP là thẻ meta đặt vào CHÍNH tài liệu proxy trước khi tạo iframe App; iframe `srcdoc` kế thừa policy, nên áp trước mọi script của App và không phân tích HTML không tin cậy. `frame-src 'none'` và `form-action 'none'` chặn điều hướng iframe; bộ đếm `load` bỏ iframe nếu bị nạp lại.
- Host không truyền `client` cho `AppRenderer` (vì `AppBridge.connect()` sẽ ghi đè mọi handler bằng proxy thẳng tới server). Mọi yêu cầu của App đi qua `src/bridge.ts`: `tools/call` chỉ khi tool khai `visibility` có `"app"` tường minh, ngoài denylist (`decide_request`, tool ghi...), tham số qua JSON Schema (`@cfworker/json-schema`, không sinh mã bằng `eval`/`Function`), rate limit token bucket + trần request đang chờ theo instance; `resources/read` chỉ `ui://`; list resource/prompt trả rỗng.
- Desk tự đọc resource `ui://`, lấy `_meta.ui.csp` và chỉ giữ `connectDomains` trùng đúng `DESK_XT_ORIGIN` (kênh `/pii/reveal`); domain khác, wildcard, resource/frame/base-uri bị bỏ.
- `ui/open-link`: cần user activation và tiêu điểm đang ở trong khung App; origin xT mở thẳng, origin khác hiện hộp thoại với URL đầy đủ; bị từ chối thì trả `isError`.
- Vòng 2 (Low, đã sửa): schema thiếu `additionalProperties` coi như `false` cho lời gọi từ App và `arguments` bị cap `DESK_APP_MAX_ARGS_BYTES` (mặc định 32768 byte UTF-8); thêm rate limit CHUNG theo tên tool cho mọi App (`DESK_APP_GLOBAL_CALLS_PER_SEC`/`DESK_APP_GLOBAL_BURST`) ngoài rate limit từng instance; hộp thoại open-link hiện origin (host punycode) nổi bật cùng URL chuẩn hóa một dòng, bỏ ký tự điều khiển/bidi/zero-width, cắt 300 ký tự; activation chỉ tính khi tiêu điểm nằm trong iframe của CHÍNH khung App yêu cầu; `visibility` phải là mảng chuỗi thuộc `model|app`, kiểu khác thì App bị từ chối và model không thấy tool. Test: `tests/regression-round2.test.ts`.
- Nginx gửi CSP qua HTTP header. Trang Desk giữ `frame-ancestors 'none'` và `X-Frame-Options: DENY`; riêng `/sandbox.html` cho phép ancestor đúng origin Desk cấu hình, rồi bootstrap tin cậy áp CSP theo metadata trước khi tạo App. `Referrer-Policy: strict-origin` cho bootstrap kiểm origin cha, không gửi path/query. `scripts/render-nginx.mjs` kiểm origin trước khi render template. `AppBridge` (thư viện) vẫn `console.debug` nội dung bridge.

## Lệnh

```bash
make desk-setup        # npm ci + playwright install chromium
make desk-test         # vitest + e2e Playwright headless, LLM giả + Keycloak giả
make desk-build        # tsc + vite build (cần desk/.env)
make desk-e2e-local    # e2e với ollama thật (đọc LLM từ desk/.env.example)
cd desk && npm run dev # dev server theo desk/.env
```

`make check` tự chạy `desk-test` khi `desk/node_modules` tồn tại (mất ~11s); CI có job `desk` riêng trong `.github/workflows/ci.yml`.

## Kết quả thật (2026-10-15, sau khi sửa theo audit)

`npm test` (vitest): 9 tệp, 110 test pass (gồm `tests/oidc.test.ts`: máy trạng thái đăng nhập). `npm run test:audit` (`tests/audit/`, test của người kiểm): 4/4 pass (trước khi sửa: 4/4 FAIL).

E2E headless `npm run e2e` (chromium; server MCP spike chép vào thư mục tạm cổng 19500; server MCP "App thử nghiệm" `e2e/evil_server.py` cổng 19503; LLM giả 19501; Desk 19502 và 19504; sandbox ở `127.0.0.1`, Desk ở `localhost`):

```
✓ [desk] chat: tool-call chip, MCP App inline, markdown an toàn, UI->tool qua bridge
✓ [desk] chat thường không gọi tool
✓ [desk] auth (5 test): fail closed khi chưa đăng nhập; client xt-desk-agent + PKCE S256 + scope mcp-xt, bearer tới MCP và LLM; token chỉ trong bộ nhớ, reload là đăng xuất; refresh rotation không dùng lại refresh token cũ; callback sai state bị từ chối
✓ [security] BUG-2a/2b/2c: CSP áp trước mọi script, không bị regex lừa (3 test)
✓ [security] BUG-3: App không điều hướng được iframe (form + location), bridge không chuyển tiếp sau điều hướng
✓ [security] BUG-1 + BUG-4: chỉ tool app-only, validate tham số, ui:// only, rate limit
✓ [security] BUG-5: ui/open-link cần user activation; origin ngoài xT phải qua hộp thoại có URL đầy đủ
✓ [security] BUG-7: _meta.ui.csp chỉ cho connect tới origin xT
14 passed (21.3s)
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

- Chưa lưu hội thoại bền. E9 kiểm Keycloak/MCP thật; E1 kiểm Ollama, catalog xT và thẻ duyệt thật. Kết quả cổng bàn giao được ghi riêng trong `docs/e2e.md`.
- Route `/desk/confirm/{id}` thuộc backend; Desk mở trang này để người dùng đăng nhập tươi + OTP. Thẻ `confirmation_required` có test đơn vị (`test/confirm.test.ts`, `tests/regression-bridge.test.ts`) và e2e trên server xT thật (`e2e/apps.spec.ts`, test approval-card).
- Bundle 896 kB (251 kB gzip), chưa tách chunk.
- Tool `show_approval_card`/`act_on_task` của server spike chưa có e2e riêng.
