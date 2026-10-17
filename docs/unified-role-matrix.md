<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->

# Ma trận quyền endpoint/tool × vai trò và provenance của hai bản nhập (G0)

Lập bằng cách **đọc mã** ngày 17/10/2026 trên `staging` (commit gốc `b952f8e`); mỗi dòng ghi `tệp:dòng`. Chưa chạy test truy cập chéo nên đây **không** phải bằng chứng nghiệm thu U03. Đường dẫn Studio tính từ `imports/dify-rea/src/`; đường dẫn Python tính từ `src/xt/`.

## 1. Vai trò và cách đọc bảng

| Cột | Vai trò thực trong mã | Nguồn |
|---|---|---|
| **SA** | `studio_admin`, `xt_admin`, `director` → quyền Studio `admin` | `xt-auth.ts:14` |
| **SE** | `studio_editor` → `editor` | `xt-auth.ts:15` |
| **SU** | `studio_user` → `viewer` | `xt-auth.ts:16` |
| **WM** | `warehouse_manager` (vai trò gói `kho-hang`) | `packages/kho-hang/pack.json:40` |
| **ST** | "staff" **không tồn tại trong mã**; tương ứng gần nhất là `employee` (vai trò nhân viên của seed/gói) | `seed/binh-minh/mapping.json:18`, `packages/kho-hang/policies/iam.json:9` |

Ký hiệu: ✔ cho phép; ✖ từ chối; ◐ có điều kiện (ghi ở cột ghi chú); `pol` = tùy policy gói đã cài (mặc định từ chối khi không có statement). Một người có thể giữ nhiều vai trò; Studio lấy vai trò cao nhất (`xt-auth.ts:11`).

Hai lớp độc lập:

1. **Studio (Node)** phân quyền theo vai trò tên `studio_*`/`xt_admin`/`director`. Người không có vai trò Studio nào bị 403 ngay khi xác minh assertion (`xt-auth.ts:81`), nên WM/ST (nếu không kèm vai trò Studio) là ✖ ở mọi dòng mục 2.
2. **Lõi xT (Python)**: bề mặt REST chặn theo **audience + client + thu hồi**, không theo tên vai trò; quyền nghiệp vụ do **IAM của tool** quyết định theo policy dữ liệu của gói (mục 4). Vai trò `studio_*` không phải subject trong policy `kho-hang`.

## 2. Studio: route Node × vai trò

Cổng chung: assertion HMAC (tenant, person, roles, method, path, body hash, `iat` ±30 s, nonce một lần) tại `xt-auth.ts:54-96`; tenant → workspace tạo/ánh xạ ở `server.ts:114-123`. Chế độ tích hợp tắt đăng nhập cục bộ, API key, public share (`server.ts:49-50,135`; `xt-auth.ts:26-29`).

