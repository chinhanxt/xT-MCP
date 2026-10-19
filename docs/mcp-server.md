<!-- SPDX-License-Identifier: Apache-2.0 -->
# MCP server của xT

Bề mặt sản phẩm: 17 tool v0.1 qua SDK `mcp` 2.3 chính thức, Streamable HTTP (prod) và stdio (dev). Nguồn quyết định: `docs/tool-contracts-v1.md` v1.0.1 (TCV1), `docs/architecture-v2.md` v2.2 §1.3/§4/§5, `research/rea-lessons.md`, `research/spike-mcp-auth.md`, `research/spike-mcp-apps.md`, `research/spike-pii-lane.md`.

## Bố cục mã

| Module | Nội dung |
|---|---|
| `xt.contracts.tools` | `ToolContract` (dataclass), `Effects` → annotations (công thức REA), 17 contract dạng dữ liệu (`catalog.py`, `schema_parts.py`), envelope thành công/lỗi + `details` của `confirmation_required` (`envelope.py`), hồ sơ `full`/`core-10`, `loads_strict` (từ chối khóa JSON trùng), `input_issues` (Draft 2020-12), `catalog_digest` |
| `xt.contracts.prompt_catalog` | Nạp `contracts/prompts/*.json` theo `prompt.schema.json`; tool trong `steps` phải có trong catalog; argument được trích dẫn như dữ liệu |
| `xt.application.tools` | `ToolDispatcher` (pipeline chung), 17 handler, `ConfirmationService` (trang `/desk/confirm` gọi), `ToolError`, port (`ports.py`) |
| `xt.adapters.mcp` | `XtMcpServer` (lowlevel `Server` của SDK), `build_http_app` (cổng auth, PRM, `/healthz`), `serve_stdio`, đánh dấu khóa trùng ở byte thô |
| `xt.entrypoints.serve` | `xt serve --transport http|stdio`: composition root, nối `xt.adapters.identity` vào adapter MCP |

Adapter MCP không import adapter khác (contract import-linter "adapter độc lập"). Danh tính đi vào qua giao diện `HttpAuth` và callable; `IdentityHttpAuth` ở entrypoint dựng hoàn toàn từ API public của lớp danh tính (`AccessTokenVerifier`, `challenge_for`, `extract_bearer`, `build_protected_resource_metadata`, `protected_resource_metadata_path`, `RevocationGuard`).

Dùng lowlevel `Server` thay cho `MCPServer` + decorator: schema, `outputSchema`, annotations và title chiếu thẳng từ contract (không suy từ chữ ký hàm Python), và tránh lỗi thứ tự của extension Apps (tool khai sau `MCPServer(extensions=[apps])` bị bỏ qua im lặng).

## Catalog là dữ liệu, docs không trôi lệch

- `tests/unit/tools/test_contracts.py` trích khối JSON của từng tool ở TCV1 §5 và so **bằng tuyệt đối** với `ToolContract.doc_view()` (mô tả, `input_schema`, `output_schema`, effects, annotations, lỗi, IAM, `ui_resource`, ví dụ). Cũng so envelope §3.6, schema lỗi và `details` xác nhận §8, bảng mã lỗi (retryable), bảng catalog §4.1 (cột RO/D/I/OW/UI), danh sách core-10 §10 và **byte đo ở §9** (cột `in`, `compact`, `data`, `out`). Sửa schema trong code mà không sinh lại docs (hoặc ngược lại) thì test đỏ.
- Bất biến của `build.py` được giữ thành test: schema hợp lệ Draft 2020-12, ví dụ qua schema đầy đủ và bản compact, `data` mở / envelope đóng, tham số lạ (`approved: true`) bị từ chối, mọi property input có `description`, mọi object input đóng, không combinator ở root, 0 tool destructive và 0 tool openWorld ở v0.1.
- Title sinh từ name (`find_people` → `Find People`). `catalog_digest()` (sha256) cho metadata skill; test kiểm `tool_count` trong `skills/*/SKILL.md` = 17.

## Pipeline một lời gọi (TCV1 §3.5)

