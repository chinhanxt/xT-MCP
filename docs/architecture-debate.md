<!-- SPDX-License-Identifier: Apache-2.0 -->
# Nhật ký debate kiến trúc: vòng 2

Ngày: 15/10/2026.
- Bên đề xuất: ARCHITECT, `architecture-v1.md`.
- Bên phản biện: CRITIC, `architecture-v1-critique.md`, gồm 30 mục.
- Kết quả: `architecture-v2.md` thay thế v1.

## Bằng chứng mới so với v1

| Nguồn | Nội dung |
|---|---|
| `research/spike-model-engine.md` (ký hiệu SME) | Chạy thật trên PG 17.11: bảng vật lý theo model + RLS + hash-chain + planner |
| `research/spike-iam.md` (SIAM) | Evaluator mini, 96/96 test, pycasbin dùng làm oracle |
| `research/spike-local-llm.md` (SLLM) | qwen3.5:4b chạy trên RTX 3050; cần tool đúng ý định |
| `research/spike-desk.md` (SDESK) | React + `@mcp-ui/client` AppRenderer, khoảng 85 dòng glue |
| `research/tool-contracts-critique.md` (TCC) | Phản biện catalog tool |

### Quyết định của chủ dự án (15/10)
- Desk **có chat + LLM local** ở v1.0.
- Tài khoản: ChatGPT Plus/Pro, connector có thể chỉ đọc. Có Gemini Pro. Claude chỉ Free, tối đa 1 custom connector.
- Repo: commit local, push sau.
- Hạ tầng công khai: Cloudflare Tunnel từ máy này.

`docs/tool-contracts-v1.md` **chưa có** lúc viết. Số tool và tên tool giao cho tài liệu đó. Kiến trúc chỉ chốt các ràng buộc catalog phải thỏa.

## Thống kê

| Phán quyết | Số mục | Mục |
|---|---|---|
| ACCEPT | 18 | 1, 3, 4, 6, 7, 9, 12, 14, 15, 18, 19, 20, 21, 23, 26, 27, 29 và phần lớn 11 |
| ACCEPT-MODIFIED | 11 | 2, 5, 8, 10, 13, 16, 17, 22, 24, 25, 30 |
| REJECT một phần | 1 | 28: Metabase trong v1.0 |

Không mục nào bị bác toàn bộ. Có 3 chỗ tôi giữ lập trường riêng, xem mục 2, 17 và 28.

---