| # | Endpoint | SA | SE | SU | WM / ST | Ghi chú | Nguồn |
|---|---|---|---|---|---|---|---|
| S1 | `GET /api/session` | ✔ | ✔ | ✔ | ✖ | trả người dùng + `integrated` | `xt-auth.ts:24`; `server.ts:131` |
| S2 | `GET /api/xt/tools` | ✔ | ✔ | ✖ | ✖ | cần capability của proxy; 503 nếu chưa cấu hình | `xt-auth.ts:25`; `server.ts:101-107` |
| S3 | `GET /api/models`, `GET /api/mcp` | ✔ | ✔ | ✖ | ✖ | không trả key/token (`has_key`) | `xt-auth.ts:31`; `resources.ts:16-23`; `mcp-routes.ts:6-16` |
| S4 | `POST/PUT/DELETE /api/models[/{id}]`, `POST …/test` | ✔ | ✖ | ✖ | ✖ | credential model: chỉ admin | `xt-auth.ts:32`; `resources.ts:25,53,61` |
| S5 | `POST/PUT/DELETE /api/mcp[/{id}]`, `POST …/refresh` | ✔ | ✖ | ✖ | ✖ | connector MCP: chỉ admin | `xt-auth.ts:32`; `mcp-routes.ts:19,42,55` |
| S6 | `GET /api/apps`, `GET /api/apps/{id}` | ✔ | ✔ | ◐ | ✖ | SU chỉ thấy app đã publish và `config` = bản publish | `xt-auth.ts:36`; `apps.ts:39-50,74,94-97` |
| S7 | `POST /api/apps`, `PUT/DELETE /api/apps/{id}`, `POST …/publish` | ✔ | ✔ | ✖ | ✖ | SE và SA không phân biệt | `xt-auth.ts:42`; `apps.ts:52,98,108,113` |
| S8 | `POST /api/apps/import`, `GET …/export`, `…/versions` (+restore) | ✔ | ✔ | ✖ | ✖ | export không mang credential | `xt-auth.ts:42`; `app-management.ts:16,55,72-91` |
| S9 | `POST /api/apps/{id}/run` | ✔ | ✔ | ◐ | ✖ | SU luôn chạy bản publish; run gắn `person_id` | `xt-auth.ts:37`; `apps.ts:158-161,207` |
| S10 | `GET/DELETE /api/apps/{id}/conversations[/{cid}]` | ✔ | ✔ | ◐ | ✖ | lọc theo audience `xt:<person>`: mỗi người chỉ thấy hội thoại của mình, kể cả SA/SE | `xt-auth.ts:38`; `apps.ts:38,132-156` |
| S11 | `GET /api/runs` | ✔ | ✔ | ◐ | ✖ | SU chỉ run của mình; SA/SE thấy **mọi** run trong workspace | `xt-auth.ts:39`; `apps.ts:315-333` |
| S12 | `/api/knowledge*`, `/api/documents/*` (CRUD, search, extract) | ✔ | ✔ | ✖ | ✖ | phạm vi workspace; **không có ACL theo tài liệu** | `xt-auth.ts:42`; `resources.ts:71-181` |
| S13 | `/api/apps/{id}/(keys|share)*`, `/v1/*`, `/public/*`, `/s/*`, `/api/setup|login|logout` | ✖ | ✖ | ✖ | ✖ | 404 "không hỗ trợ trong Studio tích hợp" | `xt-auth.ts:26-29`; `server.ts:49-50,135` |
| S14 | static `/studio/*` | ✔ | ✔ | ✔ | ✔ | không xác thực (chỉ HTML/JS/CSS); dữ liệu qua `/api/*` | `server.ts:77-92` |

## 3. Lõi xT: bề mặt HTTP ngoài MCP

