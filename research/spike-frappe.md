# Spike: Frappe Framework có làm nền cho xT được không?

Ngày: 2026-10-15. Thư mục làm việc: `/tmp/xt-spike-frappe` (compose.yml, lib.py, t3..t9.py, *.out). Mọi output dưới đây là output thật, đã cắt bớt. Container và volume đã dọn sạch (image vẫn còn trong cache docker).

Phiên bản đã chạy: image `frappe/erpnext:latest` = **Frappe 17.0.0-dev** (không phải bản stable; stable mới nhất trên GitHub là v16.52.0, v15.123.1). Chỉ cài app `frappe`, không có ERPNext. MariaDB 11.8.9, Redis 7, Postgres 17.11.
Repo: frappe/frappe, MIT, 10.9k sao, push 2026-10-14. frappe_docker cũng MIT.

## 1. Khởi động, RAM, số container, kích thước image

Compose tự viết (`compose.yml`): db, redis-cache, redis-queue, configurator (chạy xong thoát), backend (gunicorn), scheduler, worker. Không có websocket/nginx. Thiếu 2 thành phần so với stack production đầy đủ (socketio, frontend nginx).

```
docker pull frappe/erpnext:latest        -> real 5m06s (mạng, một lần)
docker compose -p xtfrappe up -d         -> 6 container chạy + 1 configurator thoát
bench new-site xt.localhost ...          -> real 0m35.7s
Tổng "up + tạo site" (image đã có): 48s
curl -H 'Host: xt.localhost' localhost:18100/api/method/ping -> {"message":"pong"}
```
```
docker stats --no-stream (sau khi chạy ~2 phút, có vài request)
xtfrappe-backend-1     141.4MiB      xtfrappe-db-1          152.3MiB
xtfrappe-worker-1       30.0MiB      xtfrappe-scheduler-1    20.1MiB
xtfrappe-redis-queue-1  14.0MiB      xtfrappe-redis-cache-1   4.4MiB
=> tổng ~362 MiB (backend lên 222 MiB sau khi có 3 site)
```
Image: `frappe/erpnext` **4.69 GB** (đã gồm ERPNext, ta không dùng; image `frappe/frappe` thuần nhỏ hơn nhưng chưa đo), mariadb 335 MB, redis 39 MB. Site rỗng: 271 bảng, 13.5 MB trong DB.

Nhận xét: nhẹ hơn tưởng về RAM, nặng về image và số bảng. Chạy ổn trên máy 15 GB.

## 2. Database

- Code có sẵn `frappe/database/{mariadb,postgres,sqlite,duckdb}`, `psycopg2-binary` trong pyproject. Mặc định và được hỗ trợ chính thức: **MariaDB**.
- Postgres thử thật (container `postgres:17-alpine`, cùng network):
```
bench new-site pg.localhost --db-type postgres --db-host pg --db-port 5432 \
   --db-root-username postgres --db-root-password root --admin-password admin
Note: PostgreSQL support is currently in development and considered experimental.
...  real 77.8s   (MariaDB: 36-49s)
```
- Sau đó tạo DocType, insert, filter, unique qua REST trên site Postgres đều chạy: filter `amount>10M` trả đúng ER-00002; trùng unique trả `UniqueViolation ... tabExpense Request_ref_no_key`.
- Hạn chế: CLI tự ghi **experimental**. Không dùng RLS: `select count(*) from pg_policies` = **0**. Phân quyền hoàn toàn ở tầng ứng dụng, một DB user duy nhất cho site. Tôi chỉ test CRUD cơ bản trên Postgres, không test workflow/permission/version trên đó.

## 3. DocType động qua REST

