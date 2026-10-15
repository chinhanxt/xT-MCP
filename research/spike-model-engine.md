# Spike: MODEL ENGINE — model nghiệp vụ là dữ liệu, agent soạn, người duyệt, áp an toàn vào Postgres đa tenant

Ngày chạy: 2026-10-15. Môi trường: `/tmp/xt-spike-model` (uv venv, Python 3.13), Docker **PostgreSQL 17.11** cổng 15434. Toàn bộ số liệu và output dưới đây là **output thật** của các script trong `research/spike-model-engine/tests/` (bản đầy đủ trong `spike-model-engine/outputs/`). Container đã xóa sau khi chạy.

Phụ thuộc (đều OSI): psycopg 3.3.6 (LGPL-3.0, chỉ import), psycopg-pool 3.3.3, pydantic 2.14.1 (MIT), PyYAML 6.0.3 (MIT). Chỉ dùng để đánh giá: SQLAlchemy 2.1.4 (MIT), Alembic 1.20.0 (MIT), sqlglot 30.23.0 (MIT).

---

## 0. Kết luận nhanh

| Yêu cầu | Kết quả |
|---|---|
| Spec model (YAML/JSON, validate) | **Đạt.** Pydantic v2 là nguồn sự thật; xuất JSON Schema (9,6 KB) dùng làm `inputSchema` cho tool `propose_model_change`. 11 kiểu trường, ràng buộc, rule chéo trường bằng DSL khai báo, index, quyền role→action+scope, nhãn tiếng Việt. |
| Compiler → DDL + RLS + audit | **Đạt.** Bảng dùng chung `xt_data.<model>` + `tenant_id` + RLS (`RESTRICTIVE` cho tenant, `PERMISSIVE` theo role/scope), FK ghép `(tenant_id, ref)` chặn tham chiếu chéo tenant, `revision` cho OCC, trigger audit băm chuỗi append-only. Đã kiểm bằng dữ liệu quan sát được, không chỉ `pg_policies`. |
| Change planning | **Đạt.** Diff theo **ID ổn định** → SAFE/RISKY/DESTRUCTIVE/BLOCKED, đếm bản ghi vi phạm + mẫu id, diễn tập DDL rồi rollback, rename là `RENAME` thật, xác nhận lần 2 theo mã thao tác, mã kế hoạch chống TOCTOU, `lock_timeout`, bảng phiên bản. |
| An toàn định danh | **Đạt.** 18/18 tên độc hại bị từ chối ở cả tên model và tên trường. Giá trị độc hại (enum, nhãn, default, regex) được quote thành literal. Cố tình bỏ qua validator thì `sql.Identifier` vẫn quote đúng (sqlglot đếm được 1 câu lệnh). |
| CRUD tổng quát | **Đạt.** Pydantic sinh lúc chạy (`extra=forbid`), lỗi tiếng Việt kèm field/label/gợi ý "có phải ý bạn là…", ánh xạ lỗi DB qua tên constraint ổn định, filter DSL dùng bind param, phân trang có `total`. |
| Hiệu năng | 50 model × 30 trường: validate 22 ms, compile 2.503 câu DDL trong 62 ms, apply trong 1 transaction 2,3 s. 100k dòng có RLS bật: tìm kiếm (count + 1 trang) 22–139 ms, sau khi thêm **gợi ý phạm vi** (§5). |
| Alembic/SQLAlchemy | **Không dùng làm lõi migration.** Chỉ hữu ích để **phát hiện drift** (`compare_metadata`). Lý do có số liệu ở §6. |

Phát hiện quan trọng khi spike (chưa có trong các tài liệu trước):
1. **Python `re` bị ReDoS:** `(a+)+$` trên chuỗi 25 ký tự mất 0,92 s. Engine Rust của pydantic-core chạy 5.001 ký tự trong 0,17 ms. Vì vậy regex trong spec bắt buộc phải biên dịch được bằng engine Rust, tức là cấm backreference/lookaround.
2. **`COPY FROM` không chạy được khi bảng có RLS** (`FeatureNotSupported`). Import hàng loạt phải đi `INSERT … executemany`, đạt khoảng 4.200 dòng/s khi có cả audit, hoặc đi một đường import riêng có đặc quyền.
3. **Policy OR nhiều role làm planner không dùng được index owner/department.** Staff "own" tìm kiếm mất 216 ms. Lặp lại điều kiện scope thành tham số trong WHERE (RLS vẫn là chốt chặn) đưa xuống 25 ms.
4. **YAML 1.1:** `value: no` bị đọc thành `False`, nên agent phải gửi spec bằng JSON. YAML chỉ dùng để hiển thị cho người.

---

## 1. Cách chạy lại

```bash
cd research/spike-model-engine
uv venv && uv pip install -r requirements.txt
docker run -d --name xt-spike-model-pg -e POSTGRES_PASSWORD=owner -e POSTGRES_USER=xt_owner -e POSTGRES_DB=xt -p 15434:5432 postgres:17
./reset.sh   # sửa đường dẫn bootstrap.sql trong reset.sh nếu không chạy ở /tmp/xt-spike-model
for t in t1_compile t2_e2e t3_migrate t4_attacks t6_perf t6b_explain t5_alembic_eval t7_audit_concurrency; do .venv/bin/python tests/$t.py; done
docker rm -f xt-spike-model-pg
```