1. Xác thực (HTTP: `AuthGate`; stdio: token đọc lại mỗi lời gọi) và tenant (đúng một Organization; không có → `unauthenticated`, nhiều → `access_denied` `tenant_ambiguous`).
2. Tool có trong hồ sơ đang quảng bá; JSON không có khóa trùng (`invalid_request` `duplicate_key`); args qua `input_schema` (`invalid_request` `schema`).
3. **IAM + SoD trước tiên** (`AccessGuard.check`, action = `iam[0]` của contract) → `access_denied` + `explain_ref`. Không bao giờ ra link xác nhận trước bước này (storyboard S2a).
4. Cờ năng lực theo mốc: `upload_file` trả `capability_unavailable` (remediation, không giả thành công) cho tới khi bật `XT_MCP_CAPABILITIES`. `aggregate_records` đã hiện thực phía server (D-UNI-AGG-1), không còn cờ.
5. Kiểm chéo field của handler (C3: không combinator ở root): một quan hệ trong `find_people`; `query` khi không có `model_id`; `metric` xor `metrics`; `text`/`base64` theo `source`; `reason` khi reject/return, `delegate_to` khi delegate; `create_record(model task)` → `wrong_tool` + continuation `create_task`; `update_record(set.state)` → `precondition_failed` `state_field` + continuation `transition_record`.
6. Ghi: token `[PII:…]` ghi ngược → `masked_value`. Từ đây tới commit chạy trong **một** `TransactionRunner.run`: `OperationStore.claim(key)` (insert-or-get theo UNIQUE, namespace tenant + effective actor + tool) **trước** khi plan. Transaction khác đang giữ key thì chờ; quá hạn → `rate_limited` `idempotency_in_progress`. Cùng args → replay (`meta.receipt.replayed=true`), khác args → `idempotency_conflict`, đang chờ → `confirmation_required` cũ, bị từ chối/hết hạn/vô hiệu → `confirmation_declined`. Lỗi bất kỳ → rollback (nhả claim), không bao giờ thực thi hai lần.
7. `plan_*` của port: validate theo model, CAS (`revision_conflict` + `current_revision`), rule (`rule_violation` + `explain_ref`), tính `effective_level`.
8. Tự chủ: `human_only` (decide) luôn cần chính người được giao bước (khác người → `not_assigned_approver`); L3 luôn cần xác nhận (kể cả agent trần L3 và phiên người); agent với level > trần → xác nhận của người được đại diện; agent trần L0 → `access_denied` `autonomy_hard_limit`; service account không có người đại diện → `autonomy_hard_limit`. Quá `max_open_pending_per_actor` → `rate_limited`.
9. Rủi ro cao (L2/L3/human_only) gọi `RevocationGuard` (introspection) trước commit; token đã thu hồi → `unauthenticated`. Commit + receipt trong `TransactionRunner.run` (một transaction).
10. Dữ liệu port được chuẩn hóa tất định (`Decimal` nguyên → chuỗi số nguyên VND, `date` → `YYYY-MM-DD`, `datetime` có tz → RFC 3339 `Z`, `UUID` → chuỗi; `Decimal` lẻ/naive datetime/kiểu lạ → `internal_error`). Mọi kết quả thành công đi qua `PiiLane.outbound` (`meta.pii`), mọi envelope lỗi có ngữ cảnh đi qua `PiiLane.scrub_error` (message, details, `plan_summary`, continuation); kiểm lại `outputSchema` và trần kích thước (`resource_constraint`). `reject()` (trước khi có ngữ cảnh) chỉ nhận lỗi tĩnh do server dựng.
11. Actor `human` không bao giờ làm L2+ trực tiếp: luôn qua xác nhận (M6).

Ngoại lệ không lường trước → `internal_error` có `trace_id`, không lộ thông điệp nội bộ. Mã lỗi nằm ngoài `errors` của contract chỉ bị ghi cảnh báo; tập cho phép = `errors` ∪ {`unauthenticated`, `invalid_request`, `access_denied`, `capability_unavailable`, `resource_constraint`, `rate_limited`, `timeout`, `internal_error`} ∪ `confirmation_declined` khi có `confirmation_required` ∪ `idempotency_conflict` cho tool ghi.

## Xác nhận của người (C7, D8)