| # | Endpoint | Cổng kiểm soát | Vai trò | Nguồn |
|---|---|---|---|---|
| H1 | `GET/POST/PUT/PATCH/DELETE /studio-api/{path}` | Bearer (+DPoP) hợp lệ; `client_id` = `XT_STUDIO_CLIENT_ID` (mặc định `xt-desk-agent`); có tenant; `Origin` = `XT_DESK_URL`; kiểm thu hồi mức L2; đường dẫn `[a-zA-Z0-9_-/]`, body JSON ≤ 8 MB, 4 request đồng thời; chỉ chuyển tới upstream cố định | Không lọc vai trò ở Python; chuyển `roles` của person xT trong assertion, Node quyết định (mục 2) | `entrypoints/studio_http.py:41-42,96,209-237,266-270`; APISIX `deploy/apisix/apisix.yaml.tmpl:79-96` |
| H2 | `POST /studio-internal/tools` | header capability tạm (420 s, tối đa 64 lời gọi) + HMAC bằng secret chung; chỉ 11 tool (mục 4); chạy qua dispatcher với `CallContext` của chính người gọi (client class `cloud`) | IAM của từng tool theo người gọi; không có `decide_request`, `transition_record`, `start_process`, `upload_file`, `create_task`, `propose_model_change`, `notify` | `entrypoints/studio_tools.py:25-38,49,62,75,88,109`; `studio_http.py:232-240`. APISIX không route đường này tới `xt-api` (chỉ `/studio-api/*` ở `:79-96`; `/*` đi vào `xt-desk`) |
| H3 | `POST /llm/chat/completions` | Bearer hợp lệ, client = Desk, có tenant, thu hồi L2, người dùng đang hoạt động | mọi người dùng đã xác thực; không phân vai trò | `entrypoints/desk_llm_http.py:189-207,231` |
| H4 | `/workflow/v1/*` (8 thao tác) | JWT `aud=xt-workflow` + scope `xt-workflow`; token MCP/data/files bị từ chối | Mỗi thao tác gọi tool tương ứng, nên áp IAM của tool: `definitions`→`inspect_workspace`/`describe_process`; `instances` POST→`describe_process`+`create_record`+`submit_request`+`start_process`; `instances/{id}`→`get_record`; `withdraw`→`transition_record`; `inbox`→`get_inbox`; `tasks/{id}/decision`→`decide_request`/`transition_record` | `adapters/http/workflow_api.py:549-665,811-817`; `entrypoints/workflow_http.py:89-117` |
| H5 | `/files/v1` (list, create, get, finalize, content, delete, presigned PUT/GET) | JWT `aud=xt-files`; presigned PUT không Bearer, chữ ký HMAC | IAM `file.create` (qua tool `upload_file`), `file.read`, `file.delete`; policy quét tệp | `adapters/http/files_api.py:9-14,588-597`; `entrypoints/files_http.py:803-826,897-915` |
| H6 | `/mcp`, PRM, `/healthz` | `/mcp` qua `AuthGate`; PRM và `/healthz` công khai (`/healthz` chỉ trả `{"status":"ok"}`, không kiểm DB) | IAM của tool (mục 4) | `adapters/mcp/http_app.py:229-257` |
| H7 | `/desk/confirm/{cid}` GET/POST, `/desk/confirm/callback` | phiên xác nhận Keycloak step-up (password/TOTP), người xác nhận phải là người được chỉ định, SoD | người có quyền duyệt theo IAM (`request.decide`/`record.approve`) | `entrypoints/composition/confirm_web.py:143-148` |
| H8 | `POST /pii/reveal` | Origin allowlist, vé một lần gắn khóa DPoP | `pii.reveal`; audit `egress` | `entrypoints/composition/pii_reveal.py:285-330` |
| H9 | `POST /telegram/webhook`, `POST /telegram/link-code` | webhook: secret header; link-code: Bearer + DPoP như MCP (`notify_http.py:48-67`) | không phải vai trò nghiệp vụ | `entrypoints/telegram_http.py:62-70`; `notify_http.py:108` |

## 4. MCP tool × vai trò (policy gói `kho-hang`)

Catalog: `contracts/tools/catalog.py` (17 tool + `start_process`), `contracts/tools/notify.py`, tool app-only `contracts/tools/apps.py`. Cột "Studio" = tool nằm trong `TOOLS` của H2 (`studio_tools.py:25-38`). Vai trò áp theo statement của `packages/kho-hang/policies/iam.json` (đã cài; trường `tenant` là mẫu, được đổi sang tenant đích lúc cài bằng `_retenant`, `entrypoints/composition/packs.py:132-145`). SA/SE/SU **không** là subject nào trong policy này, nên ✖ trừ khi người đó đồng thời giữ vai trò khác. `pol` = gói này không cấp, phụ thuộc gói khác.