Thứ tự có ý nghĩa: t3 dùng dữ liệu do t2 tạo, t6 dùng catalog v3 do t3 tạo.

## 2. Cấu trúc mã

| File | Dòng | Vai trò |
|---|---|---|
| `xtmodel/spec.py` | 340 | Spec pydantic + DSL `Expr` (Cmp/And/Or/Not) + kiểm kiểu biểu thức + danh sách 164 từ khóa PG |
| `xtmodel/compiler.py` | 271 | Spec → `psycopg.sql.Composed` (bảng, CHECK, FK, index, trigger, RLS, comment), bản đồ constraint → thông điệp |
| `xtmodel/planner.py` | 447 | Diff, phân loại, đếm vi phạm, diễn tập, render tiếng Việt/JSON, apply 2 pha |
| `xtmodel/crud.py` | 409 | Engine CRUD: create/update/delete/search, pydantic động, dịch lỗi |
| `xtmodel/bootstrap.sql` | 148 | Vai trò, schema, hàm ngữ cảnh, trigger `row_touch`, bảng audit hash-chain, `verify()` |
| `tests/t1…t7` | — | Kịch bản chạy thật (§3–§7) |

---

## 3. Thiết kế

### 3.1 Định dạng spec

Ví dụ trích từ `examples.yaml` (agent gửi JSON tương đương):

```yaml
- id: m2                      # ID ổn định do engine/agent cấp, không đổi khi rename
  name: de_xuat_tam_ung       # định danh SQL: ^[a-z][a-z0-9_]{0,39}$, không từ khóa, không "__"
  label: Đề xuất tạm ứng      # tiếng Việt tự do (<=120, không ký tự điều khiển)
  fields:
  - {id: f2, name: so_tien, type: money, label: Số tiền (VND), required: true, min: 100000, max: 500000000}
  - {id: f3, name: tu_ngay, type: date, label: Từ ngày, required: true}
  - {id: f4, name: den_ngay, type: date, label: Đến ngày, required: true}
  - {id: f5, name: khach_hang, type: ref, ref: khach_hang, label: Khách hàng}
  - {id: f6, name: trang_thai, type: enum, default: nhap, required: true, options: [{value: nhap, label: Nháp}, ...]}
  indexes: [{id: i1, fields: [trang_thai, tu_ngay]}]
  rules:
  - id: r1
    message: "'Đến ngày' phải sau hoặc bằng 'Từ ngày'"
    check: {field: den_ngay, op: ">=", other_field: tu_ngay}
  - id: r2
    message: Đề xuất trên 50 triệu phải có người duyệt khi đã duyệt
    check: {or: [{field: so_tien, op: "<=", value: 50000000},
                 {not: {field: trang_thai, op: "=", value: da_duyet}},
                 {field: nguoi_duyet, op: not_null}]}
  permissions:
    staff:      {actions: [create, read, update], scope: own}
    manager:    {actions: [read, update], scope: department}
    accountant: {actions: [read], scope: all}
```

Ánh xạ kiểu: `text→text`, `number→numeric`, `money→bigint` (VND nguyên đồng), `date→date`, `datetime→timestamptz` (bắt buộc có múi giờ), `bool→boolean`, `enum→text + CHECK IN`, `ref→uuid + FK ghép (tenant_id, col)`, `user/file→uuid`, `json→jsonb + CHECK ≤ 64 KB`.

**DSL rule/filter** dùng chung một AST. Toán tử của rule: `= != < <= > >= is_null not_null in`. Filter có thêm `contains startswith`. Giới hạn: lồng tối đa 8 cấp, `in` tối đa 100 phần tử, mỗi nhóm and/or tối đa 50 nhánh. Mọi tham chiếu trường và kiểu literal được kiểm khi validate spec. Rule được biên dịch thành CHECK. Ở app, rule được đánh giá bằng **logic 3 trị giống SQL** (CHECK chỉ vi phạm khi kết quả là FALSE), để báo lỗi sớm và khớp hệt với DB.

### 3.2 Mô hình vai trò Postgres (học từ sklite §4: RLS chưa bao giờ thực sự chạy vì dùng superuser)

| Vai trò | Thuộc tính | Dùng cho |
|---|---|---|
| `xt_owner` | superuser | chỉ chạy bootstrap |
| `xt_migrator` | NOSUPERUSER, **BYPASSRLS**, chủ `xt_data`/`xt_meta` | chạy DDL đã duyệt, đếm vi phạm xuyên tenant |
| `xt_app` | NOSUPERUSER, NOBYPASSRLS, không sở hữu bảng | MCP/CRUD runtime |
| `xt_audit_owner` | NOLOGIN | chủ bảng audit + hàm `SECURITY DEFINER` ghi audit |

Ngữ cảnh được đặt **mỗi transaction** bằng `set_config(k, v, true)`, gồm `xt.tenant_id`, `xt.actor_id`, `xt.roles`, `xt.team_ids`, `xt.department_ids`. Các hàm `xt.tenant()` và `xt.has_role()` là SQL STABLE (inline được) và dùng `nullif(...,'')`. Nếu không đặt ngữ cảnh thì giá trị là NULL và kết quả là 0 dòng (fail-closed). Tên role lấy từ token phải khớp regex định danh, để không ai chèn được `','` vào GUC.