| # | Mức | Phán quyết | Phản hồi và bằng chứng | Thay đổi trong v2 |
|---|---|---|---|---|
| 1 | BLOCKER | **ACCEPT** | Critic đúng: tôi trích sai BB. BB L53 chọn sinh DDL. Spike đã chạy và giải quyết câu hỏi: bảng vật lý `xt_data.<model>`, RLS RESTRICTIVE + PERMISSIVE, FK ghép `(tenant_id, ref)`, rename là `RENAME` thật theo id ổn định, planner SAFE/RISKY/DESTRUCTIVE/BLOCKED, apply 2 pha `NOT VALID` → `VALIDATE`/`CONCURRENTLY`, 50 model × 30 trường apply 2,3 s, 100k dòng tìm 22–139 ms (SME §0, §3–§6). Không cần thêm spike 18/10. Từ SME §8.1, để mỗi tenant có model riêng, tôi chọn namespace bảng theo tenant `xt_data.t<seq>__<model>`. Compiler chỉ phải đổi `C.table()` | D6, §6.1 |
| 2 | BLOCKER | **ACCEPT-MODIFIED** | Đồng ý F1–F5 là bắt buộc (TCC §C). **Không chốt con số 16** trong kiến trúc. Critic đề 16, TCC đề 17 (v0.1) và 22 (v1.0), còn `tool-contracts-v1.md` đang được chốt song song. Kiến trúc chốt 6 năng lực bắt buộc: luồng confirmation, transition Record, upload, aggregate, tìm xuyên model, submit đơn của mình ở L1. Kèm 3 bất biến: catalog không đổi theo dữ liệu, không Code Mode, không tool app-only cho ghi | D1, §1.3 |
| 3 | MAJOR | **ACCEPT** | SLLM L76 (Goose 10–19 s, 2/6 lượt rỗng) và L89 ("Cần xT Desk"). Chủ dự án đã chốt Desk có chat. Desk chat lên **v0.3** và là kênh demo chính. Hồ sơ tool rút gọn phía client, `reasoning_effort:"none"` (SLLM L31), đo `num_ctx` 16k | D7, §2.3, §10 |
| 4 | MAJOR | **ACCEPT** | Mâu thuẫn có thật. Bước người duyệt chạy in-process trong `xt-api`. Timer, service task và outbox chạy trong `xt-worker`. Cùng một image | D4, §6.2 |
| 5 | MAJOR | **ACCEPT-MODIFIED** | Đồng ý thử scope optional và giữ fallback denylist `azp`. Đổi thời điểm: test ngay **v0.0/v0.1**, vì OAuth đã kéo về v0.1. Phạm vi test theo tài khoản thật của chủ dự án: Claude Free (1 connector), ChatGPT Plus/Pro developer mode (chỉ đọc theo CR L10), Goose/Gemini CLI | D9, §5.1 |
| 6 | MAJOR | **ACCEPT** | CR L18 yêu cầu 401 kèm `resource_metadata`. `/mcp` và PRM chỉ đi qua APISIX ở chế độ TLS + rate-limit IP. `xt-api` tự trả 401 | D12, §5.8 |
| 7 | MAJOR | **ACCEPT** | SSPIFF L157. Compiler sinh BPMNDI bằng layout tuyến tính. **Không dùng** `bpmn-auto-layout` vì giấy phép chưa kiểm. Demo mở được trong Camunda Modeler | §6.2 |
| 8 | MINOR | **ACCEPT-MODIFIED** | Lấy evaluator `mini` + `policy_language` (SIAM §7). Spike mới có `eq/ne/le`, tôi thêm `ge/in` và test. Mọi điều kiện chỉ trên thuộc tính whitelist. Casbin làm oracle CI cho phần nó biểu diễn được (SIAM §3: casbin không lưu được condition). Thêm regression case 26–32 | D11, §5.2 |
| 9 | MINOR | **ACCEPT** | Sửa trích dẫn: SAPPS L19 + CR L22. SLLM đã có kết quả. Metabase là AGPL-3.0, giấy phép OSI hợp lệ; lý do loại là JVM, UI và RLS (xem 28) | toàn văn |
| 10 | BLOCKER | **ACCEPT-MODIFIED** | Lấy khung của critic: walking skeleton có OAuth sớm, đóng băng 28/11. Điều chỉnh theo thực tế: (a) v0.0 không cần spike D6 vì đã xong; thay bằng smoke test Claude Free qua Cloudflare Tunnel trước 18/10. (b) Kênh xác nhận v0.1 là trang `/confirm` tối giản của `xt-desk`, không dùng CLI (xem 17). (c) Lịch tính cả việc chạy >5 agent song song (CLAUDE.md), nhưng vẫn giữ thứ tự cắt | §10 |
| 11 | MAJOR | **ACCEPT** (một dòng sửa) | Cắt Coraza, Prometheus container, neo vào git; OpenBao chỉ KV + Transit; 2 người duyệt chỉ cho model change destructive và xóa PII; Alembic chỉ cho schema tĩnh (SME §7 cũng kết luận vậy); prompt/skill v0.1 rút về 3 + 2. **Sửa:** MCP Apps v0.3 là 4 app của critic cộng **orgchart**, vì code spike đã chạy (SAPPS, SDESK) và storyboard xếp P0 (F-15). Với `keycloak-config-cli`, xem 30 | §2.2, §7, §8 |
| 12 | MINOR | **ACCEPT** | Ghi 3 điểm cấu hình PostgREST: JWKS tĩnh phải reload khi xoay khóa, `jwt-role-claim-key`/role mặc định, và rủi ro view. Thêm từ SME §8.10: pre-request đặt GUC `xt.*` + `NOTIFY pgrst` sau mỗi apply | §3.3c |
| 13 | MINOR | **ACCEPT-MODIFIED** | Đồng ý "chống chối bỏ" là chữ ký, không phải ack. Ký Ed25519 trên `event_hash` lúc relay. Khóa nạp một lần từ OpenBao Transit (full) hoặc tệp secret (minimal); không gọi Transit cho từng event. `event_hash` được audit tham chiếu. Làm ở v1.0 | §2.2, §6.4 |
| 14 | MINOR | **ACCEPT** | Mỗi user thuộc đúng 1 Organization. Nhiều hơn 1 thì trả `tenant_ambiguous`. Mapper test trong v0.1 | D9 |
| 15 | — | **ACCEPT** | Critic xác nhận luận điểm kênh người. Giữ nguyên | §2.3 |
| 16 | BLOCKER | **ACCEPT-MODIFIED** | Đồng ý mối đe dọa agent điều khiển trình duyệt. Mọi confirmation dùng xác thực tươi: OIDC `max_age=0` + TOTP; L3 hoặc vượt ngưỡng thêm `prompt=login` + `acr_values`. Lưu `auth_time`/`acr`, từ chối nếu quá 120 s. `confirm_url` chỉ là locator. Thêm: hạn chế còn lại là agent dụ người đọc mã TOTP; giảm bằng việc trang chỉ hiện plan do server tính và gắn nhãn "do agent soạn"; WebAuthn tùy chọn cho L3. CIBA để roadmap | D8, §5.5 |
| 17 | MAJOR | **ACCEPT-MODIFIED (mạnh hơn critic)** | Critic đề Device Authorization Grant. **Tôi bác đề xuất này.** Device grant chỉ xác thực *ai đăng nhập vào CLI*. Trang Keycloak trên điện thoại hiện tên client, không hiện plan cụ thể. Plan lại in trên terminal mà agent điều khiển được, nên agent vẫn dụ được người. **Bỏ hẳn CLI làm kênh xác nhận.** Từ v0.1, kênh xác nhận là `/confirm` của `xt-desk` (trang server-render, khoảng 150 dòng). Stdio chỉ là transport cho dev, xác thực bằng token agent; không có danh tính người cục bộ | D8, §5.1, §5.5 |
| 18 | MAJOR | **ACCEPT** | Trigger DB `decider_human ≠ requester_human`. Ngoại lệ chỉ khi policy cho `self_approval_allowed`, và có audit. Delegation và `xt-desk-agent` không bao giờ mang decide/ký/activate; server kiểm. SIAM case 30/31 đã có test delegation và binding receipt | §5.5 |
| 19 | MAJOR | **ACCEPT** | `callback_data` mang nonce do xT ký, dùng một lần. Service account bot chỉ gọi được `confirm.telegram`. Chọn **long polling** của aiogram nên không mở webhook vào trong; nếu sau này dùng webhook thì bắt buộc `secret_token` | §5.5 |
| 20 | MAJOR | **ACCEPT** | Lớp `cloud`: `pii ≥ basic` luôn masked. Bỏ lời hứa về làn PII trong host đóng. Input từ chối giá trị đã che hoặc token hóa | §5.7 |
| 21 | MAJOR | **ACCEPT** | `jwt-aud = xt-data`; token MCP không bao giờ có aud này. View `security_invoker=true`, owner không BYPASSRLS. **Lưu ý:** SME dùng `xt_migrator` có BYPASSRLS làm chủ bảng, nên view phải do role khác sở hữu (`xt_api_owner` NOBYPASSRLS). Field `sensitive` không có trong `api.*`. Test RLS chạy qua HTTP PostgREST | §3.3c, §5.3 |
| 22 | MAJOR | **ACCEPT-MODIFIED** | Thêm `org_unit` + `position` có hiệu lực theo thời gian, và resolver whitelist. Đổi so với v1: **xT là nguồn sự thật cho tổ chức và vai trò**. Keycloak chỉ lo xác thực + MFA, không giữ nhóm nghiệp vụ, nên không có hai nguồn sự thật. GUC `xt.roles/department_ids` của SME §3.2 lấy từ bảng tổ chức của xT. Làm ở v0.2 | §5.1, §6.2 |
| 23 | MAJOR | **ACCEPT** | `on_return`, `parallel all\|any`, `trigger manual\|timer\|event` ở v0.2. Test reload cho từng dạng. VS ghi `_reorder` với song song/vòng lặp chưa chạy; vì v2 lưu state bằng `json` nên rủi ro giảm, nhưng vẫn test | §6.2 |
| 24 | MAJOR | **ACCEPT-MODIFIED** | SME đã có `ref` có FK, enum, rule DSL, `permissions` → RLS. Thêm theo mốc: **states/transitions ở v0.1** (W1 và TCC F2 cần), `sequence` ở v0.2 (số chứng từ trong storyboard), `children` + `computed` + quyền theo field ở v0.3 (SME §8.9), `views` ở v0.3. Tiêu chí phổ quát: CI chứng minh gói W5 CRM và "Kho đơn giản" chỉ cần YAML | §6.1 |
| 25 | MAJOR | **ACCEPT-MODIFIED** | Action tổng quát `record.create/update/transition` + `notify` ở v0.2. `webhook` ở v1.0, có allowlist domain theo tenant, khai L3 lúc activate, secret ở OpenBao (full) hoặc Compose secrets (minimal). Ô "serverless" của đề được trả lời bằng cách này | §2.2, §6.2 |
| 26 | MINOR | **ACCEPT** | SLLM L58–61 có bằng chứng. Thêm `views` (truy vấn đặt tên, có câu hỏi mẫu tiếng Việt), gọi qua tool tìm kiếm. Tên tham số giao cho `tool-contracts-v1.md` | §6.1 |
| 27 | MAJOR | **ACCEPT** | Tách namespace APISIX: `/auth`, `/data/v1`, `/files/v1`, `/workflow/v1`, `/rest/v1/tools`, `/mcp`. Route REST theo tài nguyên **khai trong ToolContract**, nên vẫn chỉ một nguồn. Thêm `docs/headless-demo.sh` | D12, §2.1, §4.3 |
| 28 | MINOR | **ACCEPT, trừ Metabase** | Không dùng chữ "HSM" nếu chưa có SoftHSM2. NGSI-LD làm một endpoint cho model Thiết bị (v1.0), không kịp thì bỏ khỏi slide. Lake dùng DuckDB `postgres` + `httpfs`. **REJECT Metabase ở v1.0**: muốn đúng RLS phải có role DB riêng cho từng tenant, kèm GUC đặt qua `ALTER ROLE … SET` và phạm vi role/department. Đây là thêm một đường truy cập dữ liệu phải test, đổi lại ít điểm. Để roadmap sau v1.0 | §2.4 |
| 29 | MINOR | **ACCEPT** | Repo đã có 2 commit cục bộ (`git log`: `1eab7b1`, `00c3e5d`). Chủ dự án chốt push sau. Push vẫn giữ nguyên ngày commit, nên I.1 không mất lịch sử; nhưng release `.tar.gz` phải nằm trên forge công khai trước khi nộp. Điều kiện đội thi vẫn là câu hỏi mở | §10, §11 |
| 30 | MAJOR | **ACCEPT-MODIFIED** | `procrastinate`: spike nửa ngày ở v0.0. Tiêu chí: enqueue trong cùng transaction psycopg của use case, job có `tenant_id`, worker mở tenant transaction; không đạt thì giữ bản tự viết theo SK §2. `keycloak-config-cli`: chỉ dùng nếu hỗ trợ KC 26.8; nếu không, giữ import realm JSON + `kc_setup.py` idempotent (đã chạy ở SAUTH và VS). DuckDB extension: dùng. `holidays` + `dateutil`: dùng (giấy phép `holidays` kiểm khi scaffold). Device grant: bác (xem 17). Renderer: dùng `@mcp-ui/client` `AppRenderer` (SDESK đã chạy), không tự gọi API host của ext-apps. IAM mini: dùng | §6.2, §8, §3.2 |