| Tool (nguồn) | IAM action | Tự chủ tối đa | Studio | SA | SE/SU | WM | ST | Căn cứ policy `kho-hang` |
|---|---|---|---|---|---|---|---|---|
| `inspect_workspace` (`catalog.py:73-77`) | `workspace.read` | L0 | ✔ | ✖ | ✖ | ✔ | ✔ | `iam.json:142-164` (`wh.tools`) |
| `describe_model` (`:218-222`) | `model.read` | L0 | ✔ | ✖ | ✖ | ✔ | ✔ | `iam.json:142-164` |
| `describe_process` (`:285-289`) | `process.read` | L0 | ✔ | ✖ | ✖ | ✔ | ✔ | `iam.json:142-164` |
| `find_people` (`:417-421`) | `person.read` | L0 | ✖ | ✖ | ✖ | ✔ | ✔ | `iam.json:142-164` |
| `search_records` (`:502-506`) | `record.search` | L0 | ✔ | ✖ | ✖ | ✔ | ✔ | `iam.json:142-164` |
| `aggregate_records` (`:586-590`) | `record.aggregate` | L0 | ✔ | ✖ | ✖ | ✖ | ✖ | **không statement nào** cấp `record.aggregate`; composition còn `unavailable` (`entrypoints/composition/records.py:364-365`); hiện thực dự kiến theo D-UNI-AGG-1 (`docs/decisions.md`), khi đó gói cần thêm statement |
| `get_record` (`:720-724`) | `record.read` | L0 | ✔ | ✖ | ✖ | ✔ | ✔ | `iam.json:5-27` (`wh.read`); phiếu kho: ST/agent chỉ bản ghi của mình (`stock_request.json:110-118`) |
| `get_inbox` (`:820-824`) | `inbox.read` | L0 | ✔ | ✖ | ✖ | ✔ | ✔ | `iam.json:142-164` |
| `get_history` (`:908-912`) | `history.read` | L0 | ✔ | ✖ | ✖ | ✔ | ✔ | `iam.json:142-164` (`history.read` ở dòng 158) |
| `create_record` (`:1019-1023`) | `record.create` | L2 | ✔ | ✖ | ✖ | ◐ | ◐ | danh mục kho: chỉ WM/admin (`iam.json:28-45`); phiếu: người tạo = chủ (`:46-73`); `stock_balance`/`stock_movement`: **Deny mọi chủ thể** (`:104-121`) |
| `update_record` (`:1096-1100`) | `record.update` | L2 | ✔ | ✖ | ✖ | ◐ | ◐ | như trên |
| `transition_record` (`:1166-1170`) | `record.transition` | L3 | ✖ | ✖ | ✖ | ◐ | ◐ | chỉ phiếu của chính mình (`iam.json:46-73`) |
| `upload_file` (`:1250-1254`) | `file.create` | L1 | ✖ | ✖ | ✖ | pol | pol | gói `tep-dinh-kem` |
| `submit_request` (`:1350-1354`) | `request.submit` | L2 | ✔ | ✖ | ✖ | ◐ | ◐ | phiếu của chính mình (`iam.json:46-73`) |
| `decide_request` (`:1429-1434`) | `request.decide` | L3 | ✖ | ✖ | ✖ | ✔ | ✖ | chỉ WM (`iam.json:74-88`); agent bị Deny (`:89-103`); người tạo không tự duyệt (SoD: `application/iam/guard.py:65,159`) |
| `create_task` (`:1540-1544`) | `task.create` | L2 | ✖ | ✖ | ✖ | pol | pol | gói `giao-viec-sla` |
| `propose_model_change` (`:1736-1740`) | `model.propose` | L1 | ✖ | ✖ | ✖ | pol | pol | gói `quan-tri-mo-hinh` |
| `start_process` (`:1914-1918`) | `process.start` | L3 | ✖ | ✖ | ✖ | pol | pol | `stock_approval` khởi chạy qua `submit_request` (`pack.json:26-37`) |
| `notify` (`notify.py:10-15`) | `notify.send` | L2 | ✖ | pol | pol | pol | pol | gói `thong-bao`; cần xác nhận |
| App-only `org_person_details` (`apps.py:58-64`) | `person.read` | — | ✖ | pol | pol | pol | pol | MCP App `orgchart` |
| App-only `records_table_page` (`apps.py:86-92`) | như `search_records` | — | ✖ | ✖ | ✖ | ✔ | ✔ | `iam.json:142-164` |
| App-only `pii_reveal_ticket` (`apps.py:104-112`) | `pii.reveal` | — | ✖ | pol | pol | pol | pol | gói `kenh-pii` |