### 3.3 DDL sinh ra (trích output thật t1)

```sql
CREATE TABLE "xt_data"."de_xuat_tam_ung" (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL DEFAULT xt.tenant(),
  owner_id uuid DEFAULT xt.actor(), team_id uuid, department_id uuid,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(), updated_at ..., created_by uuid, updated_by uuid,
  revision integer NOT NULL DEFAULT 1,
  "noi_dung" text NOT NULL, "so_tien" bigint NOT NULL, ... "trang_thai" text DEFAULT 'nhap' NOT NULL, ...
  CONSTRAINT "pk_m2" PRIMARY KEY (id),
  CONSTRAINT "uq_m2_tid" UNIQUE (tenant_id, id),
  CONSTRAINT "ck_m2_f2_min" CHECK ("so_tien" >= 100000),
  CONSTRAINT "ck_m2_f6_enum" CHECK ("trang_thai" IN ('nhap', 'cho_duyet', 'da_duyet', 'tu_choi')),
  CONSTRAINT "ck_m2_f11_json" CHECK (octet_length("chi_tiet"::text) <= 65536),
  CONSTRAINT "ck_m2_r_r1" CHECK (("den_ngay" >= "tu_ngay")),
  CONSTRAINT "ck_m2_r_r2" CHECK ((("so_tien" <= 50000000) OR (NOT ("trang_thai" = 'da_duyet')) OR ("nguoi_duyet" IS NOT NULL)))
)
CREATE POLICY "p_m2_tenant" ON "xt_data"."de_xuat_tam_ung" AS RESTRICTIVE FOR ALL TO "xt_app"
  USING (tenant_id = xt.tenant()) WITH CHECK (tenant_id = xt.tenant())
CREATE POLICY "p_m2_read" ON ... AS PERMISSIVE FOR SELECT TO "xt_app" USING ((xt.has_role('accountant') AND true)
  OR (xt.has_role('manager') AND (owner_id = xt.actor() OR xt.in_departments(department_id)))
  OR (xt.has_role('staff') AND owner_id = xt.actor()))
CREATE POLICY "p_m2_create" ... FOR INSERT ... WITH CHECK ((xt.has_role('staff') AND owner_id = xt.actor()))
```

Ngoài các câu trên, compiler còn sinh: FK `fk_m2_f5 (tenant_id, khach_hang) → khach_hang(tenant_id, id)`, các index `(tenant_id, owner_id|department_id|team_id|created_at DESC)`, trigger `xt_touch` (tăng `revision`, khóa `tenant_id/id/created_at`), trigger `xt_audit`, `ENABLE` và `FORCE ROW LEVEL SECURITY`, cùng `COMMENT` theo nhãn tiếng Việt.

**Tên constraint/index/policy suy ra từ ID, không từ tên** (`ck_<model_id>_<field_id>_<kind>`, tối đa 63 byte). Nhờ vậy rename không làm vỡ ánh xạ lỗi, và lỗi DB tra được về trường/nhãn/thông điệp qua `constraint_map()`.

### 3.4 Audit hash-chain

`xt_audit.event(seq, tenant_id, actor_id, at, model, record_id, op, diff, prev_hash, hash)` với `hash = sha256(prev_hash ‖ canon(...))`, trong đó `canon` gồm timestamp UTC cố định định dạng và `jsonb::text`, vốn đã chuẩn hóa. UPDATE chỉ lưu các khóa thay đổi (`{"old":{"trang_thai":"nhap"},"new":{"trang_thai":"cho_duyet"}}`). Mỗi tenant có một chuỗi riêng, được tuần tự hóa bằng `pg_advisory_xact_lock(hash(tenant))`. Kiểm chứng toàn chuỗi bằng một câu SQL: `xt_audit.verify(tenant) → (n, first_bad_seq)`.

### 3.5 Planner và apply

- **Đối chiếu theo ID.** Model, trường, index và rule đều có `id`. Cùng id mà khác `name` thì sinh `RENAME`. id biến mất là `DROP`, id mới là `ADD`.
- **Phân loại.** SAFE gồm thêm cột trống được, thêm model, thêm/xóa index CONCURRENTLY, nới min/max, enum mở rộng, đổi nhãn, thu hẹp quyền. RISKY gồm thêm cột bắt buộc có default, siết ràng buộc khi có 0 vi phạm, mở rộng quyền. DESTRUCTIVE gồm drop/rename trường hoặc model. BLOCKED gồm vi phạm > 0, cột bắt buộc không default trên bảng có dữ liệu, đổi kiểu, hoán đổi tên, hoặc diễn tập DDL lỗi.
- **Dry-run** gồm (a) đếm vi phạm trên dữ liệu **hiện tại**, có resolver ánh xạ trường mới về cột cũ hoặc default; (b) `rehearse=True` chạy toàn bộ bước `tx` trong transaction `force_rollback`, mỗi thao tác một savepoint, và ghi lỗi gốc của Postgres.
- **Apply 2 pha.**
  - Pha 1 là một transaction: `pg_advisory_xact_lock('xt_meta:catalog')`, `SET LOCAL lock_timeout`/`statement_timeout`. Kế hoạch được **lập lại** trên dữ liệu hiện tại; `plan_hash` phải khớp mã người đã duyệt; mọi mã DESTRUCTIVE phải có trong `confirm`. Sau đó chạy DDL và ghi `xt_meta.catalog_version` (spec, plan, người đề xuất/duyệt).
  - Pha 2 chạy sau commit: `VALIDATE CONSTRAINT` (mỗi câu một transaction ngắn, chỉ giữ khóa SHARE UPDATE EXCLUSIVE) và `CREATE/DROP INDEX CONCURRENTLY`. Ràng buộc mới được thêm ở pha 1 dưới dạng `NOT VALID`, nên ghi mới bị kiểm ngay mà không phải quét bảng dưới ACCESS EXCLUSIVE.
  - Đặt NOT NULL cho cột đã có thì đi theo chuỗi: `CHECK (col IS NOT NULL) NOT VALID` → VALIDATE → `SET NOT NULL` (PG12+ bỏ qua bước quét) → drop CHECK.