---

## Điểm tôi giữ lập trường riêng

1. **Không chốt "16 tool" trong kiến trúc** (mục 2). Con số là việc của hợp đồng tool, đang chốt song song. Kiến trúc chỉ chốt năng lực bắt buộc và bất biến.
2. **Bỏ CLI làm kênh xác nhận, không dùng device grant** (mục 17). Lý do: màn hình hiển thị plan phải là bề mặt agent không chạm được.
3. **Không Metabase ở v1.0** (mục 28). Lý do: RLS theo người xem đòi role DB riêng cho từng tenant.

## Vấn đề mới tôi tự phát hiện khi tích hợp bằng chứng

- **Demo rút mạng và Cloudflare Tunnel xung đột issuer.** Keycloak có một `KC_HOSTNAME` công khai. Khi rút mạng, Desk đăng nhập qua hostname đó sẽ hỏng. Cách xử lý: split-horizon DNS. Máy demo trỏ cùng hostname về `127.0.0.1` qua `/etc/hosts`, APISIX giữ sẵn chứng chỉ của hostname đó **[chưa kiểm chứng]**. Ghi ở §8.
- **PII nằm trong audit diff** (SME §8.5) mâu thuẫn với xóa dữ liệu theo Đ.25. Quyết định: field `pii=sensitive` chỉ ghi hash vào audit, không ghi giá trị.
- **COPY không chạy được khi có RLS** (SME §2). Nhập lô phải dùng `executemany`, khoảng 4.200 dòng/s.
- **Bẫy YAML 1.1** (SME §2.4). Agent gửi spec bằng JSON. Gói YAML đọc bằng loader chỉ chấp nhận `true/false` cho kiểu bool.
- **Regex trong spec**: chỉ nhận tập con biên dịch được bằng engine Rust của pydantic-core (không backreference, không lookaround) và có test vi sai với Postgres ARE (SME §8.6).