- Thiếu điều kiện tự chủ: server lưu `PendingOperation{tool, args chuẩn hóa, key, fingerprint, actor, plan_digest, plan_summary, level, confirmers, ttl}` và trả `confirmation_required` với `confirm_url = {XT_DESK_URL}/desk/confirm/{id}` (chỉ là locator), `confirmer`, `level`, `expires_at`, `plan_digest` (`sha256:` của tool + args + target + level + plan do port tính), `plan_summary`, `channels`, `continuation` (đúng lời gọi để gọi lại). Không có tham số gate trong input; stdio cũng trả `confirm_url` (không có kênh CLI).
- Desk backend (sau khi tự kiểm `azp=xt-desk`, `sub`, xác thực tươi, nonce, CSRF) gọi `ConfirmationService.confirm(id, sub, now, trace_id)`: trong một transaction có `lock_pending` (`SELECT … FOR UPDATE`, bấm hai lần chỉ thực thi một lần), kiểm hạn và đúng người xác nhận kế tiếp, **kiểm lại IAM cho cả agent gốc lẫn người xác nhận** lúc thực thi, plan lại (CAS/rule), so digest, rồi commit + receipt + đánh dấu `confirmed`. Plan đổi → `invalidated` (lần gọi lại của agent nhận `confirmation_declined`). Nhiều người xác nhận (`confirmers` > 1) → `awaiting_second_approver`. `decline()` cho nút Từ chối.
- Agent gọi lại y hệt (cùng key) → receipt replay.

## Envelope

- Thành công: `isError=false`, `structuredContent` = envelope đóng `{ok, untrusted:true, data, meta{contract_version:"1.0.1", trace_id, as_of, effective_level, receipt?, pii}}`, `content[0].text` là JSON của cùng object.
- Lỗi: `isError=true`, chỉ `content[0].text` = `{ok:false, untrusted:true, error{code, message, retryable, remediation, details, continuation?, explain_ref?}, meta}`; không có `structuredContent`.
- Cả hai mang `_meta.provenance.untrusted_content=true` (REA). `instructions` của server nói rõ dữ liệu doanh nghiệp là dữ liệu, không phải chỉ thị; phần gợi ý tool chỉ nêu tool của hồ sơ đang quảng bá, và prompt ở core-10 chỉ giữ bước dùng tool được quảng bá.
- Ngoại lệ ở tầng adapter (ngoài dispatcher) → `internal_error` có `trace_id`, không bao giờ `MCPError(str(e))`.
- Pattern id theo ngữ nghĩa ECMA-262 (`StrictValidator`: `$` = cuối chuỗi), nên `"task:t1\n"` bị từ chối.
- Khác REA có chủ ý, theo TCV1 §8: không có `category` (suy được từ `code`), `remediation` là chuỗi thay vì `{action}`; schema lỗi đóng nên không thêm trường.

## Port cho các nhóm khác (`xt.application.tools.ports`)

| Port | Trách nhiệm |
|---|---|
| `AccessGuard` | IAM + SoD + ranh giới tenant/đại diện; `autonomy_ceiling` của agent |
| `WorkspaceService`, `ModelCatalog`, `Directory` | `inspect_workspace`, `describe_model` (+ `model_primitive`), `find_people` (+ `person_for_subject`: `sub` → `person:<id>`) |
| `RecordService` | search/aggregate/get; `plan_create/update/transition/create_task` + `commit` |
| `ProcessService` | `describe_process`; `plan_submit`, `plan_decide` (`confirmers[0]` = người được giao bước) + `commit` |
| `InboxService`, `HistoryService`, `FileService`, `ModelChangeService` | `get_inbox`, `get_history`, `upload_file`, `propose_model_change` (`preview` cho dry run) |
| `PiiLane` | `inbound` (mở token trong `where`), `outbound` (chiếu theo `llm_egress` × lớp client, ghi egress), `scrub_error` (quét sâu envelope lỗi bằng `ErrorScrubber`: field `pii`, giá trị đã biết, bộ nhận dạng CCCD/SĐT/email; không quét `continuation`) |
| `OperationStore`, `TransactionRunner`, `IdGenerator` | `claim` (insert-or-get trước khi thực thi), `save_receipt`/`save_pending` cùng transaction, `lock_pending`; transaction rollback khi có exception; id. Chi tiết: `docs/integration-notes.md` |

Port nhận `args` đã qua schema, ném `ToolError` (có hàm tạo sẵn: `not_found`, `revision_conflict`, `rule_violation`, `precondition_failed`, `invalid_request`...). Ghi theo 2 pha: `plan_*` không ghi gì, `commit(plan)` ghi. Lớp client (`cloud`/`local`/`human`) suy từ `azp` (`XT_MCP_LOCAL_CLIENTS`), client lạ là `cloud`.

## Transport