---

## 4. Kết quả chạy thật

### 4.1 CRUD + poka-yoke (t2, rút gọn)

```
--- trùng mã trong tenant A
{"ok": false, "error": {"code": "DUPLICATE", "message": "'Mã khách hàng' đã tồn tại, phải là duy nhất", "fields": [{"field": "ma_kh", "label": "Mã khách hàng", ...}]}}
--- sai định dạng + thiếu trường + enum sai
"'Mã khách hàng' không đúng định dạng (mẫu ^KH[0-9]{4}$)" | "'Tên khách hàng' là trường bắt buộc, chưa có giá trị" | "'Hạng' chỉ nhận một trong: thuong (Thường), vang (Vàng)"
--- gõ sai tên trường
"Trường 'so_tien_vnd' không tồn tại trong 'Đề xuất tạm ứng' — có phải ý bạn là 'so_tien'?", "valid_fields": [...]
--- đến ngày < từ ngày (rule DSL, kiểm ở app)
{"code": "RULE_VIOLATION", "message": "'Đến ngày' phải sau hoặc bằng 'Từ ngày'", "fields": [den_ngay, tu_ngay]}
--- tiền lẻ + ngày sai
"'Số tiền (VND)' phải là số nguyên (VND không có phần lẻ)" | "'Từ ngày' phải là ngày dạng YYYY-MM-DD"
--- ref sang khách hàng của tenant B
{"code": "BAD_REFERENCE", "message": "'Khách hàng' tham chiếu tới bản ghi không tồn tại hoặc không thuộc đơn vị của bạn"}
--- kế toán (chỉ read) thử tạo
{"code": "FORBIDDEN", "message": "Vai trò ['accountant'] không có quyền 'create' trên 'Đề xuất tạm ứng'", "hint": "Vai trò được phép: ['staff']"}
--- staff1 tạo hộ người khác (owner_id lạ) -> RLS WITH CHECK
{"code": "FORBIDDEN", "message": "Bạn không có quyền create bản ghi này trên 'Đề xuất tạm ứng' (ngoài phạm vi được cấp)"}
--- staff2 cập nhật cùng rev1 (đã cũ)
{"code": "CONFLICT", "message": "Bản ghi đã bị sửa bởi người khác (revision hiện tại 2, bạn gửi 1)", "hint": "Đọc lại bản ghi, hỏi người dùng ..."}
--- duyệt 80tr thiếu người duyệt (CHECK r2 ở DB)
{"code": "CONSTRAINT_VIOLATION", "message": "Đề xuất trên 50 triệu phải có người duyệt khi đã duyệt", "fields": [nguoi_duyet, so_tien, trang_thai]}
--- gán null cho trường bắt buộc
"'Nội dung' không được để trống (trường bắt buộc)"
--- xóa khách hàng đang được tham chiếu
{"code": "IN_USE", "message": "Không xóa được 'Khách hàng': đang được 'Khách hàng' của 'Đề xuất tạm ứng' tham chiếu"}
--- model không tồn tại
{"code": "UNKNOWN_MODEL", "message": "Không có model 'khach_hangg' — có phải 'khach_hang'?"}
```

### 4.2 Cô lập tenant và phạm vi dòng (t2). Pool chỉ có 1 kết nối, ép dùng chung giữa tenant A/B.

```
staff1(own)                  total=1 owners=['a1']
staff2(own)                  total=1 owners=['a2']
mgrA(department D1)          total=2 owners=['a1', 'a2']
acct(all trong tenant)       total=3 owners=['a1', 'a2', 'a9']
staffB(tenant B)             total=1 owners=['b1']
mgrB tìm KH0001 -> chỉ thấy bản của B: {"total": 1, "la_ban_ghi_cua_B": true}   (KH0001 cũng tồn tại ở A)
tenant B sửa bản ghi tenant A theo id -> NOT_FOUND
staff2 sửa đề xuất của staff1       -> NOT_FOUND (vô hình với staff2)
xt_app không đặt tenant -> thấy 0 dòng; current_setting sau commit = ''
SQL thô chèn tenant_id=B khi ngữ cảnh là A -> InsufficientPrivilege new row violates row-level security policy "p_m2_tenant"
xt_app tắt RLS -> InsufficientPrivilege must be owner of table de_xuat_tam_ung
```

Số liệu phạm vi này đo được ở cả lần chạy **trước** khi thêm gợi ý phạm vi ở tầng app (`out_t6_perf_before` chạy cùng t2 cũ). Nghĩa là RLS tự đứng vững; gợi ý chỉ là tối ưu.