---

# Vòng 3: hòa giải với `tool-contracts-v1.md` (VERD B1, §8 E1–E12)

Ngày: 15/10/2026.
- Phán quyết của judge cho v2: **GO-WITH-CONDITIONS** (`docs/architecture-v2-verdict.md`).
- TCV1 (17 tool ở v0.1, 21 tool ở v1.0) là nguồn có thẩm quyền về bề mặt tool.
- Đã sửa `architecture-v2.md` tại chỗ thành v2.1 (changelog ở đầu tệp).

## Năm điểm lệch của B1

| # | Điểm | v2.0 | TCV1 | Chốt v2.1 | Lý do |
|---|---|---|---|---|---|
| 1 | Cơ chế xác nhận | Tham số `confirmation_ref` | Không có tham số gate. PendingOperation, trang `/desk/confirm/{id}` tự thực thi, agent gọi lại cùng key | **Theo TCV1** | Agent không giữ bằng chứng nên không giả được. Bỏ được một artifact (TCC F16) |
| 2 | Transport v0.1 | HTTP + `/confirm`, không CLI | §3.5 "Mốc": `/confirm` ở v0.1; dòng stdio dùng `xt confirm` trên TTY | **HTTP + `/desk/confirm` ở v0.1.** Stdio chỉ để dev; xác nhận vẫn qua `confirm_url` của Desk cục bộ | CRIT 17: TTY và CLI nằm trong vùng agent điều khiển được. Judge giữ D8 |
| 3 | Skill/prompt v0.1 | 2 skill + 3 prompt | 4 skill + 4 prompt | **Theo TCV1** | Nghiệm thu (4)(5) của v0.1 cần `xt-tasks`, `xt-data-modeling`, `change_model_safely` |
| 4 | Hồ sơ model nhỏ | 8 tool | core-10, đo được 3.916 token | **core-10, `num_ctx ≥ 16384`** | Có số đo thật, smoke test 22/22 chọn đúng tool. Bỏ cấu hình eval 8k |
| 5 | Độ tươi xác thực | 120 s | 5 phút | **5 phút** từ `auth_time`. Re-auth `max_age=0` + TOTP lúc mở trang. PendingOperation TTL 10 phút | 120 s quá chặt khi người duyệt cần đọc plan. Rủi ro tăng thêm nhỏ vì mỗi lần mở trang đều phải đăng nhập lại |