- **HTTP:** `StreamableHTTPSessionManager(stateless=True, json_response=True)` (nhiều replica sau APISIX, không phụ thuộc buffering SSE). Route: endpoint MCP = path của `XT_MCP_RESOURCE` (mặc định `/mcp`), PRM `/.well-known/oauth-protected-resource/<path>` (công khai), `/healthz`. `AuthGate` áp cho mọi method: thiếu token → 401 `WWW-Authenticate: Bearer resource_metadata="…", scope="mcp-xt"`; token sai → 401 `error="invalid_token"`; thiếu scope → 403 `insufficient_scope`; IdP không tới được → 503; principal `human` (client trong `XT_MCP_HUMAN_CLIENTS`) → 401 (M6: Desk chat dùng client agent `xt-desk-agent` (public, tier masked; xem giá trị thật qua `/pii/reveal`); chỉ trang `/desk/confirm` là người). Có token: GET → 405; parse/kiểm khóa trùng O(n) chạy trong thread pool, không chặn event loop (stateless không có stream SSE); body lồng > 64 cấp → 400 `-32700`, batch hoặc khóa trùng ngoài `tools/call` → 400 `-32600`. Chống DNS rebinding bằng allowlist Host (host công khai của `XT_MCP_RESOURCE` + bind + `XT_MCP_ALLOWED_HOSTS`). Body POST > 1,5 MiB → 413 ở biên ASGI (1 MiB tệp inline base64 + envelope; tệp lớn đi `/files`). JSON > 20.000 phần tử hoặc > 64 cấp → 400 trước khi SDK parse (đo: `/healthz` p95 < 70 ms dưới 16 request đồng thời).
- **stdio (dev):** không có danh tính người cục bộ; mỗi `tools/call` đọc lại `XT_MCP_TOKEN_FILE` (token client-credentials của một agent) và kiểm bằng cùng verifier.
- **MCP Apps:** `_meta.ui.resourceUri` chỉ gắn khi resource `ui://` tương ứng được đăng ký (`UiResource`, mime `text/html;profile=mcp-app`); composition root gọi `register_apps(...)` (xem mục MCP Apps) nên `tools/list` không bao giờ quảng bá link hỏng.

## Cấu hình `xt serve`

| Biến / cờ | Bắt buộc | Ý nghĩa |
|---|---|---|
| `--transport http\|stdio` | có | |
| `--host`/`XT_MCP_HOST`, `--port`/`XT_MCP_PORT` | có (http) | Địa chỉ bind |
| `--profile full\|core-10` | không (`full`) | Hồ sơ catalog quảng bá |
| `XT_MCP_SERVICES` | có | `package.module:factory`, `factory(env) -> ToolServices` |
| `XT_DESK_URL` | có | URL công khai của xT Desk cho `confirm_url` |
| `XT_MCP_CAPABILITIES` | không | Bật `upload_file` (v0.2) |
| `XT_CONFIRM_CHANNELS` | không (`desk`) | `desk[,telegram]` |
| `XT_CONFIRMATION_TTL_SECONDS` | không (`600`) | 60..3600 |
| `XT_MAX_AGGREGATE_GROUPS` | không (`1000`) | 1..5000; trần số nhóm của `aggregate_records` (vượt → `resource_constraint`); `inspect_workspace.limits.max_aggregate_groups` |
| `XT_MCP_LOCAL_CLIENTS` | không | `azp` tier `local` (PII full). Chỉ client **confidential** phía server, phải có trong `XT_MCP_CONFIDENTIAL_CLIENTS` và `XT_MCP_ALLOWED_CLIENTS`. Client public (kể cả `xt-desk-agent`) tối đa masked (`docs/decisions.md`) |
| `XT_MCP_CONFIDENTIAL_CLIENTS` | không | Client confidential do vận hành xác nhận |
| `XT_MCP_DEV_LOCAL_TIER` | không | `true`: cho client public ở tier `local`, chỉ dùng trong profile dev |
| `XT_MCP_ALLOWED_HOSTS` | không | Host bổ sung được chấp nhận |
| `XT_MCP_ALLOWED_ORIGINS` | không (rỗng) | Origin trình duyệt được phép (MCP Inspector web...) |
| `XT_MCP_TOKEN_FILE` | có (stdio) | Tệp access token của agent |
| `XT_IDP_*`, `XT_MCP_RESOURCE`, `XT_MCP_ALLOWED_CLIENTS`, `XT_MCP_REQUIRED_SCOPES`, `XT_MCP_SCOPES_SUPPORTED`, `XT_IDP_INTROSPECTION` | có | Xem `docs/identity.md` |