### 4.3 Audit (t2, t7)

```
xt_app verify tenant A: (31, None) | thấy event tenant B: 0
ví dụ event UPDATE: 7 UPDATE de_xuat_tam_ung {'new': {'trang_thai': 'cho_duyet'}, 'old': {'trang_thai': 'nhap'}}
xt_app      UPDATE/DELETE/INSERT xt_audit.event -> InsufficientPrivilege: permission denied for table event
xt_migrator UPDATE/DELETE/INSERT xt_audit.event -> InsufficientPrivilege: permission denied for table event
superuser UPDATE (trigger) -> xt_audit.event là append-only (UPDATE bị cấm)
superuser tắt trigger rồi sửa số tiền -> verify: (31, 3) | tenant B vẫn: (2, None)      <- phát hiện đúng seq bị sửa
1 tenant x 8 luồng: 1200 bản ghi trong 1.06s = 1129 ghi/s, lỗi=0
2 tenant x 8 luồng: 2400 bản ghi trong 1.54s = 1560 ghi/s, lỗi=0
verify 629c40f1 (2400, None) / verify ba9c5ce1 (1200, None)          <- chuỗi vẫn tuyến tính khi ghi song song
```

### 4.4 Change planning trên dữ liệu thật (t3)

Agent đề xuất v2. Dữ liệu có sẵn 2 tên dài 72 ký tự và 2 email trùng.

```
KẾ HOẠCH THAY ĐỔI MODEL v1 → v2  (mã kế hoạch …)
Tổng: 6 AN TOÀN, 4 RỦI RO, 3 PHÁ HỦY, 3 BỊ CHẶN
  [AN TOÀN] Tạo model mới 'Hợp đồng' (hop_dong) với 3 trường, RLS theo tenant + quyền ['manager']
  [PHÁ HỦY – cần xác nhận lần 2 [drop_field:m2:f12]] Xóa trường 'Tỷ lệ VAT' (ty_le_vat) khỏi 'Đề xuất tạm ứng' — mất dữ liệu cột này của 4 bản ghi
  [PHÁ HỦY – cần xác nhận lần 2 [rename_field:m1:f3]] Đổi tên trường 'email' → 'email_lien_he' trong 'Khách hàng' (RENAME COLUMN, giữ dữ liệu)
  [PHÁ HỦY – cần xác nhận lần 2 [rename_field:m1:f5]] Đổi tên trường 'ghi_chu' → 'ghi_chu_noi_bo' trong 'Khách hàng' (RENAME COLUMN, giữ dữ liệu)
  [AN TOÀN] Thêm trường 'Số điện thoại' (so_dien_thoai, text) vào 'Khách hàng' — cho phép trống
  [RỦI RO] Thêm trường BẮT BUỘC 'Khu vực' (khu_vuc) với giá trị mặc định 'mien_bac' — 27 bản ghi hiện có sẽ nhận giá trị này (PG11+: không rewrite bảng)
  [BỊ CHẶN] Thêm trường BẮT BUỘC 'Mã số thuế' (ma_so_thue) không có default nhưng bảng đã có 27 bản ghi — hãy khai báo 'default' (backfill) hoặc để không bắt buộc — 27 bản ghi vi phạm
  [BỊ CHẶN] Siết ràng buộc: 'Tên khách hàng' tối đa 50 ký tự — dữ liệu hiện có vi phạm, sửa trước khi áp dụng — 2 bản ghi vi phạm (vd: 8101939d-…, a451e638-…)
  [AN TOÀN] Nới ràng buộc: 'Nội dung' phải có ít nhất 3 ký tự
  [RỦI RO] Siết ràng buộc: 'Trạng thái' chỉ nhận: nhap, cho_duyet, da_duyet (đã kiểm dữ liệu hiện có) — 0 bản ghi vi phạm
  [RỦI RO] Thêm ràng buộc: Đề xuất khẩn tối đa 5 triệu (đã kiểm dữ liệu hiện có) — 0 bản ghi vi phạm
  [AN TOÀN] Tạo index i1 trên 'Khách hàng' (CONCURRENTLY, không khóa ghi)
  [BỊ CHẶN] Đặt 'Email liên hệ' là duy nhất (CREATE UNIQUE INDEX CONCURRENTLY) — có giá trị trùng, xử lý trước — 2 bản ghi vi phạm (vd: …)
  [RỦI RO] MỞ RỘNG quyền trên 'Khách hàng': sales:create own→team, sales:read own→team, sales:update own→team
  ...
→ Không thể áp dụng: xử lý các mục BỊ CHẶN trước.
APPLY -> Còn thao tác BỊ CHẶN: ['add_field:m1:f8', 'check:m1:ck_m1_f2_max', 'unique_field:m1:f3']
```

Agent sửa spec theo phản hồi (MST không bắt buộc, bỏ unique email, `ten` max 100). Kế hoạch mới: `7 AN TOÀN, 5 RỦI RO, 3 PHÁ HỦY, 0 BỊ CHẶN`. JSON máy đọc (`outputs/plan_v2.json`) có một op như sau:

```json
{"id": "rename_field:m1:f3", "class": "DESTRUCTIVE", "kind": "rename_field", "model": "khach_hang", "field": "email_lien_he",
 "message_vi": "Đổi tên trường 'email' → 'email_lien_he' trong 'Khách hàng' (RENAME COLUMN, giữ dữ liệu)",
 "violations": null, "sample_ids": [], "requires_confirmation": true, "rehearsal_error": null,
 "sql": [{"phase": "tx", "sql": "ALTER TABLE \"xt_data\".\"khach_hang\" RENAME COLUMN \"email\" TO \"email_lien_he\""}]}
```

Các rào chắn khi apply:

```
APPLY (thiếu xác nhận phá hủy) -> Thiếu xác nhận lần 2 cho thao tác PHÁ HỦY: ['drop_field:m2:f12', 'rename_field:m1:f3', 'rename_field:m1:f5']
APPLY (mã kế hoạch cũ/khác) -> Kế hoạch đã thay đổi kể từ lúc duyệt (duyệt deadbeefdeadbeef, hiện …) — cần duyệt lại
### lock_timeout: một giao dịch khác đang giữ khóa bảng khach_hang
APPLY -> LockNotAvailable: canceling statement due to lock timeout sau 2.0s; version hiện tại = 1     <- rollback sạch
### Áp dụng với xác nhận lần 2
OK v2 tx_steps=50 trong 0.09s; post: 6× VALIDATE CONSTRAINT OK, CREATE INDEX CONCURRENTLY OK
dữ liệu sau rename: ('KH0005', 'a@b.vn', 'mien_bac', None)      <- email giữ nguyên ở cột mới, khu_vuc nhận default
constraint NOT VALID còn lại: 0 | index invalid: 0
```

v3 thử đổi tên model, xóa model và đổi kiểu:

```
[PHÁ HỦY – cần xác nhận lần 2 [drop_model:m3]] XÓA model 'Hợp đồng' (hop_dong) cùng 0 bản ghi
[PHÁ HỦY – cần xác nhận lần 2 [rename_model:m2]] Đổi tên model 'de_xuat_tam_ung' → 'de_xuat_chi' (giữ dữ liệu; client/agent dùng tên cũ sẽ lỗi)
[BỊ CHẶN] Đổi kiểu 'Mã khách hàng' text→number: chưa hỗ trợ — tạo trường mới, chuyển dữ liệu, rồi xóa trường cũ
...
OK v3; bảng: ['de_xuat_chi', 'khach_hang'] | số dòng de_xuat_chi: 4 | policy: 4   <- policy/trigger theo id, sống qua rename
```

---

## 5. Tấn công định danh và giá trị (t4)

```
### A. Tên độc hại trong spec -> bị từ chối khi validate
  'x"; drop table xt_meta.catalog_version; --' -> tên định danh phải là snake_case ASCII ...
  'khách_hàng'                                  -> tên định danh phải là snake_case ASCII ...
  'select'                                      -> 'select' là từ khóa SQL dành riêng
  'a'*41                                        -> ... tối đa 40 ký tự
  'tenant_id'                                   -> tên trường trùng cột hệ thống: ['tenant_id']
  model.name: từ chối 18/18      field.name: từ chối 18/18
     (gồm cả ｓelect full-width, user, order, a*300, xmin, Ten, "ten\x00", "ten\n", a__b, 1abc, _x, "", ten'--)
  role độc hại "admin') OR true OR ('"  -> tên định danh phải là snake_case ASCII ...
  ref tới model không tồn tại (pg_authid) -> model đích 'pg_authid' không tồn tại
  rule tham chiếu cột hệ thống           -> rule r9 tham chiếu trường không tồn tại: ['tenant_id']
  rule kiểu sai (date so với money)      -> không so sánh tu_ngay(date) với so_tien(money)
  rule là SQL tự do {"sql": ...}         -> Field required (không có nhánh nào nhận SQL)
  regex backreference (ReDoS)            -> regex không hợp lệ/không được hỗ trợ: backreferences are not supported
  id ổn định độc hại                     -> id ổn định phải khớp [a-z0-9][a-z0-9_]{0,23}

### B. Giá trị độc hại hợp lệ về cú pháp -> được quote thành literal
  CHECK enum: CONSTRAINT "ck_m9_f4_enum" CHECK ("hang" IN ('thuong', 'vang', 'x''); DROP TABLE xt_meta.catalog_version; --'))
  sqlglot đếm câu lệnh trong CREATE TABLE: 1
  bảng tạo được, enum độc hại lưu nguyên văn: ("x'); DROP TABLE xt_meta.catalog_version; --", "'); DELETE FROM xt_audit.event; --")
  comment: Khách'); DROP TABLE xt_meta.catalog_version; --
  catalog_version còn nguyên: 3 phiên bản

### C. Bỏ qua validator (model_construct) -> Identifier vẫn quote an toàn
   CREATE TABLE "xt_data"."x""; DROP TABLE xt_meta.catalog_version; --" (
   relname trong pg_class: x"; DROP TABLE xt_meta.catalog_version; -- | sqlglot: 1 câu lệnh
   catalog_version còn nguyên: 3

### D. Runtime
  role 'staff,accountant'  -> role không hợp lệ (không chèn được role qua GUC)
  filter lồng 10 cấp       -> biểu thức lồng quá 8 cấp
  filter in 1000 phần tử   -> toán tử in cần value là danh sách 1..100 phần tử
  filter op lạ "~"         -> toán tử không hỗ trợ; hợp lệ: '=', '!=', ...
  filter key lạ "sql"      -> khóa 'sql' không hợp lệ (chỉ field/op/value/other_field/and/or/not)
  filter field "ten; drop table x" -> tên định danh phải là snake_case ASCII...
  filter value "x' OR '1'='1"      -> total 0 (bind param)
  sort "desc; drop"        -> BAD_SORT
  limit 1e6                -> limit 1..200, offset 0..10000
  json 100KB               -> 'Chi tiết' vượt 65536 byte (CHECK DB, dịch qua constraint_map)
```