## Sửa TCV1 theo phán quyết của judge (giữ D8: không có kênh xác nhận CLI)

Các dòng TCV1 phải sửa. Không breaking, vì trường vốn đã nullable:
- `docs/tool-contracts-v1.md:183`, dòng "Chế độ stdio (dev/offline)". Sửa thành: "Chế độ stdio: `confirm_url` trỏ tới Desk cục bộ (`http://<host>/desk/confirm/{id}`); không có `cli_command`; xác nhận luôn qua Desk hoặc Telegram."
- `docs/tool-contracts-v1.md:3491`: bỏ "null in stdio mode (use cli_command)". `confirm_url` luôn có giá trị khi `status=pending`.
- `docs/tool-contracts-v1.md:3492`: bỏ property `cli_command` (hoặc khai `{"type":"null"}` nếu cần giữ tương thích).
- `docs/tool-contracts-v1.md:49`: bổ sung "OpenAPI 3.1" vào danh sách đầu ra (E9). CLI ghi rõ là CLI vận hành, không phải kênh xác nhận.

## E1–E12 (VERD §8): đã áp dụng toàn bộ vào v2.1

E1 không có tham số gate · E2 `details.confirmation_id` · E3 `/desk/confirm/{id}`, client `xt-desk` không bao giờ là DCR · E4 17/21 tool liệt kê theo TCV1 · E5 L1 gồm giao việc cho cấp dưới trực tiếp, L2 là ngoài tuyến hoặc `impact: high`, `human_only` tách khỏi thang L · E6 core-10, `num_ctx ≥ 16384`, bỏ eval 8k · E7 4 skill + 4 prompt · E8 `pii`/`sensitive`, `llm_egress ∈ {masked, local_only}`, `[PII:token]`, từ chối ghi token ngược · E9 giữ OpenAPI, sửa TCV1:49 · E10 cột "Khả dụng" (`upload_file`/`aggregate_records` `capability_unavailable` tới v0.2; PII ở v0.3) · E11 IAM/SoD trước xác nhận, agent tự duyệt không ra link · E12 thẻ duyệt mở `confirm_url` bằng `ui/open-link`, 2 chạm.