Tạo bằng `POST /api/resource/DocType` (token `API_KEY:API_SECRET` của Administrator, tạo bằng `bench execute frappe.core.doctype.user.user.generate_keys`), không restart, không migrate:
```json
{"name":"Expense Request","module":"Core","custom":1,"naming_rule":"Expression (old style)","autoname":"ER-.#####","track_changes":1,
 "fields":[{"fieldname":"amount","fieldtype":"Currency","reqd":1,"non_negative":1},
  {"fieldname":"purpose","fieldtype":"Small Text","reqd":1},
  {"fieldname":"requester","fieldtype":"Link","options":"User","reqd":1},
  {"fieldname":"ref_no","fieldtype":"Data","unique":1},
  {"fieldname":"status","fieldtype":"Select","options":"Draft\nPending\nApproved\nRejected","default":"Draft"},
  {"fieldname":"reason","fieldtype":"Data","mandatory_depends_on":"eval:doc.status=='Rejected'"}], "permissions":[...]}
```
Kết quả: HTTP 200 trong chưa đến 1 giây; bảng `tabExpense Request` được tạo ngay.
```
POST /api/resource/Expense Request {amount:5000000,...}   -> 200 name ER-00001
POST ... amount 20000000 -> ER-00002 ; amount 300 -> ER-00003
GET  ...?filters=[["amount",">",10000000]]&fields=["name","amount","status"]
     -> {"data":[{"name":"ER-00002","amount":20000000.0,"status":"Draft"}]}
GET  ...?filters=[["purpose","like","%may chu%"]] -> ER-00002
GET  /api/method/frappe.client.get_count?doctype=Expense Request -> {"message":3}
```
REST CRUD/filter/search/count có sẵn, rất đầy đủ. Lưu ý: `custom:1` ghi định nghĩa vào DB, không sinh file; DocType có sẵn chuẩn REST nên "nghiệp vụ là dữ liệu" khớp tư tưởng xT.

## 4. Validation / poka-yoke

Test thật (HTTP 417 là lỗi validation):

| Ràng buộc | Cách khai báo | Kết quả thật |
|---|---|---|
| Bắt buộc | `reqd:1` | 417 `MandatoryError: purpose` (OK, server-side) |
| Duy nhất | `unique:1` | 417 `UniqueValidationError ... Duplicate entry 'A1'` (OK) |
| Không âm | `non_negative:1` | 417 `NonNegativeError` (OK) nhưng **amount=0 vẫn được chấp nhận** |
| Link tồn tại | `Link` | 417 `LinkValidationError: Could not find Requester` (OK) |
| Select hợp lệ | `options` | 417 `Status cannot be "Foo"` (OK) |
| Bắt buộc có điều kiện | `mandatory_depends_on` | **HTTP 200, KHÔNG được thực thi ở server** (chỉ client/UI) |
| min/max, amount > 0 | không có trường khai báo | **Không khai báo được** |

Cách duy nhất cho `0 < amount <= 1e9`: tạo **Server Script** (Python chạy sandbox RestrictedPython, lưu trong DB) qua REST:
```
POST /api/resource/Server Script {"script_type":"DocType Event","reference_doctype":"Expense Request",
  "doctype_event":"Before Validate","script":"if doc.amount <= 0 or doc.amount > 1000000000:\n    frappe.throw('amount phai trong (0, 1e9]')"}
-> tạo được (200). Sau khi bật `bench set-config -g server_script_enabled 1` + restart backend:
amount=0   -> 417 ValidationError: amount phai trong (0, 1e9]  (_exc_source: Server Script)
amount=2e9 -> 417 ValidationError: ...
```
Kết luận: khai báo được mandatory/unique/type/link/select/non_negative; min/max/biểu thức tùy ý thì phải có code (Server Script hoặc app Python). Server Script bị tắt mặc định (cờ toàn cục). Tôi không test trường hợp script bị gọi khi cờ tắt.

## 5. Workflow

Tạo qua REST: Role (`Expense User`, `Expense Manager`, `Director`), 4 User (A, B = Expense User, M = Manager, D = Director), Workflow State/Action Master, rồi `POST /api/resource/Workflow` với states Draft/Pending/Approved/Rejected, transitions:
Draft -Submit-> Pending (Expense User); Pending -Approve-> Approved (Manager, `condition: doc.amount <= 10000000`); Pending -Approve-> Approved (Director, `condition: doc.amount > 10000000`); Reject (Manager hoặc Director). Tạo workflow HTTP 200; Frappe tự thêm trường `workflow_state` vào DocType.
Áp dụng bằng `POST /api/method/frappe.model.workflow.apply_workflow {"doc": <json>, "action": "..."}`:
```
A tạo đơn 5M                         -> state Draft
A Approve trên Draft                  -> 417 WorkflowTransitionError "Not a valid Workflow Action"
A Submit                              -> 200 workflow_state Pending
A (không có role) Approve             -> 417 WorkflowTransitionError   <= bị chặn
A PUT workflow_state=Approved trực tiếp-> 417 (state vẫn Pending)       <= không bypass được
Manager Approve đơn 5M                -> 200 Approved
A tạo đơn 20M, Submit                 -> Pending
Manager Approve đơn 20M (condition sai)-> 417 WorkflowTransitionError   <= điều kiện amount có hiệu lực
Director Approve đơn 20M              -> 200 Approved
```
Phát hiện xấu: sau khi Approved, A vẫn `PUT purpose="tamper"` được (HTTP 200, DB đã đổi: `tamper Approved`). `allow_edit` của state **không được áp ở server** cho DocType không submittable (chỉ khóa qua `docstatus` khi `is_submittable`; tôi chưa test nhánh submittable). Cũng lưu ý: `delete` trong DocPerm mặc định = 1 nếu quên khai báo, tôi đã vô tình cho A xóa được đơn của mình rồi phải đặt `delete:0` tường minh.
Workflow của Frappe là máy trạng thái phẳng (state, action, role, condition biểu thức Python). Không có song song/subprocess/timer/SLA, không BPMN.