Hai lớp phòng thủ: (1) validator chặn từ cửa vào; (2) **mọi** định danh đi qua `sql.Identifier` và mọi giá trị đi qua `sql.Literal` (DDL) hoặc placeholder (truy vấn). Lớp (2) đã được chứng minh độc lập với lớp (1) ở mục C.

Đo ReDoS (chạy thật):

```
pydantic-core (Rust) '(a+)+$' trên 'a'*5000+'b': 0.00017 s
Python re            '(a+)+$' trên 'a'*24+'b'  : 0.92 s   (tăng gấp đôi theo mỗi ký tự)
Postgres 17 '~'      '(a+)+$' trên 5001 ký tự  : 1.8 ms
```

---

## 6. Hiệu năng (t6, t6b)

```
50 model x 30 trường (+2 model cũ): validate 22 ms | compile 2503 câu DDL 62 ms | plan(diff, đếm) 54 ms | apply 1 transaction 2448 câu 2294 ms | DDL 283 KiB
COPY FROM dưới RLS -> FeatureNotSupported COPY FROM not supported with row-level security
nạp 100k dòng qua xt_app (RLS + audit hash-chain): 23.8s = 4201 dòng/s; tổng dòng 100004, audit 100037
```

Tìm kiếm qua `search_records`, mỗi lần gồm một `count(*)` cộng một trang `LIMIT 20 OFFSET 40`, lấy median của 7 lần. Tenant A có 80k dòng, 40 staff, 5 phòng ban; tenant B có 20k dòng.

| Truy vấn | total | Trước (chỉ RLS) | Sau (+ gợi ý phạm vi, + index dept/team) |
|---|---|---|---|
| staff own, sắp theo created_at desc | 2.000 | 216,3 ms | **25,5 ms** |
| manager department, `trang_thai` + `so_tien >= 10tr` | ~3.96k | 113,0 ms | **38,9 ms** |
| accountant all, `contains 'đà nẵng'` (ILIKE) | ~13.2k | 183,2 ms | 139,1 ms |
| accountant all, khoảng ngày + OR | 2.212 | 37,0 ms | 30,6 ms |
| accountant tenant B, không lọc | 20.001 | 34,1 ms | 21,8 ms |

Nguyên nhân (EXPLAIN thật): policy SELECT là `has_role(a) AND … OR has_role(b) AND …`. Planner không biết role nào đúng nên chỉ dùng được index `(tenant_id, …)` rồi lọc từng dòng: `Rows Removed by Filter: 78000`. Cách sửa: CRUD gộp phạm vi rộng nhất của các role của actor rồi thêm vào WHERE, ví dụ `owner_id = $actor` hoặc `department_id = ANY($depts)`. Lúc này count dùng `Index Scan using ix_m2_owner … Execution Time: 3.655 ms`. Điều kiện thêm này chỉ **thu hẹp**, RLS vẫn là chốt chặn.

`verify()` 80k event audit mất 658 ms (một câu SQL, window `lag`).

---

## 7. Alembic / SQLAlchemy / sqlglot: giúp hay cản?

Output thật t5. Metadata SQLAlchemy được sinh từ spec rồi đưa vào `alembic.autogenerate.compare_metadata` để so với DB:

```
1) DB khớp spec hiện tại -> khác biệt cột/kiểu/nullable/comment: []
2) Sau khi superuser sửa tay -> drift: [('modify_nullable', 'khach_hang', 'ten', False), ('remove_column', 'khach_hang', 'cot_la')]
3) Rename ghi_chu_noi_bo -> ghi_chu_rieng theo autogenerate: [('add_column', 'khach_hang', 'ghi_chu_rieng'), ('remove_column', 'khach_hang', 'ghi_chu_noi_bo')]
4) Policy RLS / trigger / CHECK: compare_metadata không so sánh
```

Đánh giá:

| Tiêu chí | Alembic/SQLAlchemy | Engine tự viết (spike) |
|---|---|---|
| Model là dữ liệu runtime | Thiết kế cho file migration có revision chain, viết bởi dev lúc build | Diff trực tiếp 2 catalog JSON trong DB |
| Rename | Autogenerate ra **drop + add**, tức mất dữ liệu (output 3) | Rename thật theo id ổn định |
| CHECK/RLS/trigger | Không so sánh. Muốn có thì cần `alembic_utils` cho policy/trigger, và vẫn không có CHECK | Sinh và diff đầy đủ, tên ổn định |
| Phân loại SAFE/RISKY + đếm vi phạm | Không có | Có |
| `NOT VALID`/`CONCURRENTLY` 2 pha | Phải viết tay từng op | Tự động |
| Có ích ở đâu | **Phát hiện drift**: DB bị sửa ngoài luồng duyệt (output 2) | — |