Thiếu hoặc sai bất kỳ mục nào: in toàn bộ danh sách lỗi ra stderr và thoát mã 2, trước khi mở cổng. Prompt sai định nghĩa cũng dừng lúc khởi động.

## Hồ sơ core-10 và kích thước đo thật

| Hồ sơ | `tools/list` (B) | kèm `outputSchema` (B) | Token tool trên qwen3.5:4b |
|---|---:|---:|---:|
| `full` (17) | 37.367 | 81.251 | 8.159 |
| `core-10` | 20.182 | 44.034 | 3.920 |

Byte đo trên entry thật server gửi (có title, annotations, `_meta`, input đầy đủ kèm `default`), nên lớn hơn cột compact của TCV1 §9. Token đo bằng `prompt_eval_count` của ollama 0.30 trừ baseline (`uv run pytest -m slow tests/unit/mcp/test_token_budget.py -s --no-cov`), khớp TCV1 §10 (3.916 / 8.155) sai lệch 4 token. Client local dùng core-10 hoặc đặt `num_ctx ≥ 16384`.

## Kiểm thử

**Kiểm thử nền tảng thật 16/10/2026:** Claude và ChatGPT đọc và tạo/gửi giấy demo được; hai giấy đã qua hai cấp duyệt, `approved`, revision 4. Các bước host, trang xác nhận OTP và SDK được phân biệt ở [mcp-live-20261016.md](archive/mcp-live-20261016.md). ChatGPT chặn replay của giám đốc vì mô tả `decide_request`; `create_task` và quyền đọc người duyệt còn lỗi cần xử lý. Không coi kết quả backend là toàn bộ luồng host đã đạt.

Cả hai nền tảng gửi yêu cầu DCR dùng `client_secret_post` và yêu cầu OAuth `offline_access`, trong khi cổng DCR hiện chỉ nhận public client. Lượt thử dùng hai confidential client cấu hình sẵn, callback cố định riêng từng nền tảng, PKCE S256, consent và quyền masked cho người dùng demo. `offline_access` chỉ được thêm cho hai client test, thời hạn offline idle/max 900/1800 giây trong realm test; không mở rộng DCR hoặc scope công khai. Đây là đường kiểm thử có cấu hình riêng, chưa phải bằng chứng DCR production tương thích.

- `tests/unit/tools/test_contracts.py`: catalog ↔ docs, bất biến schema, annotations, profile, khóa trùng, prompts.
- `tests/unit/tools/test_handlers.py`: mọi tool qua pipeline với port giả (`tests/unit/tools/fakes.py`), đủ 15 mã lỗi (test cuối module kiểm điều này), vòng xác nhận đủ (pending → xác nhận/từ chối/hết hạn/vô hiệu → gọi lại).
- `tests/unit/mcp/test_mcp_http.py`: client `mcp` thật qua Streamable HTTP trong tiến trình (client tự kiểm `structuredContent` theo `outputSchema`), 401 + `WWW-Authenticate` khi gọi tool không có token, 403 thiếu scope, PRM, chặn Host lạ, core-10, verifier thật của lớp danh tính với khóa ký cục bộ.
- `tests/unit/mcp/test_mcp_stdio_and_cli.py`: stdio, `xt serve` thiếu cấu hình, smoke chạy thật `xt serve --transport http` (uvicorn) kiểm PRM + 401 qua socket.

## MCP Apps (`ui://xt/*`)

Ba view HTML/SVG/JS thuần (không thư viện UI, không tài nguyên ngoài), tiếng Việt, ở `src/xt/adapters/mcp/apps/`:

| View | Tool của model gắn view (`ToolContract.ui_resource`) | Tool app-only (`_meta.ui.visibility:["app"]`) | Hành vi |
|---|---|---|---|
| `ui://xt/orgchart` | `find_people` | `org_person_details` (cổng `PersonDetails`) | Cây tổ chức SVG từ `people[]` + `manager`; bấm/Enter trên nút gọi tool app-only lấy chức danh, đơn vị, quản lý, báo cáo trực tiếp. Email/điện thoại đi qua làn PII, hiện "đã che" |
| `ui://xt/approval-card` | `decide_request`, `submit_request` | không có | Tóm tắt `confirmation_required` (summary, người xác nhận, hạn, trạng thái) hoặc tuyến duyệt của `submit_request`. Nút "Mở trang xác nhận" chỉ gửi `ui/open-link` tới `confirm_url`; không có nút duyệt/từ chối và HTML không chứa đường gọi tool |
| `ui://xt/record-table` | `search_records` | `records_table_page` (cùng schema, chuyển nguyên cho `search_records`) | Bảng phân trang (cursor ký của port) + lọc từ khóa, mô hình, trạng thái, số dòng |