Quyền ở tầng model (RLS/CRUD theo cột `permissions` của từng model, áp thêm dưới IAM): `warehouse` (`warehouse.json:29`), `warehouse_location` (`:38`), `stock_item` (`:50`): WM và `admin` tạo/đọc/sửa toàn bộ, ST và agent chỉ đọc. `stock_balance` (`:57`), `stock_level` (`stock_level.json:80`) và `stock_movement` (`:80`): mọi vai trò người chỉ đọc; chỉ vai trò hệ thống `xt_workflow` ghi. `stock_request` (`:110`): ST và agent tạo/đọc/sửa phạm vi `own`; WM và `admin` phạm vi `all`.

Ghi kho: bước `post_stock` chỉ chạy từ quy trình `stock_approval` sau khi `warehouse_manager` duyệt, kiểm tra person đang hoạt động và có vai trò `warehouse_manager` (`entrypoints/composition/warehouse.py:22-37`; `packages/kho-hang/processes/stock_approval.json:14-33`). Người tạo phiếu không được duyệt phiếu của chính mình (SoD ở guard).

## 5. Kết quả và việc còn lại cho G0

Đã xác nhận bằng đọc mã:
- Tenant của Studio chỉ lấy từ assertion đã ký, ràng method/path/body (`xt-auth.ts:54-96`); mọi truy vấn Studio kèm `workspace` suy ra từ tenant (`server.ts:114-124,186`).
- Hội thoại tách theo từng người kể cả với SA/SE (`apps.ts:38,135,146`).
- Public share và API key bị chặn ở cả Node (`xt-auth.ts:28-29`, `server.ts:49-50`) lẫn kỳ vọng "chưa bật" trong `docs/unified-local.md`.

Khoảng trống phát hiện (chưa sửa trong PR này, cần quyết định hoặc ticket):
1. **Không có đường nào gán vai trò `studio_*`, `xt_admin`, `warehouse_manager`.** Không seed, `mapping.json` hay gói nào chứa chúng (grep trên `seed/`, `packages/`, `src/`); vai trò xT lấy từ `person.roles` (`entrypoints/composition/runtime.py:100`), nạp bằng `xt org import` (`entrypoints/composition/org_seed.py:69-76`). Theo D-UNI-H-1 (`docs/decisions.md`), gán vai trò ở H đi qua `packages/nhan-su-vong-doi` (`access_grant` + duyệt), không qua `xt org import` từ web. Hiện `access_grant` chỉ được **đếm** (`pack_ops.py:404`); chưa có application service áp bản ghi đã duyệt thành vai trò hiệu lực, nên chưa chứng minh được "thu hồi quyền có hiệu lực" (U02).
2. **"staff" không có trong mã**: dùng `employee`. Nếu yêu cầu cần vai trò mới, phải khai trong gói (không thêm ngầm).
3. **`director` (Studio admin) không là subject của policy `kho-hang`**, nên giám đốc không đọc được tồn kho qua MCP/Studio nếu không giữ thêm `employee` hoặc `admin`; `aggregate_records` không ai được cấp và còn `unavailable` (D-UNI-AGG-1 mới là quyết định, chưa hiện thực; ảnh hưởng U08, U09). Cần bổ sung statement trong gói hoặc quyết định cố ý.
4. **SA/SE xem mọi run trong workspace** (`apps.ts:323-331`), gồm input/output của người dùng khác.
5. **Tri thức/RAG không có ACL theo tài liệu**; chỉ có biên workspace (`resources.ts:71-181`). U10 ("đổi quyền tài liệu thì RAG ngừng trả") chưa có cơ chế để nghiệm thu.
6. `/healthz` không kiểm phụ thuộc (`http_app.py:251`).
7. Còn cần test truy cập chéo U03 chạy thật (hai tenant, token hết hạn/thu hồi) trước khi coi ma trận này là bằng chứng.