## 6. Phân quyền

DocPerm theo role, `if_owner:1` cho Expense User:
```
A tạo ER-00006, B tạo ER-00007
A list (fields name,purpose,owner)  -> chỉ thấy ER-00006 (của A)
B GET ER-00006                      -> 403 PermissionError
B PUT ER-00006                      -> 403 "does not have doctype access via role permission"
Manager list                        -> thấy tất cả (ER-00007, 00005..00001)
```
User Permission (cấp dòng): `POST /api/resource/User Permission {"user":"mgr@xt.test","allow":"User","for_value":"usera@xt.test","apply_to_all_doctypes":1}`
```
Manager list sau đó  -> []  (lúc đó đơn của A đã bị xóa)
Tạo lại đơn của A    -> Manager thấy duy nhất ER-00008 (requester usera)
Manager GET đơn của B -> 403
```
Cấp dòng chạy tốt ở tầng ứng dụng (cả list lẫn get). Nhưng không có RLS ở DB (đo trên cả MariaDB lẫn Postgres: 0 policy), nên mọi truy cập đi qua code Frappe; SQL trực tiếp bypass.

## 7. Audit

`track_changes:1` → doctype `Version` ghi diff từng lần sửa, kèm user:
```
usera@xt.test 08:35:06.29 {"changed":[["workflow_state","Draft","Pending"]]}
dir@xt.test   08:35:06.41 {"changed":[["workflow_state","Pending","Approved"]]}
usera@xt.test 08:35:06.45 {"changed":[["purpose","wf test","tamper"]]}
```
Có thêm `Deleted Document` lưu bản sao doc bị xóa. **Không bất biến**: admin `DELETE /api/resource/Version/<id>` → 202 và `PUT` sửa `data` → 200 (không hash chain, không append-only). Không đáp ứng yêu cầu "kiểm toán bất biến" của đề nếu không tự làm thêm.

## 8. Đa tenant (multi-site)

```
bench new-site tenant1.localhost ... -> 44.7s
bench new-site tenant2.localhost ... -> 48.8s
DB mỗi site: ~13.4 MB, 271 bảng, một database + một user MariaDB riêng; thư mục sites/<site> 252K
Host: tenant1.localhost → pong; dùng API key của site xt cho tenant1 → 401 (cô lập)
RAM sau 3 site: backend 222 MiB (từ 141), tổng thêm không đáng kể
```
Cô lập mạnh (database riêng, định tuyến theo Host) nhưng tạo site mất ~45 s qua CLI bench (chạy migrate cả 271 bảng), không phải API realtime. 1000 tenant = 1000 DB x 271 bảng: nặng về số bảng, migrate mỗi lần nâng cấp phải chạy trên từng site.

## 9. Xác thực Keycloak/JWT bearer

- Đọc `frappe/auth.py` `validate_auth()`: chỉ chấp nhận (a) API key `token key:secret`, (b) Basic, (c) `Bearer` được tra trong bảng **OAuth Bearer Token** do chính OAuth server của Frappe cấp, (d) session cookie. Không có introspection/JWKS.
- Test: `Authorization: Bearer <JWT giả issuer keycloak>` → **401 AuthenticationError**.
- Social Login Key có provider **Keycloak** (`login_via_keycloak`) → chỉ cho đăng nhập UI (tạo session), không dùng cho API bearer.
- Điểm mở rộng: hook `auth_hooks` (`validate_auth_via_hooks`, gọi mỗi request) cho phép tự viết app verify JWT của Keycloak (JWKS) rồi đặt `frappe.session.user`. Phải tự viết, tôi không thử.

## 10. MCP quanh Frappe (chỉ ghi nhận, API GitHub 2026-10-15)