## Mục không chặn trong phạm vi kiến trúc (đã sửa)

- N1: sandbox lấy qua npm, không vendor.
- N2: allowlist `azp`.
- N3: introspection.
- N4: dùng `PyJWKClient` cấu hình trước.
- N5: trần cho `check_expr` + vá `call_service`.
- N6: APISIX passthrough ngay v0.1.
- N7: một module `domain/expr`.
- N8: `cyclonedx-py` + SHA256SUMS; bỏ câu "đã có".
- N9: bug tracker = Issues + template sẵn có; khuyến nghị push ở v0.1.
- N10: chặn `/auth/admin` và UI SeaweedFS.
- N12: kế hoạch cộng đồng.
- N13: đo footprint ngay v0.1.
- N14: `role(x)` ở v0.1.
- B4: namespace tenant ghi [chưa kiểm chứng]; phương án PostgREST `api_t<seq>` + `Accept-Profile`, dự phòng `/data/v1` tự sinh, chốt trước v0.2.
- B5: quy tắc `THIRD_PARTY_NOTICES` cùng commit.
- B6: §9.1 ánh xạ khoảng trống PACK vào mốc; G-P3/G-M1/G-M2/G-P8 chặn W1 v0.1; loader `pack.json` ở v0.1.
- `LICENSING.md` có ở v0.1.
- Hàng đợi: spike cả procrastinate lẫn pgqueuer.