- **Một nguồn:** view và tool app-only khai ở `xt.contracts.tools.apps` (`UI_VIEWS`, `APP_TOOLS`); tool của model chỉ khai `ui_resource` trong catalog. Server tự nối: `tools/list` gắn `_meta.ui.resourceUri` khi resource đã đăng ký; tool app-only chỉ được liệt kê khi view của nó đã đăng ký và có tool của model dùng view đó (nên core-10 vẫn gọn). Tool app-only không thuộc 17 tool của model, chỉ đọc, đi qua cùng IAM + làn PII + cursor ký; mọi lời gọi tool app-only đã qua kiểm xác thực/tenant như tool thường. Lưu ý lỗi thứ tự `MCPServer(extensions=[apps])` không áp dụng vì dùng lowlevel `Server`.
- **Resource:** `resources/list` + `resources/read`, mime `text/html;profile=mcp-app`, `_meta.ui = {prefersBorder, csp:{connectDomains:[<origin xT>], resourceDomains:[], frameDomains:[], baseUriDomains:[]}}`. Origin xT lấy từ `register_apps(xt_origin=...)` (chỉ `https`, `http` cho loopback; không path/query/userinfo); cùng giá trị được nhúng vào thẻ phê duyệt để tự kiểm `confirm_url` (đúng origin, đường dẫn `/desk/confirm/{id}`, không query/hash/credentials) trước khi bật nút; không đạt thì nút bị vô hiệu.
- **Sandbox:** Desk chạy App trong iframe không `allow-forms`, nên bảng dùng nút + phím Enter, không dùng `<form>` (xem `docs/archive/audit-desk.md`). Desk chỉ cho App gọi tool có `visibility` chứa `"app"` tường minh và đúng `inputSchema`; `ui/open-link` ra ngoài origin xT phải qua hộp thoại xác nhận.
- **PII:** App chỉ nhận token/gợi ý che; không có đường lấy giá trị thật trong các view này (kênh vé `/pii/reveal` ở `spike-pii-lane.md` vẫn chỉ ở Desk).
- **Đăng ký (composition root):**

```python
from xt.adapters.mcp import XtMcpServer, register_apps

apps = register_apps(xt_origin=settings.desk_url, dispatcher=dispatcher, people=person_details_port)
server = XtMcpServer(dispatcher, resolve_caller, prompts=..., ui_resources=apps.ui_resources, app_tools=apps.app_tools)
```

- **Kiểm thử:** `tests/unit/mcp/test_mcp_apps.py` (resources/list|read, mime, CSP, HTML tự chứa và không có đường duyệt, liên kết `_meta.ui`, visibility app-only, hành vi tool, nhất quán đăng ký); `desk/e2e/apps.spec.ts` (`npx playwright test --project=apps`) chạy cả ba App trong Desk thật trên server xT thật (`desk/e2e/xt_apps_server.py`, port giả của `tests/unit/mcp/apps_support.py`): bấm nút orgchart gọi `org_person_details`, thẻ phê duyệt mở `confirm_url` đúng origin cấu hình và bị vô hiệu với origin lạ, bảng phân trang/lọc. Ảnh: `docs/img/app-*.png`.

## Còn mở

- Adapter thật cho các port (Postgres `OperationStore` cùng UoW, model engine, process, IAM bridge từ `xt.application.iam.AccessGuard`, làn PII v0.3) do các nhóm tương ứng làm.
- Trang `/desk/confirm/{id}` (Desk backend) gọi `ConfirmationService`; Telegram (v0.2); elicitation dạng URL chưa kiểm chứng trên Claude/ChatGPT.
- View `report`, `record-card`, `inbox`, `task-board`, `diff-preview`, `process` mới ở mức contract (chưa có HTML). Composition root chưa gọi `register_apps` (xem `docs/integration-notes.md`).
- Desk: `confirmInfo` đọc `structuredContent.error`, nhưng lỗi của xT không có `structuredContent` (chỉ `content[0].text`), nên thẻ `confirm-card` của Desk chưa hiện với server thật; thẻ `ui://xt/approval-card` đã tự đọc `content[0].text`.