- `frappe/mcp` (chính chủ, MIT, 169 sao, push 2026-05-29): app cho phép Frappe làm Streamable HTTP MCP server; README ghi "highly experimental ... breaking changes without notice". Tự viết WSGI vì SDK chính thức là async.
- `buildswithpaul/Frappe_Assistant_Core`: **AGPL-3.0**, 322 sao, push 2026-10-14. Nhắm ERPNext. Không dùng được (Apache-2.0 của xT; chỉ nên tham khảo).
- Khác: `rakeshgangwar/erpnext-mcp-server` (MIT), `Ambibuzz/ampower_jive` (MIT), `vyogotech/frappe_ai` (MIT).

## So sánh: Frappe vs "model engine tự viết trên Postgres + SpiffWorkflow"

| Tiêu chí | Frappe | Tự viết (Postgres + RLS + SpiffWorkflow) |
|---|---|---|
| Giấy phép | MIT (OK Apache-2.0) | Tự chủ |
| Time-to-value | Rất nhanh: DocType động, REST CRUD/filter/count, form UI, role, version, workflow có sẵn, đo được trong vài giờ | Phải viết tất cả: vài tuần |
| DB | MariaDB chính; Postgres **experimental** | Postgres thuần (khớp kiến trúc xT) |
| RLS cấp dòng ở DB | Không (0 policy), chỉ tầng app | Có, đề thi yêu cầu "phân quyền tới cấp dòng" |
| Validation khai báo | reqd/unique/non_negative/link/select; min/max và điều kiện cần Python (Server Script) | JSON Schema/CHECK khai báo được trọn vẹn |
| Workflow | FSM phẳng + condition + role, chạy đúng; không BPMN/song song/SLA; allow_edit không áp server (non-submittable) | SpiffWorkflow: BPMN thật, timer, song song |
| Audit bất biến | Version sửa/xóa được | Tự làm hash chain / bảng append-only |
| JWT/OIDC bearer | Không có sẵn, cần `auth_hooks` tự viết | Gateway APISIX verify JWT, truyền claim |
| Đa tenant | 1 DB/site, cô lập tốt, tạo site ~45 s | RLS theo tenant_id: tạo tenant tức thì |
| Tài nguyên | ~360 MiB RAM cho 6 container; image 4.7 GB (erpnext) | Nhẹ hơn nhiều |
| Số tool MCP | Còn phải tự dựng lớp MCP ~15 tool bên ngoài (frappe/mcp còn experimental) | Thiết kế đúng ý ngay từ đầu |
| Rủi ro | `latest` là 17.0.0-dev; lệ thuộc framework lớn, "mang theo" 271 bảng và khái niệm Desk | Rủi ro tự phát triển, lỗi tự chịu |

## Kết luận

**Không nên dùng Frappe làm lõi xT.** Lý do chính, theo bằng chứng đo được: (1) Postgres chỉ experimental và không có RLS, trong khi đề thi và kiến trúc xT đặt Postgres + RLS làm trụ; (2) JWT Keycloak không dùng được cho API nếu không viết `auth_hooks`, lệch với mô hình APISIX + SSO; (3) audit Version sửa/xóa được; (4) workflow là FSM phẳng, `allow_edit` không chặn ghi qua REST; (5) validation min/max không khai báo được.

Frappe làm tốt: mô hình DocType động qua REST, permission theo role + if_owner + User Permission, workflow với điều kiện amount theo role (đã chứng minh chạy), multi-site cô lập.

Đề xuất: giữ hướng tự viết engine trên Postgres + SpiffWorkflow, nhưng **học các mẫu thiết kế của Frappe** (DocType = metadata JSON, `if_owner`/User Permission, workflow transition có `condition` theo role, `Version` diff) và dùng Frappe như tham chiếu hành vi. Nếu thời gian OLP quá gấp, phương án B hợp lý duy nhất: dùng Frappe + MariaDB như "Data service" phụ ở một ứng dụng Tầng 2, không phải lõi; khi đó vẫn phải tự viết lớp MCP, auth hook Keycloak và audit bất biến.

## Sự cố và hạn chế của spike
- Pull image `frappe/erpnext` mất 5 phút (mạng). Dùng `-I` khiến script cục bộ không import được `lib.py`, đã chuyển sang `PYTHONPATH`.
- Lần chạy đầu của `docker compose stats` bị treo (lệnh stream), đã kill, không ảnh hưởng dữ liệu.
- Không test: Frappe `is_submittable` + docstatus, hook `auth_hooks`, workflow/permission trên Postgres, hiệu năng/tải, `frappe/frappe` image thuần, stable v16 (đã chạy 17.0.0-dev).