## 6. Provenance và giấy phép của hai bản nhập

| Hạng mục | Bằng chứng trong repo | Trạng thái |
|---|---|---|
| `imports/dify-rea` (xT Studio) — mã | `package.json` `"license": "Apache-2.0"`; README: "mã ứng dụng được viết mới; không fork mã Dify"; nguồn nội bộ `projects/reclaw/repos/dify-rea` (`imports/README.md:3`) | Đã ghi; chưa có xác nhận độc lập rằng không có mã Dify bị chép |
| `imports/dify-rea/LICENSE` | Văn bản Apache-2.0 nguyên bản; dòng bản quyền còn mẫu `Copyright [yyyy] [name of copyright owner]` (`LICENSE:190`) | **Chưa giải quyết**: chưa điền chủ sở hữu bản quyền; header SPDX từng tệp: xem PR `g0-spdx-check` |
| `imports/dify-rea` — dependency | `package.json`: `@modelcontextprotocol/sdk`, `@xyflow/react`, `ajv`, `react`, `react-dom`, `react-markdown`, `remark-gfm`; dev: `esbuild`, `prettier`, `typescript`, `@types/*` | `react`, `react-dom`, `ajv`, `@modelcontextprotocol/sdk` đã có trong `THIRD_PARTY_NOTICES.md` (dòng 101, 106, 108); `@xyflow/react`, `react-markdown`, `remark-gfm`, `esbuild`, `prettier`, `typescript` **chưa kiểm giấy phép** (cần `package-lock.json` + tra cứu mạng, ngoài phạm vi môi trường này) |
| `imports/dify-rea/docs/evidence/*` | README của thư mục: ảnh chụp từ browser capture local, chỉ dữ liệu QA; có tham chiếu Dify Cloud | Cần rà trước khi phát hành gói nguồn (ảnh chụp giao diện bên thứ ba) |
| `imports/waretrack-3d` — giấy phép | D-UNI-WT-1 (`docs/decisions.md`): do thành viên team NguyenThong251 chia sẻ, phát hành Apache-2.0 theo dự án; upstream `https://github.com/NguyenThong251/waretrack-3d` không có LICENSE (`imports/README.md`, mục provenance) | **Đã giải quyết** bằng quyết định chủ dự án; tệp nguồn giữ nguyên, phủ giấy phép bằng chú thích REUSE (PR `g0-spdx-check`) |
| `imports/waretrack-3d` — dependency | `package.json`: `three ^0.186.1` (MIT), `vite ^8.3.2` (MIT) | `vite` đã có `THIRD_PARTY_NOTICES.md:102`; `three` **chưa có dòng** trong `THIRD_PARTY_NOTICES.md` |
| `imports/waretrack-3d/index.html:8-10` | tải font Inter từ `fonts.googleapis.com`/`fonts.gstatic.com` lúc chạy | Phụ thuộc mạng ngoài và lộ IP người dùng; Inter dùng giấy phép OFL nhưng chưa có trong THIRD_PARTY_NOTICES. Cần quyết định tự lưu font hay giữ |
| `imports/waretrack-3d/docs/ref/*.jpg`, `docs/ref/ui/*.png` | khung hình/ảnh cắt từ `reference_video.mp4` (`docs/SPEC.md:3-4`) — video không có trong repo, không ghi chủ sở hữu | **Chưa giải quyết**: nguồn và quyền dùng của video tham chiếu chưa rõ; D-UNI-WT-1 không nói đến tài sản này. Nên loại khỏi gói phát hành hoặc xác nhận với tác giả |