Chốt:
- **Không dùng Alembic cho model động.** Có thể dùng Alembic cho schema **tĩnh** của platform (`xt_meta`, `xt_audit`, bảng workflow), là phần dev viết.
- Có thể chạy `compare_metadata` định kỳ trong `xt doctor` để báo drift.
- `psycopg.sql` đủ để sinh DDL an toàn, không cần SQLAlchemy Core.
- `sqlglot` không cần trong đường chính vì xT không nhận SQL. Chỉ dùng trong test để khẳng định một spec sinh ra đúng một câu lệnh. Lưu ý sqlglot không parse hết `CREATE POLICY`.

---

## 8. Vấn đề mở

1. **Tenant tự định nghĩa model khác nhau.** Bảng dùng chung `xt_data.<model>` nghĩa là toàn deployment chung một catalog: tốt cho self-host một DN với nhiều công ty con, và cho template mẫu dùng chung. SaaS nhiều DN có model riêng cần **namespace bảng theo tenant**, ví dụ `t<seq>__<model>` hoặc schema riêng, mà vẫn giữ `tenant_id` + RLS để phòng thủ nhiều lớp. Compiler đã gom tên bảng vào `C.table()` nên chỉ cần đổi ở một chỗ; số bảng = tenant × model (theo dõi catalog bloat khi > 10k bảng).
2. **Đổi kiểu trường hiện bị BLOCKED.** Cần quy trình expand/contract: cột mới, backfill theo lô, đọc kép, rồi drop. Hoán đổi tên (a↔b) trong một lần cũng bị chặn.
3. **Pha 2 thất bại sau commit.** Nếu VALIDATE hoặc CONCURRENTLY lỗi, catalog đã ghi version N nhưng constraint vẫn `NOT VALID` hoặc index invalid. Hiện kết quả chỉ ghi vào `post_status`. Cần worker retry/cleanup và cờ "ràng buộc chưa xác thực" hiển thị cho người duyệt. Ngoài ra unique mới chỉ được đảm bảo khi `CREATE UNIQUE INDEX CONCURRENTLY` xong.
4. **Backfill `UPDATE … SET col = default`** khi đặt bắt buộc hiện chạy trong pha 1 (một câu, khóa dòng). Bảng lớn cần backfill theo lô ở pha 2.
5. **Audit tuần tự theo tenant** đạt khoảng 1.100–1.500 ghi/s với 8 luồng. DN lớn hơn có thể cần "sealer" bất đồng bộ (ghi event trước, băm chuỗi sau). Superuser vẫn sửa được (đã chứng minh `verify` phát hiện ra), nên vẫn cần **neo hash định kỳ ra ngoài** như building-blocks đã nêu. `diff` audit lưu nguyên giá trị nên PII nằm trong audit; cần chính sách mask/xóa theo Luật BVDLCN (mâu thuẫn với append-only, cần thiết kế crypto-shredding).
6. **Ngữ nghĩa regex Rust và Postgres ARE** khác nhau ở rìa: `\d` của Rust là Unicode, lớp ký tự locale. Nên giới hạn spec vào tập con chung (lớp ASCII, neo `^$`) và chạy bộ test vi sai.
7. **`total` đếm chính xác** tốn chi phí khi tập kết quả lớn (ILIKE 13k dòng mất 139 ms). Cân nhắc giới hạn đếm (`count` tới 10k rồi trả "10k+"), `pg_trgm` cho `contains`, keyset pagination thay offset. Đang chặn offset ở mức ≤ 10.000.
8. **Trang "own" sắp theo created_at** vẫn quét index created rồi lọc (26 ms). Thêm index `(tenant_id, owner_id, created_at DESC)` khi model có nhiều người dùng.
9. **Quyền ở mức trường** (ẩn `so_tien` với một role) chưa có. RLS chỉ lọc theo dòng. Cần column privileges hoặc view/projection theo role trong CRUD.
10. **PostgREST** dùng chung bảng thì phải đặt cùng GUC `xt.*` từ claim JWT (pre-request function) và cần `NOTIFY pgrst` sau mỗi apply. Chưa thử trong spike này.
11. **Spec của model đã áp dụng** đang được đọc lại từ `catalog_version` mỗi lần plan. Cần cache theo version và thông báo cho các tiến trình MCP (LISTEN/NOTIFY) để `Engine.set_catalog()` được gọi đúng lúc.
12. **Tool MCP.** Ánh xạ đề xuất: `propose_model_change(spec)` → `plan(rehearse=True)` trả `render_vi()` + JSON; `approve_model_change(plan_hash, confirm_ids)` chỉ dành cho người, qua MCP elicitation hoặc UI MCP Apps, sau đó gọi `apply`. Agent **không** được giữ quyền approve (xem rea-lessons §3, mức tự chủ L0–L3).

---

## 9. File

- Báo cáo này: `research/spike-model-engine.md`
- Mã (header SPDX Apache-2.0 ở mọi file .py/.sql/.sh/.yaml/requirements): `research/spike-model-engine/{xtmodel,tests}/`, `examples.py`, `examples.yaml`, `reset.sh`, `requirements.txt`
- Output thật: `research/spike-model-engine/outputs/out_t*.txt`, `plan_v2.json` (kế hoạch máy đọc), `schema.model-spec.json` (JSON Schema cho tool). Hai file JSON không có header vì JSON không có cú pháp comment.