## Câu hỏi mở còn lại cho chủ dự án

- Đội thi.
- Tên miền tunnel (B2).
- Câu hỏi số tool **đã đóng**: CLAUDE.md đã ghi 17/21.

---

# Vòng 4: bố trí đa tenant (đóng B4, G-M1, G-M2), v2.2

Nguồn: `research/spike-model-tenancy.md` (SMT), chạy thật trên PG 17 + PostgREST 16.4, so sánh 3 biến thể.

| Quyết định | Phán quyết | Bằng chứng | Sửa trong v2.2 |
|---|---|---|---|
| D6: chọn **B2**, mỗi tenant một schema `xt_t<seq>.<model>` + `api_t<seq>`, vẫn giữ `tenant_id` + RLS FORCE | **ACCEPT** (thay `t<seq>__<model>` của v2.0/2.1) | SMT §0–§1: chỉ biến thể B cho tenant có model riêng; B1 va chạm index `pk_m1`. §6: B1 restore mất view, B2 tự chứa. §1: bỏ RLS thì lỗi route sai bảng là rò dữ liệu | D6, §6.1, §6.3 |
| D5: bỏ schema-per-tenant khỏi danh sách bị loại | **ACCEPT** | SMT §0 | D5 |
| §3.3(c): view `security_invoker=false` thuộc `xt_api_owner`; `xt_web` không là thành viên `xt_app` | **ACCEPT**, tự sửa lỗi của v2 (v2 ghi `security_invoker=true`) | SMT §5.2: `xt_web` leo được `SET ROLE xt_workflow` qua membership `xt_app`; sau khi sửa thì bị `permission denied` | §3.3(c), §5.3 |
| PostgREST: `Accept-Profile` + `db-schemas` động + pre-request ràng tenant | **ACCEPT**, bỏ phương án dự phòng `/data/v1` | SMT §5: token T7 + `api_t8` → 403; thêm tenant chạy được sau khoảng 3 s | §3.3(c) |
| §6.3: trần khoảng 200 tenant × 30 model mỗi DB, pool `max_lifetime`, một transaction/tenant, rollout theo tenant + lệch phiên bản | **ACCEPT-MODIFIED**: con số 200 ghi là [suy luận], đo lại khi có tải thật | SMT §2, §3: 18.749 `pg_class`, relcache 115 MB, `out of shared memory` | §6.3 |
| G-M1: `managed_by: workflow` + quyền cột + `xt_workflow` | **ACCEPT** | SMT §8: SQL thô của `xt_app` sửa `trang_thai` → permission denied | §6.1, §5.3 |
| G-M2: nhãn `none/basic/sensitive` + HMAC có khóa trong audit | **ACCEPT**. Ánh xạ sang TCV1: `basic` ⇔ `pii:true`; `sensitive` ⇔ `pii:true, sensitive:true` | SMT §9 | §5.6, §6.1 |
| Bẫy psycopg: savepoint giữ `set_config` | **ACCEPT** | SMT §1 | §6.3 |

**Còn mở:**
- HMAC cho cả field `basic` trong audit: quyết định ở v0.3.
- "Không được hạ dưới mức nhãn của gói chuẩn": hiện mới là RISKY, chưa chặn cứng.
- Con số trần tenant mỗi DB: đo lại khi có tải thật.
- B6 còn G-P3 và G-P8, chờ báo cáo workflow-v2.
