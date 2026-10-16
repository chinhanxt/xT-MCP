<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->
# Audit Model Engine (bản port chưa commit), 15/10/2026

**Phạm vi:** `src/xt/domain/model/*`, `src/xt/contracts/model.py`, `src/xt/application/model_engine/*`, `src/xt/adapters/db/*` (trừ `process_timers.py`, queue), `tests/unit/model/`, `tests/integration/test_model_engine_pg.py`, `docs/model-engine.md`.

**Đối chiếu với:** `docs/architecture-v2.md` v2.2 (D5/D6, §5.3, §5.6, §6.1, G-M1/G-M2), `docs/tool-contracts-v1.md`, `research/spike-model-engine.md`, `research/spike-model-tenancy.md`.

**Cách làm:**
- Tấn công trực tiếp trên Postgres 17 thật (`postgres:17-alpine`, container riêng, cổng 20301–20303).
- Không sửa `src`.
- Container và artifact tạm đã xóa hết. `df -h /` lúc cuối: 6,6 GB trống.

## 0. Kết luận

**CHƯA ĐẠT để chốt "G-M2 đã đóng" và để merge.**

Những phần đứng vững:
- Cô lập tenant: RLS, schema riêng, FK ghép, view `security_invoker=false`, gắn login BI theo `session_user`.
- G-M1 ở lớp DB (quyền cột).
- Chống SQL injection.
- Ràng mã kế hoạch (plan hash) với digest của spec.

Những lỗi phải sửa trước khi chốt:
- **2 lỗi HIGH:**
  - AUD-01: đổi tên trường `sensitive` thì audit ghi nguyên văn, tức G-M2 bị vượt.
  - AUD-02: một lần dry-run của agent chạy backfill toàn bảng và chặn ghi của tenant.
- **5 lỗi MEDIUM:** AUD-03 đến AUD-07.
- Các lỗi LOW và INFO liệt kê ở §2.

| Kiểm thử | Kết quả |
|---|---|
| `uv run pytest tests/unit/model` | **134 passed** (sau khi thêm file audit: 135 passed + 7 xfailed) |
| `tests/integration/test_model_engine_pg.py` (PG17, cổng 20301) | **10 passed** trong 9,6 s |
| Regression mới `tests/unit/model/audit/` + `tests/integration/model_audit/` | 17 passed (phòng thủ đứng vững) + **19 xfailed strict** (lỗi đã tái hiện) |

## 1. Bảng PASS/FAIL theo mục tấn công

| # | Mục | KQ | Bằng chứng |
|---|---|---|---|
| 1 | SQL injection qua giá trị bản ghi/filter (`'); drop…`, `in`, `other_field`) | PASS | Giá trị đi qua placeholder/`sql.Literal`, định danh qua `sql.Identifier` + whitelist `[a-z][a-z0-9_]{0,39}`. Payload chèn trả 0 dòng, không lỗi cú pháp |
| 2 | Chèn qua ORDER BY | PASS | `sort=["code; drop"]`, `-tenant_id`, `revision` → `invalid_request` |
| 3 | Ký tự đại diện LIKE (`%`, `_`, `\`) | **FAIL** (AUD-03) | Có escape nhưng `contains`/`startswith` **luôn** lỗi. Không khai thác được để chèn SQL |
| 4 | JSON path | PASS | DSL không có toán tử JSON, trường `json` chỉ nhận `is_null`/`not_null` |
| 5 | Định danh homoglyph/unicode (`ﬁeld`, `kelvinK`, `aа` Cyrillic), từ khóa, quá 40 ký tự | PASS | Đều bị từ chối. `oid/xmin/ctid` làm tên field bị `SYSTEM_COLUMNS` chặn |
| 6 | Chéo tenant qua view `api_t*` / role BI | PASS | BI gắn A đọc `api_t<B>` → 42501. `xt_web` đặt GUC sang B → chỉ thấy dữ liệu B qua view B (chặn bằng pre-request, xem INFO-1) |
| 7 | `SET ROLE`/`set_config` từ `xt_web`/`xt_app` | PASS | `SET ROLE xt_app/xt_workflow/xt_api_owner/xt_audit_owner/xt_migrator`, `SET SESSION AUTHORIZATION`, `session_replication_role` → 42501 |
| 8 | Chiếm search_path | PASS (có INFO-3) | Hàm SECURITY DEFINER đều ghim `search_path`. `xt_app`/`xt_web`/BI không có CREATE ở schema nào, không có TEMP (`has_database_privilege(...,'TEMP') = false`) |
| 9 | COPY khi có RLS | PASS | `COPY (SELECT * FROM xt_t<B>.emp) TO STDOUT` trong ngữ cảnh A → 0 dòng. `COPY ... FROM` cột workflow → 42501 |
| 10 | Prepared statement / GUC dùng lại qua pool | PASS | `prepare_threshold=None`. Sau `tenant_tx`, cùng kết nối pool có `current_setting('xt.tenant_id') = ''`, `current_user = xt_app` |
| 11 | G-M1: bulk UPDATE … FROM, INSERT … SELECT, ON CONFLICT DO UPDATE, MERGE, COPY, sửa qua view, `revision`/`tenant_id` | PASS | 14 câu lệnh của `xt_app` đều 42501 (test `test_xt_app_cannot_bypass_gm1_or_escalate`) |
| 12 | G-M1: thứ tự trigger / quyền của `xt_workflow` | **FAIL nhẹ** (AUD-09) | `xt_workflow` sửa được `created_by` |
| 13 | Audit: UPDATE/DELETE/TRUNCATE/tắt trigger bằng role thường | PASS | `xt_app`/`xt_migrator` không sửa được. `ALTER TABLE … DISABLE TRIGGER xt_audit` → "must be owner" |
| 14 | Audit: superuser xóa đuôi chuỗi / tính lại toàn chuỗi | FAIL có chủ đích (AUD-16) | `verify` vẫn báo toàn vẹn. Đây là giới hạn đã ghi trong §5.6, nhưng docstring `audit.py` nói quá |
| 15 | Audit: ghi đồng thời ở REPEATABLE READ | **FAIL** (AUD-05) | Chuỗi rẽ nhánh, `first_bad_seq` báo sai |
| 16 | Khóa HMAC: không nằm trong log, không đặt được tùy ý | **FAIL** (AUD-07, AUD-15) | `xt_migrator` cài được khóa của mình. Bật `log_statement=all` thì khóa hiện nguyên văn trong log |
| 17 | Giá trị sensitive không vào audit dạng plaintext | **FAIL** (AUD-01) | Đổi tên trường lương thì audit ghi `{'old': {'luong': 12345678}, 'new': {'luong': 99999999}}` |
| 18 | Thao tác DESTRUCTIVE thiếu xác nhận lần 2 | PASS | `check_gate` → `confirmation_missing` (test sẵn có) |
| 19 | Rename và drop+add | PASS | Diff theo id ổn định: cùng id → `RENAME COLUMN`. Hoán đổi tên → BLOCKED |
| 20 | Duyệt plan xong rồi đổi spec (ràng digest) | PASS | Duyệt hash của spec X rồi áp spec Y (chỉ khác nhãn) → `revision_conflict` |
| 21 | `lock_timeout` khi có giao dịch dài đồng thời | PASS | Giữ ACCESS SHARE → `timeout` sau 1,0 s, `commit_state=rolled_back`. Version, cột và job không đổi. Thử lại thì thành công |
| 22 | Lỗi giữa chừng phải để tenant nhất quán | Pha 1 PASS, **pha 2 FAIL** (AUD-06) | Job `concurrent` chết thì catalog vẫn ghi `unique=true` nhưng index INVALID. Version sau vẫn áp lên được |
| 23 | Lệch phiên bản | PASS / LOW (AUD-08) | Phân bố version theo tenant đúng. Đề xuất cũ gặp `ValueError` thô thay vì `revision_conflict` |
| 24 | Dry-run không có tác dụng phụ | **FAIL** (AUD-02) | Diễn tập chạy `UPDATE` backfill thật (sau đó rollback) |
| 25 | ReDoS phía Rust | PASS | `^(a+)+$`, `(x+x+)+y`… tuyến tính. Lặp lồng vượt giới hạn bị Rust hoặc PG từ chối (PG lỗi → diễn tập ra BLOCKED) |
| 26 | Regex Rust ↔ PG cho kết quả khác nhau | **FAIL nhẹ** (AUD-10, AUD-17) | `\x41B`: Rust khớp "AB", PG hiểu là U+041B |
| 27 | Spec rất lớn (DoS) | **FAIL nhẹ** (AUD-11) | Catalog 134,6 MB được nhận: validate 11,1 s, compile 9,3 s, 22.000 bước |
| 28 | Số cột tối đa, tên constraint ≤ 63 byte | PASS | 200 field + 10 cột hệ thống < 1600. `cname` chặn cứng > 63 byte |
| 29 | Trần catalog (200 tenant, 6.000 bảng) | PASS | `resource_constraint` (test sẵn có + đọc mã) |

## 2. Lỗi chi tiết

Quy ước:
- **Repro** là kịch bản đã chạy thật.
- **Test** là regression `xfail(strict=True)`. Sửa xong lỗi thì test sẽ XPASS và làm suite fail, nhắc gỡ marker.

### AUD-01: HIGH, G-M2 bị vượt: đổi tên trường `sensitive` thì audit ghi plaintext

**Nguyên nhân:**
- Danh sách cột sensitive truyền vào trigger qua `TG_ARGV` bằng **tên cột** (`audit_trigger_steps`).
- `diff_model` chỉ phát change `pii` khi tập **id** sensitive thay đổi (`o_sens != n_sens`).
- Đổi tên không làm đổi id, nên không có change `pii`, và trigger giữ `tgargs = 'salary'`.
- Từ đó cột `luong` mới không còn được HMAC.

**Repro (đã chạy):**
1. Tạo `emp.salary` (sensitive) = 12345678.
2. Đổi tên `salary` → `luong_co_ban` (DESTRUCTIVE, đã xác nhận).
3. Gọi `update` = 99999999.
4. Kết quả: `xt_audit.event.diff = {"new": {"luong_co_ban": 99999999}, "old": {"luong_co_ban": 12345678}}` và `pg_trigger.tgargs = b'salary\x00'`.

**Sửa:**
- Trong `diff_model`, so cả **tên** cột sensitive (`{nf[i].name for i in n_sens} != {of[i].name for i in o_sens}`) để phát change `pii`. Hoặc tạo lại trigger audit mỗi khi `rename_field` chạm vào trường sensitive.
- Thêm test integration: đổi tên trường sensitive thì diff vẫn là `hmac:`.
- Tenant nào đã từng đổi tên trường sensitive thì đã lộ dữ liệu. Cần rà `xt_audit.event`.

**Test:** `test_renamed_sensitive_field_still_hmac_in_audit`.

### AUD-02: HIGH, DoS tenant: dry-run/diễn tập chạy backfill toàn bảng, không có statement_timeout

**Nguyên nhân:**
- `PgMigrator._rehearse` chạy **mọi** bước phase `tx` trong transaction rollback, kể cả `UPDATE … SET col = default WHERE col IS NULL` của `require_field`.
- Bước này kéo theo trigger audit: 1 event/dòng và `pg_advisory_xact_lock` của chuỗi audit tenant.
- Diễn tập còn giữ ACCESS EXCLUSIVE suốt thời gian chạy.
- `_plan` không đặt `statement_timeout`.
- `propose_model_change` với `dry_run=true` (agent gọi được) đi qua đúng đường này.

**Repro (đã chạy, bảng 200.000 dòng):**
- `plan(rehearse=True)` cho thay đổi "`note` bắt buộc, default `n/a`" mất **47,0 s**.
- `n_tup_upd` tăng **200.003**.
- Một `create_record` chạy đồng thời trên tenant đó bị **timeout sau 15,03 s**.

**Sửa:**
- Không chạy bước sửa dữ liệu (backfill) khi diễn tập. Probe `NullCount` đã cho số dòng bị ảnh hưởng.
- Đặt `SET LOCAL statement_timeout` (vd 5 s) cho kết nối plan/diễn tập.
- Cân nhắc giới hạn tần suất propose.

**Test:** `test_rehearsal_does_not_execute_backfill`. Bảng 500 dòng tiêu 500 seq audit trong transaction đã hủy.

### AUD-03: MEDIUM, `contains`/`startswith` của search luôn lỗi

**Nguyên nhân:**
- `compile_expr` nhúng pattern bằng `sql.Literal('%…%')` vào câu truy vấn có tham số (luôn có `LIMIT %s OFFSET %s`).
- psycopg coi `%` trong literal là placeholder.

**Repro:**
- `where={"field":"note","op":"contains","value":"done"}` → `only '%s', '%b', '%t' are allowed as placeholders, got '%d'` → `internal_error "Lỗi lưu trữ dữ liệu"`.
- `startswith "100%"` không lỗi nhưng cho kết quả **sai** (0 dòng), vì `%%` bị psycopg đổi thành `%`.
- Không thoát được khỏi chuỗi trích dẫn, nên không phải injection.
- Chưa có test nào phủ hai toán tử này.

**Sửa:** dùng `literal(f, pat)` (placeholder) thay cho `sql.Literal(pat)`.

**Test:** `test_search_contains_and_startswith[contains|startswith]`.

### AUD-04: MEDIUM, lọc theo `created_at`/`updated_at`/`owner_id` → `KeyError` (lỗi 500)

**Nguyên nhân:**
- `RecordService.search` kiểm kiểu bằng `_with_system(m)` và còn trả các trường này trong `valid_fields`.
- Nhưng `PgRecordRepository.search` compile bằng `m.by_name()`, không có cột hệ thống.

**Repro:** `where={"field":"created_at","op":">","value":"2020-01-01T00:00:00+00:00"}` → `KeyError: 'created_at'`, không được dịch thành lỗi TCV1.

**Sửa:** truyền map trường dùng để kiểm kiểu (gồm cột hệ thống) xuống repo.

**Test:** `test_search_filter_on_system_fields[*]`.

### AUD-05: MEDIUM, chuỗi audit rẽ nhánh khi ghi ở REPEATABLE READ/SERIALIZABLE

**Nguyên nhân:**
- `xt_audit.log()` lấy advisory lock rồi `SELECT hash … ORDER BY seq DESC LIMIT 1`.
- Ở RR, snapshot có từ câu lệnh đầu tiên của transaction, nên hai transaction cùng đọc một `prev`.

**Repro:**
- 2 luồng `BEGIN ISOLATION LEVEL REPEATABLE READ` đọc trước, rồi chèn đồng thời.
- `verify` → `ChainStatus(events=6, first_bad_seq=7)`.
- Báo động giả này vĩnh viễn: từ đó không còn phân biệt được với sửa đổi thật.

**Sửa:** chọn một trong hai:
- `IF current_setting('transaction_isolation') <> 'read committed' THEN RAISE`.
- Dùng bảng đầu chuỗi `xt_audit.head(tenant_id PK, seq, hash)` cập nhật bằng `UPDATE … RETURNING`. Khi đó RR gặp lỗi serialization thay vì rẽ nhánh.

**Test:** `test_audit_chain_survives_repeatable_read_writers`.

### AUD-06: MEDIUM, job pha 2 `dead` làm catalog và DB lệch nhau mãi mãi

**Repro:**
1. Thêm field `badge unique` (index `CONCURRENTLY` ở pha 2).
2. Chèn 2 giá trị trùng trước khi worker chạy.
3. Sau 3 lần `run_jobs`, job ở trạng thái `dead` và `post_state='failed'`.
4. `ux_m2_e9` còn **INVALID**, nhưng catalog vẫn `unique=true`.
5. Bản ghi trùng thứ 3 vẫn được nhận, vì app không kiểm unique.
6. Plan version sau chỉ có `label_field` (không sửa chữa gì) và **vẫn áp được** lên trên.

**Sửa:**
- `apply` từ chối khi version gần nhất có `post_state='failed'`. Hoặc planner sinh change "sửa chữa" (drop index INVALID, probe trùng → BLOCKED).
- Hiển thị trạng thái này trong `describe_model`/plan.

**Test:** `test_dead_post_job_blocks_or_repairs_next_version`.

### AUD-07: MEDIUM, `xt_migrator` tự cài được khóa HMAC, rồi dò ngược giá trị sensitive

**Nguyên nhân:** `GRANT EXECUTE ON FUNCTION xt_audit.set_pepper(int, bytea) TO xt_migrator`.

**Repro:**
- `xt_migrator` chạy `set_pepper(3, 'K'*32)`.
- Lương 31.000.000 ghi sau đó cho `hmac:3:ce44c42c…`.
- Vét cạn 0..100 triệu bước 1.000 tìm ra **31000000** trong < 1 s.

**Sửa:**
- `REVOKE EXECUTE ON FUNCTION xt_audit.set_pepper … FROM xt_migrator`. Chỉ đường bootstrap/superuser (secret OpenBao/Compose) được xoay khóa.
- Ghi quy trình xoay khóa vào tài liệu.

**Test:** `test_migrator_cannot_install_pepper`.

### AUD-08: LOW, đề xuất cũ trả `ValueError` thô thay vì `revision_conflict`

**Repro:**
1. `propose(dry_run=false)`.
2. Một thay đổi khác được áp (version tăng).
3. `approve_and_apply` → `ValueError: spec mới phải có version = 4 (đang là 3)`, lọt qua `ModelService._apply` thành lỗi 500.
4. Đường replay idempotent trong `propose` cũng gặp lỗi này.

**Sửa:** bắt `ValueError` của `build_plan` trong migrator và trả `PlanRejected("plan_changed", …)`.

**Test:** `test_stale_proposal_is_revision_conflict`.

### AUD-09: LOW, `xt_workflow` giả mạo được `created_by`

**Nguyên nhân:** `xt.row_touch()` chặn `tenant_id/id/created_at` nhưng không chặn `created_by`. `xt_workflow` có UPDATE cả bảng.

**Repro:** `SET LOCAL ROLE xt_workflow; UPDATE … SET created_by = gen_random_uuid()` → 1 dòng.

**Sửa:** trong `row_touch`, khi UPDATE gán `NEW.created_by := OLD.created_by`. Hoặc đưa `created_by` vào kiểm tra bất biến.

**Test:** `test_workflow_role_cannot_forge_created_by`.

### AUD-10: LOW, regex `\xhh` hiểu khác nhau giữa Rust và PG

**Repro:** `^\x41B$`. Rust hiểu là "A" rồi "B". PG ARE `\x` ăn hết chữ số hex, thành U+041B. App nhận "AB" nhưng CHECK ở DB từ chối (`rule_violation` cho dữ liệu hợp lệ).

**Sửa:** `check_regex` cấm `\x`, `\u`, `\U` (hoặc buộc `\x` + đúng 2 hex rồi tới ký tự không phải hex).

**Test:** unit `test_regex_hex_escape_diverges_between_rust_and_pg`.

### AUD-11: LOW, không có trần kích thước spec

**Repro:** catalog 100 model × 200 field × 50 option = **134,6 MB** được `parse_catalog_json` nhận: validate 11,1 s, compile 9,3 s.

**Ghi chú:**
- Đường TCV1 bị giới hạn 50 op mỗi đề xuất, nhưng catalog vẫn tăng dần.
- Mỗi version và mỗi đề xuất lưu **toàn bộ** spec (jsonb).

**Sửa:** `MAX_SPEC_BYTES` (vd 1 MiB) trong `load_spec_json` + trần tổng số field mỗi catalog.

**Test:** unit `test_spec_json_has_byte_cap`.

### AUD-12: LOW, nhãn nhận U+2028/U+2029/U+202E/U+200B, giả được dòng trong kế hoạch người duyệt đọc

**Nguyên nhân:** `check_label` chỉ chặn `ord < 32` và 127.

**Repro:** nhãn `"Hạn mức   [AN TOÀN] Đổi nhãn trường note…‮"` làm `render_vi()` hiện thêm dòng `[AN TOÀN]` giả ngay cạnh mục PHÁ HỦY.

**Sửa:** từ chối các nhóm Unicode `Cc, Cf, Zl, Zp` (`unicodedata.category`).

**Test:** unit `test_label_rejects_line_and_bidi_controls[*]`.

### AUD-13: MEDIUM (cần quyết định thiết kế), login BI giả được `xt.roles`/`xt.actor_id`, nên RBAC trong tenant vô hiệu

**Hiện trạng:**
- `xt.tenant()` bỏ qua GUC khi login đã gắn tenant, nhưng `has_role`/`actor`/`in_teams` vẫn đọc GUC.

**Repro:**
- `bi_a` không đặt role → 0 dòng (BI không dùng được).
- `set_config('xt.roles','hr')` → thấy hết 4 dòng.
- `xt.actor_id` = người 99 + role `staff` (scope own) → đọc được bản ghi riêng của người 99.

**Sửa (chọn một):**
- (a) BI = đọc toàn tenant một cách tường minh: policy SELECT riêng cho login đã gắn, bỏ qua GUC role.
- (b) Gắn role/actor vào `xt_meta.login_tenant` và bỏ qua GUC khi `xt.login_tenant()` khác NULL.

Chưa viết test xfail cho mục này vì còn chờ quyết định.

### AUD-14: LOW, `verify_hmac` lệch dạng text của jsonb (datetime, date, json)

**Nguyên nhân:**
- Trigger HMAC trên `to_jsonb(NEW) ->> col`, dạng ISO có `T`.
- `audit_hmac` dùng `str(datetime)`, dạng có dấu cách.
- Kết quả: kiểm chứng sai với trường sensitive kiểu `datetime` hoặc `json`.

**Sửa:** chuẩn hóa theo cách jsonb in ra (`isoformat()`, JSON compact kiểu jsonb).

**Test:** unit `test_verify_hmac_matches_jsonb_text_for_datetime`.

### AUD-15: LOW, khóa HMAC lộ ra log, và khóa nằm trong DB

**Repro:**
- Bật `log_statement=all`.
- `SELECT xt_audit.set_pepper($1,$2)` ghi `DETAIL: Parameters: … $2 = '\xabab…'`. `bootstrap()` dùng đúng lời gọi này.
- Khóa cũng nằm ở `xt_audit.pepper`: superuser đọc được, và `pg_dump -n xt_audit` lấy được.

**Sửa:**
- Trong `bootstrap()`, chạy `SET LOCAL log_statement='none'; SET LOCAL log_min_duration_statement=-1` trước `set_pepper`.
- Ghi rõ trong tài liệu triển khai: backup của schema `xt_audit` chứa khóa, phải tách hoặc mã hóa.

### AUD-16: LOW (tài liệu), docstring `audit.py` nói quá

- `audit.py` viết "phát hiện sửa đổi (kể cả của superuser)".
- **Repro:** superuser xóa event cuối, hoặc sửa một event giữa chuỗi rồi tính lại hash các event sau → `verify` vẫn ra `first_bad_seq=None`.
- Kiến trúc §5.6 đã ghi đúng ("không chống được superuser", neo Merkle ở v1.0).
- **Sửa:** sửa docstring theo đúng §5.6.

### AUD-17: LOW, regex hợp lệ ở Rust có thể tốn CPU ở PG

- `([a-z]*[a-z]*){200}$` trên 3.000–10.000 ký tự mất **1,27–1,90 s** mỗi lần đánh giá CHECK ở PG, trong khi Rust mất 0,000 s.
- Số đo dao động vì PG có heuristic.
- Bị chặn trên bởi `statement_timeout` 15 s, nhưng vẫn khuếch đại CPU theo từng lần ghi.
- **Sửa:** cấm lượng từ lồng có `*`/`+` bên trong nhóm có `{n}` với n > 10, hoặc giới hạn `max` của text khi có `regex`.

### INFO (không tính là lỗi)

1. **Chưa có `xt.pgrst_pre`:**
   - `xt_web` vẫn tự chọn tenant được bằng `set_config` nếu có SQL trực tiếp.
   - PostgREST không cho làm vậy, nhưng cổng `Accept-Profile` ↔ JWT (§D6) chưa tồn tại.
   - Hiện tại fail-closed (không có GUC → 0 dòng). `docs/model-engine.md` §7 đã ghi.
2. **`rollout` mặc định `allow={SAFE, RISKY}`:**
   - Mở rộng quyền, hạ nhãn DLCN và bỏ `managed_by` (đều RISKY) được áp tự động cho mọi tenant mà không có người xem plan của từng tenant.
   - Nếu truyền `DESTRUCTIVE` vào `allow` thì các mã phá hủy được tự xác nhận.
3. **`search_path` của hàm SECURITY DEFINER thiếu `pg_temp` ở cuối:**
   - Hiện an toàn vì mọi bảng đều ghi schema và không role nào có TEMP.
   - Nên thêm `pg_temp` để phòng thủ nhiều lớp.
4. **`pg_advisory_xact_lock` catalog được lấy trước `SET LOCAL lock_timeout`,** nên apply đồng thời trên cùng tenant phải chờ không giới hạn.
5. **Các bộ nhớ đệm và lời giải thích còn lại:**
   - Cache `RecordService._models` không giới hạn (tenant × version × model × mode).
   - Lỗi CHECK/UNIQUE của PG ghi `DETAIL` có giá trị sensitive vào log server. App không trả ra ngoài vì chỉ dùng `message_primary`.

## 3. File regression mới (không đụng `src`)

- `tests/unit/model/audit/test_audit_spec_findings.py`: AUD-10, 11, 12, 14 (7 xfail strict).
- `tests/integration/model_audit/test_model_engine_audit_pg.py`:
  - 16 test phòng thủ PASS: G-M1/leo quyền ×14, COPY + RLS, lock_timeout rollback.
  - 12 xfail strict: AUD-01..09.
  - Container `xt-audit-model-pg`, cổng 20303, tự xóa khi xong.
- Đã kiểm bằng `--runxfail`: mọi xfail fail **đúng** lý do đã ghi.

Lệnh chạy lại:

```sh
uv run pytest tests/unit/model -q --no-cov
uv run pytest -m integration tests/integration/model_audit tests/integration/test_model_engine_pg.py --no-cov
```

Ghi chú: `tests/integration/pg.py` cố định cổng 19700. Lần chạy audit này đổi sang 20301 bằng một plugin pytest tạm (đã xóa), không sửa file.

## 4. Thứ tự sửa đề xuất

1. AUD-01, AUD-02 (chặn merge).
2. AUD-07, AUD-05, AUD-03, AUD-04, AUD-06.
3. Quyết định thiết kế cho AUD-13 và INFO-2.
4. Các lỗi LOW.

Sau khi sửa, gỡ `xfail` tương ứng. Test sẽ XPASS và làm suite fail cho tới khi gỡ.

## Phản hồi tác giả (15/10/2026)

Đã sửa toàn bộ AUD-01…AUD-17 và INFO-2/3/4 trong các module model engine. Đã gỡ mọi marker `xfail`. Kết quả chạy lại:

| Kiểm thử | Kết quả |
|---|---|
| `uv run pytest tests/unit/model -q --no-cov` | 145 passed |
| `uv run pytest -m integration tests/integration/test_model_engine_pg.py tests/integration/model_audit --no-cov` | 40 passed (PG17, cổng 19700 + 20303) |
| `make test-integration` (toàn repo) | 105 passed, 1 skipped |

| Mã | Cách sửa |
|---|---|
| AUD-01 | Trigger audit được sinh lại từ catalog MỚI cho mọi model bị chạm, ngay trong cùng transaction pha 1 (`compile_plan`). Danh sách cột sensitive vì thế luôn theo tên hiện hành của field có `pii=sensitive` trong catalog. Planner cũng phát change `pii` khi tên cột sensitive đổi |
| AUD-02 | Bước sửa dữ liệu mang cờ `Step.data` và không bao giờ chạy khi diễn tập. Backfill chuyển sang job pha 2 loại `backfill`: chạy theo lô 1.000 dòng, mỗi lô một transaction có `lock_timeout`, tiến độ ghi ở `apply_job.progress`. `ADD CONSTRAINT … NOT VALID` cho NOT NULL chạy sau backfill. Kết nối plan/diễn tập có `statement_timeout` 5 s. Số dòng ảnh hưởng lấy từ probe |
| AUD-03 | Pattern `contains/startswith` đi qua placeholder |
| AUD-04 | `with_system_fields(m)` dùng chung cho kiểm kiểu và compile |
| AUD-05 | Thêm bảng `xt_audit.head(tenant_id, seq, hash)`, nối chuỗi bằng `SELECT … FOR UPDATE`. Ở RR/SERIALIZABLE, transaction đến sau gặp `SerializationFailure` (thử lại) thay vì rẽ nhánh. Test đã sửa để có ghi thật (trước đó có trường hợp cả 2 insert đều lỗi vì thiếu khóa nên test qua mà không kiểm gì) |
| AUD-06 | `apply` từ chối khi version mới nhất có `post_state='failed'` (`precondition_failed`, `post_failed`). Job concurrent chết thì xóa index INVALID. Thêm `reconcile()` chạy lúc khởi động để đối chiếu index INVALID, constraint NOT VALID và job dead với catalog. `retry_failed()` chạy lại sau khi đã sửa dữ liệu. `propose` báo finding `blocker` |
| AUD-07/15 | Khóa không nằm trong DB và không đi qua câu lệnh SQL của bootstrap: DB chỉ có `sha256(khóa)` theo kid, ghi được bằng superuser. App/worker đưa khóa vào từng transaction ghi (GUC `xt.audit_key`). Trigger kiểm dấu vân tay, sai hoặc thiếu thì từ chối. Đã bỏ `set_pepper` |
| AUD-08 | `ValueError` về version đổi thành `PlanRejected("plan_changed")`, rồi thành `revision_conflict` (cả đường duyệt lẫn đường replay idempotent) |
| AUD-09 | `row_touch` chặn đổi `created_by` |
| AUD-10/17 | Regex chỉ cho escape ký tự đặc biệt (cấm `\x \u \d \w…`). Cấm lặp một nhóm có lượng từ không giới hạn bên trong (`(a+)+`, `([a-z]*){200}`). Cấm lazy/possessive và `{` không phải lượng từ |
| AUD-11 | Trần 1 MiB cho một spec JSON và 3.000 field cho một catalog |
| AUD-12 | Nhãn, thông điệp, mô tả và giá trị enum cấm ký tự Unicode Cc/Cf/Zl/Zp/Cs/Co/Cn |
| AUD-13 | Theo quyết định của chủ dự án. Policy không còn đọc GUC mà đọc `xt.cur()`. Login đã gắn (`xt_meta.login_tenant`: tenant, roles, actor, team, phòng ban cố định) lấy ngữ cảnh từ bảng đó. Các phiên còn lại chỉ lấy từ `xt_meta.session_ctx`, khóa theo (pid, xid) và chỉ ghi được qua `xt.set_context` (SECURITY DEFINER, chỉ `xt_app`). Test integration: GUC tự đặt → 0 dòng; BI tự đặt `xt.roles=admin` hoặc đổi tenant → vẫn đúng role/tenant cố định; BI không role → 0 dòng; `xt_web` không gọi được `set_context`; `xt_app` không INSERT thẳng vào `session_ctx` được |
| AUD-14 | `audit_hmac` chuẩn hóa theo dạng `jsonb ->> col` (ISO 8601 có `T`, bool, JSON compact theo thứ tự khóa của jsonb) |
| AUD-16 | Docstring `audit.py` và chú thích `verify` sửa đúng theo §5.6 |
| INFO-2 | `rollout` mặc định chỉ tự áp SAFE. RISKY phải có `allow` tường minh, PHÁ HỦY phải có đúng mã trong `confirm` (không còn tự xác nhận) |
| INFO-3/4 | Hàm SECURITY DEFINER có `pg_temp` cuối `search_path`. `lock_timeout` đặt trước advisory lock của catalog |

Điểm khác với đề xuất, kèm lý do:
1. **AUD-07:** đề xuất ban đầu là "chỉ thu hồi `set_pepper`". Tôi chọn mức chặt hơn: khóa ra hẳn khỏi DB. Hệ quả là mọi đường ghi bảng có field sensitive (kể cả SQL thô của migrator) phải đưa khóa vào. Vì vậy hai test audit `test_rehearsal_does_not_execute_backfill` và `test_audit_chain_survives_repeatable_read_writers` có thêm `set_config('xt.audit_key', …)`. Test `test_migrator_cannot_install_pepper` được viết lại theo thiết kế mới: không còn hàm, migrator không ghi được bảng dấu vân tay, khóa tự chọn bị trigger từ chối.
2. **AUD-07:** `SELECT key FROM xt_audit.pepper` trong danh sách leo quyền đổi thành `SELECT *`, vì cột `key` không còn.
3. **AUD-13:** helper `ctx()` của test audit đổi sang `xt.set_context`, vì GUC không còn tác dụng. Đây chính là hành vi mà quyết định yêu cầu.
4. **AUD-15:** khóa vẫn đi qua GUC trong từng transaction. Nếu bật `log_statement=all`, giá trị hex sẽ hiện trong log server. Ghi rõ trong `docs/model-engine.md`: production không bật `log_statement=all`/`mod` cho role `xt_app`/`xt_migrator`. App không tự tắt được vì `log_statement` là tham số chỉ superuser đổi được.
5. **Chưa làm** (ngoài phạm vi): pre-request PostgREST (`xt.pgrst_pre`) và role `xt_job_t<seq>` cho hàng đợi. Xem `docs/integration-notes.md`.

## Vòng 2 (16/10/2026, Opus tester)

**Cách làm:** đọc lại test tác giả đã sửa, đọc `bootstrap.sql`/`connection.py`/`migrator.py`/`compiler.py`/`spec.py`. Chạy lại suite. Tấn công AUD-01 trên PG17 thật (container riêng `xt-audit2-model-pg`, cổng 20313, đã xóa). Không sửa `src`. Phiên này bị ngắt giữa chừng nên **chưa chạy thực nghiệm** AUD-02 (đo side-effect trên 200k dòng), log/pg_stat_statements, đồng thời SERIALIZABLE, kill job pha 2 và so regex. Các mục đó dưới đây là kết luận **từ đọc mã**, ghi rõ ở cột KQ.

### 1. Kiểm thử chạy lại

| Kiểm thử | Kết quả |
|---|---|
| `uv run pytest tests/unit/model -q --no-cov` | 144 passed, **1 failed**: `test_spec.py::test_all_pack_models_validate` (`158 == 157`). Gói `packages/*` đang bị sửa ở working tree (thêm 1 field); test đếm cứng bị lệch, không phải lỗi engine |
| `pytest -m integration tests/integration/test_model_engine_pg.py tests/integration/model_audit` | 40 passed / 12,3 s |

### 2. Soát các test tác giả đã sửa

| Test | Nhận xét | KQ |
|---|---|---|
| `test_audit_chain_survives_repeatable_read_writers` | Mạnh hơn bản vòng 1: bắt buộc có ít nhất 1 `ok` và số event khớp. Chưa phủ SERIALIZABLE và chưa phủ việc thử lại của transaction thua | PASS (chưa đủ phủ) |
| `test_rehearsal_does_not_execute_backfill` | Chỉ thêm khóa HMAC cho bước seed, ngưỡng `< 10` giữ nguyên. Chỉ đo sequence audit, không đo `n_tup_upd` | PASS |
| `test_migrator_cannot_install_pepper` (viết lại) | Khớp thiết kế mới, nhưng không còn kiểm mục tiêu gốc của AUD-07 ("migrator không dò ngược được HMAC"), xem R2-01 | Bị thu hẹp |
| `SELECT * FROM xt_audit.pepper` | Tương đương bản cũ | PASS |
| helper `ctx()` → `xt.set_context` | Đúng. Nhưng trong `test_xt_app_cannot_bypass_gm1_or_escalate` và `test_migrator_cannot_install_pepper`, lời gọi `ctx()` nằm **bên trong** `pytest.raises(InsufficientPrivilege)`. Nếu `xt_app` mất EXECUTE trên `set_context` thì cả 15 test vẫn qua mà không kiểm gì. Nên đưa `ctx()` ra ngoài khối `raises` | Yếu (LOW) |
| `test_workflow_role_cannot_forge_created_by` | Bắt `psycopg.Error` chung. Nên bắt đúng `CheckViolation` / `xt_immutable_system_columns` | Yếu (LOW) |

### 3. Bảng PASS/FAIL vòng 2

| Mã | KQ | Bằng chứng |
|---|---|---|
| AUD-01 | **PASS** (thực nghiệm) | Chuỗi đổi tên salary→tmp_s, note→salary, tmp_s→note, đổi tên model emp→staff, hoán `pii` hai field trong cùng version, drop rồi add lại cùng tên: `tgargs` luôn theo tên hiện hành, mọi giá trị sensitive đều là `hmac:1:`. INFO: hạ `sensitive→basic` thì event kế tiếp ghi plaintext giá trị cũ. Hợp lý, vì đó là thay đổi RISKY đã duyệt |
| AUD-02 | PASS (đọc mã) | `Step.data` bị loại khỏi diễn tập; backfill chuyển sang pha 2 theo lô; plan có `statement_timeout` 5 s; diễn tập có `lock_timeout`. Còn lại: diễn tập vẫn chạy DDL `tx` dưới ACCESS EXCLUSIVE (tối đa 5 s mỗi lần gọi). Agent gọi dry-run lặp lại thì vẫn chặn ghi theo nhịp đó, và chưa có giới hạn tần suất propose. **Chưa đo lại** |
| AUD-05 | PASS (đọc mã + test) | `xt_audit.head` có `FOR UPDATE` sau advisory lock; identity `seq` cấp sau khi giữ khóa nên thứ tự seq khớp chuỗi. Không có retry trong `tenant_tx`: SerializationFailure đẩy thẳng lên caller |
| AUD-06 | **FAIL** (đọc mã), R2-02 | `apply` chỉ xem `post_state` của version **mới nhất**. Kịch bản: áp vN (unique, job còn pending) → áp ngay vN+1 (không có job pha 2, nên `validated`) → job vN chết → `_drop_invalid` xóa index. Từ đó `reconcile` không còn thấy index INVALID và vN+1 vẫn `validated`, nên vN+2 áp được trong khi catalog vẫn `unique=true` mà không có index |
| AUD-06 (worker) | Rủi ro, R2-03 | `run_jobs` không claim job (không `FOR UPDATE SKIP LOCKED`, không có trạng thái `running`). Hai worker chạy song song có thể cùng chạy một job; `_drop_invalid` của worker này có thể chạm vào index worker kia đang build; lần ghi trạng thái sau cùng thắng, nên có thể đánh `failed/dead` một index thực ra đã hợp lệ |
| AUD-07 | **FAIL một phần**, R2-01 | Khóa đã ra khỏi DB và migrator không ghi được `pepper` (PASS). Nhưng khóa nằm trong GUC phiên của **mọi** `tenant_tx`, đặt cả khi không ghi field sensitive. `xt_migrator` sở hữu bảng `xt_t*` và các hàm `xt.*` (`actor`, `has_role`, `cur`…). Nó có thể gắn trigger hoặc thay một hàm `xt.*` bằng hàm SECURITY DEFINER đọc `current_setting('xt.audit_key')` khi `xt_app` chạy. Có khóa thì dò ngược được HMAC lịch sử, đúng năng lực AUD-07 muốn chặn. **Chưa demo thực nghiệm** |
| R2-04 (mới) | FAIL (đọc mã) | `xt_audit.log()` được GRANT EXECUTE cho `xt_migrator` và không kiểm `TG_TABLE_SCHEMA` hay tenant của bảng. Migrator tạo bảng có cột `tenant_id, id` ở schema của mình, gắn trigger `xt_audit.log()`, rồi chèn → event giả với `tenant_id`/`model` tùy ý được nối hợp lệ vào chuỗi (`verify` vẫn intact). Migrator cũng `DISABLE TRIGGER xt_audit` được trên bảng nó sở hữu. **Chưa demo thực nghiệm** |
| AUD-15 | PASS có điều kiện | Khóa đi qua tham số bind (`$1`), nên `pg_stat_statements` và `pg_stat_activity` chỉ thấy `set_config('xt.audit_key', $1, true)`. Lộ khi bật ghi log tham số (xem §5) |
| AUD-13 | PASS (đọc mã + test tác giả) | `set_context`/`cur` có `SET search_path = pg_catalog, pg_temp`; `REVOKE ALL … FROM PUBLIC` rồi chỉ GRANT `set_context` cho `xt_app`. Khóa `(pid, xid8)` dùng `pg_current_xact_id_if_assigned()`: tx sau trên cùng kết nối pool có xid khác nên không thừa hưởng ngữ cảnh; savepoint rollback thì mất ngữ cảnh (fail-closed); hàm là STABLE nên plan generic của prepared statement vẫn đánh giá lại mỗi lần. INFO: `xt.tenant()`/`has_role()` không ghim `search_path` (toán tử `=`/`ANY` tra theo search_path của caller); hiện an toàn vì BI, `xt_web`, `xt_app` không có CREATE. Hàm mặc định PARALLEL UNSAFE, nên truy vấn qua RLS không chạy song song (hiệu năng). Bảng `session_ctx` giữ 1 dòng mỗi pid, không tự dọn khi backend thoát (UNLOGGED, nhỏ) |
| AUD-10/17 | **FAIL nhẹ** (đọc mã), R2-05 | `check_regex` chỉ coi `[` lồng là ký tự thường, trong khi Rust regex hỗ trợ lớp lồng và phép tập `&&`, `--`, `~~`. Ví dụ `^[a-c&&b]$`, `^[a[b]c]$`: Rust hiểu là giao/hợp tập, PG ARE hiểu là ký tự `&`, `[` và `]` theo sau. Thêm `.`: Rust không khớp `\n`, PG ARE khớp. Ngoài ra lượng từ có cận trên lồng nhau (`(a{255}){255}`) không bị cấm. **Chưa chạy đối chiếu thực nghiệm** |
| AUD-03/04/08/09/11/12/14/16 | PASS | Test hồi quy xanh; đã đọc mã sửa |

### 4. Việc tác giả cần làm

1. **R2-02:** chặn `apply` khi **bất kỳ** version nào của tenant có `post_state IN ('pending','failed')` với job chưa xong. Nếu không thì `reconcile` phải xét mọi version có job `dead`, không chỉ version mới nhất.
2. **R2-03:** claim job bằng `UPDATE … SET status='running', lease_until=… WHERE status IN ('pending','failed') … FOR UPDATE SKIP LOCKED`, hoặc advisory lock theo tenant trong `run_jobs`.
3. **R2-01 / R2-04:** chỉ đặt `xt.audit_key` khi transaction ghi bảng có field sensitive. `xt_audit.log()` kiểm `TG_TABLE_SCHEMA ~ '^xt_t[0-9]+$'` và `NEW/OLD.tenant_id` thuộc đúng tenant của schema. Cân nhắc tách owner bảng `xt_t*` và owner `xt.*` khỏi login `xt_migrator` (migrator `SET ROLE` sang owner NOLOGIN khi apply). Thêm event trigger (superuser) chặn `ALTER TABLE … DISABLE TRIGGER xt_audit` và `CREATE TRIGGER … xt_audit.log` ngoài migration. Ghi rõ trong mô hình đe dọa nếu chấp nhận migrator là tin cậy.
4. **R2-05:** trong `check_regex`, cấm `[` bên trong `[...]` (trừ trường hợp đứng đầu), cấm `&&`, `--`, `~~` trong lớp, cấm `.` (hoặc ép PG dùng chế độ newline-sensitive) và cấm lượng từ có cận lồng nhau.
5. Sửa các test yếu ở §2 và test đếm field cứng.

### 5. `deploy/` phải đặt gì để Postgres không bao giờ log khóa audit

Khóa chỉ đi qua tham số bind của `SELECT set_config('xt.audit_key', $1, true)`. Vì vậy phải tắt mọi đường ghi **giá trị tham số**:

| Tham số (superuser, `postgresql.conf` hoặc `ALTER SYSTEM`) | Giá trị | Lý do |
|---|---|---|
| `log_statement` | `none` (tối đa `ddl`) | `all` ghi `DETAIL: Parameters: $1 = '<khóa hex>'`. Lưu ý: `ddl` lộ mật khẩu `ALTER ROLE … PASSWORD` của `bootstrap()`, nên chạy bootstrap với `SET log_statement='none'` |
| `log_parameter_max_length` | `0` | Chốt chặn chung: không ghi tham số cho cả `log_statement` lẫn `log_min_duration_statement`, `log_min_duration_sample`, `log_transaction_sample_rate` |
| `log_parameter_max_length_on_error` | `0` (mặc định) | Không ghi tham số khi câu `set_config` lỗi (timeout, tx aborted) |
| `log_min_duration_statement`, `log_min_duration_sample`, `log_transaction_sample_rate` | `-1` / `-1` / `0`, hoặc giữ nhưng bắt buộc `log_parameter_max_length=0` | |
| `auto_explain` | Không nạp. Nếu nạp: `auto_explain.log_parameter_max_length=0` | Mặc định `-1` ghi đầy đủ tham số kèm plan |
| `pg_stat_statements` | Dùng được | Chuẩn hóa thành `$n`, không lưu giá trị bind |
| `log_error_verbosity` | `terse` | Bỏ `DETAIL: Failing row contains (…)` của lỗi CHECK/UNIQUE, nơi giá trị **sensitive** hiện nguyên văn (INFO-5 vòng 1) |
| `ALTER ROLE xt_app, xt_migrator SET log_statement='none'; … SET log_parameter_max_length=0` | | Chặn thêm ở mức role (đều là tham số SUSET nên app không tự đổi được) |
| `track_activity_query_size`, `pg_stat_activity` | Không cần đổi | Chỉ chứa văn bản câu lệnh có `$1` |

Thêm smoke test triển khai: `SHOW log_parameter_max_length` = 0 và không có `auto_explain` trong `shared_preload_libraries`, hoặc nếu có thì tham số của nó = 0.

### 6. Kết luận vòng 2

**CHƯA ĐẠT để chốt.**
- Hai lỗi HIGH (AUD-01 đã kiểm thực nghiệm, AUD-02 đã kiểm qua đọc mã) đã đóng. AUD-03/04/05/08–16 PASS.
- Còn mở: **R2-02 (MEDIUM**, vượt được chặn AUD-06), **R2-01/R2-04 (MEDIUM, cần quyết định mô hình đe dọa** cho `xt_migrator`: lấy được khóa, giả hoặc bỏ qua audit), R2-03 (MEDIUM khi có nhiều worker), R2-05 (LOW).
- Phiên vòng 2 bị ngắt nên chưa thêm test xfail mới và chưa chạy thực nghiệm cho R2-01…R2-05. Cần một lượt tester nữa: viết `tests/integration/model_audit/test_model_engine_audit_r2_pg.py` (xfail strict) để tái hiện R2-02/R2-04 trước khi tác giả sửa.

## Phản hồi tác giả, vòng 2 (16/10/2026)

Theo quyết định của chủ dự án (`docs/decisions.md`, "Vai trò `xt_migrator` với audit"): migrator được tin để đổi schema, không được tin với audit.

| Mã | Cách sửa | Test |
|---|---|---|
| R2-01 / AUD-07 | Khóa HMAC **không bao giờ vào Postgres**, kể cả trong transaction ghi: app tính HMAC trong tiến trình và chỉ đưa chuỗi `hmac:kid:hex` (GUC `xt.audit_hmac`) vào transaction có ghi field sensitive. Mức này chặt hơn yêu cầu (2) "chỉ đưa khóa khi ghi sensitive": mã do migrator kiểm soát chạy trong transaction đó không còn gì để lấy | `test_migrator_cannot_install_pepper` viết lại theo mục tiêu gốc: migrator gài policy "gián điệp" `WITH CHECK (xt.spy())` trên bảng tenant. Khi app ghi lương, policy chỉ thu được HMAC: không có khóa, không có giá trị. Migrator không đọc/ghi được `pepper`; thiếu HMAC hoặc kid lạ → từ chối |
| R2-04 | Audit thuộc `xt_audit_owner`. `log()`/`append()` không GRANT cho migrator/app; trigger cài qua `xt_audit.install` (CREATE OR REPLACE TRIGGER, quyền TRIGGER được cấp cho `xt_audit_owner`). `log()` kiểm `TG_NAME`, schema `xt_t<seq>` và tenant của dòng. Event trigger superuser chặn DISABLE/DROP trigger audit, trigger lạ (kể cả trigger gián điệp) và RULE. TRUNCATE được ghi thành event | `test_r2_04_migrator_cannot_tamper_or_forge_audit` (9 tấn công → 42501; giả event qua bảng tự tạo sai tenant → 42501; TRUNCATE có event; chuỗi vẫn toàn vẹn) |
| Neo ngoài DB | Checkpoint ký Ed25519 lưu ngoài Postgres; `xt audit verify --against-checkpoint` | `test_external_checkpoint_detects_tail_deletion_and_recompute`: superuser xóa đuôi chuỗi và sửa `head` thì DB `verify` vẫn "intact", còn checkpoint báo `missing`, CLI trả 1; checkpoint ký bằng khóa khác → `CheckpointError` |
| R2-02 | `apply` chặn khi còn bất kỳ job nào chưa `done` ở mọi version. `reconcile` xét job dead và job chờ ở mọi version | `test_r2_02_pending_jobs_of_earlier_version_block_next_apply` + test cũ `test_dead_phase2_job_blocks_until_repaired_and_reconciles` |
| R2-03 | `run_jobs` claim theo tenant bằng `pg_try_advisory_lock`; lỗi thì dừng tenant | `test_r2_03_parallel_workers_claim_jobs_once` (4 worker song song, 0 failed/dead, index hợp lệ) |
| R2-05 | Cấm `[` lồng, `&&`/`--`/`~~` trong lớp, `.`, lặp nhóm có lượng từ (kể cả có cận); chỉ cho escape `\n \r \t` và ký tự đặc biệt | Unit: 16 pattern mới bị từ chối; regex của các gói vẫn hợp lệ; test vi sai Rust ↔ PG vẫn xanh |
| Test yếu | `ctx()` đưa ra ngoài `pytest.raises`, có assert ngữ cảnh đã đặt. `created_by` bắt `CheckViolation` + `constraint_name = xt_immutable_system_columns`. Test đếm field theo dữ liệu gói | — |
| Deploy | Cấu hình `log_*`, `auto_explain` và mức role ghi ở `docs/model-engine.md` §8 và `docs/integration-notes.md` | — |

**Kết quả chạy lại:**
- `tests/unit/model`: 160 passed.
- `tests/integration/test_model_engine_pg.py` + `tests/integration/model_audit`: 44 passed, chỉ một container Postgres mỗi lần, đã dọn.

**Còn lại:**
- **Rủi ro chấp nhận:** migrator vẫn sửa được *dữ liệu*. Mọi lần sửa đều đi qua trigger audit và để lại event với actor NULL; migrator không tắt được trigger này.
- **Chưa làm:** audit cho bảng `xt_meta.*` (I-AUD-META) cần một hàm riêng của `xt_audit_owner`.

## Vòng 3 (16/10/2026, Opus tester)

**Cách làm:**
- Không sửa `src`.
- Một container `postgres:17-alpine`, tên và cổng ngẫu nhiên, dọn bằng `docker rm -f -v`. Có hai CSDL:
  - `xt`: cài mới bằng mã hiện tại (bootstrap + migrate 0001–0015).
  - `xt_up`: CSDL nâng cấp thật. Bootstrap, migrate 0001–0004, tạo tenant và áp catalog bằng mã commit `98e1a1b` (lấy qua `git archive`), sau đó chạy `install_meta_audit` + tạo `xt_job` + migrate bằng mã hiện tại, đúng như `init_db.py`.
- Test mới: `tests/integration/model_audit_r3/test_model_engine_audit_r3_pg.py`, gồm 18 test phòng thủ và 17 xfail strict.

### 1. Kiểm thử chạy lại

| Kiểm thử | Kết quả |
|---|---|
| `uv run pytest tests/unit/model` | 160 passed |
| `tests/integration/test_model_engine_pg.py` + `tests/integration/model_audit` | 49 passed, **1 failed**: `test_audit_meta_pg.py::test_fresh_install_attaches_meta_audit_to_all_tables`. Migration 0014 gắn audit cho `notification`/`telegram_link` nhưng `META_TABLES` không được cập nhật (test cũ, không phải lỗi engine) |
| `tests/integration/model_audit_r3` (mới) | 18 passed, 17 xfailed (strict) |

### 2. Soát test của auditor

- `git log -p tests/unit/model/audit tests/integration/model_audit`: `test_model_engine_audit_pg.py` và `tests/unit/model/audit/` không đổi kể từ `98e1a1b`. Các sửa của vòng 1/2 bị gộp chung vào commit đó nên không diff được bản gốc của tester.
- Nội dung hiện tại **không bị làm yếu**:
  - `ctx()` đã nằm ngoài `pytest.raises` và có assert ngữ cảnh.
  - `created_by` bắt `CheckViolation` + `constraint_name`.
  - Các test R2 có mặt.
- Lần sửa duy nhất sau đó là `e531425`: thêm 3 bảng vào `META_TABLES` của `test_audit_meta_pg.py`.
- Điểm yếu còn lại (LOW):
  - `test_fresh_install_…` chỉ so trong danh sách cứng. Bảng `xt_meta` mới không có audit sẽ không bao giờ làm test đỏ, xem R3-06.
  - `test_r2_03` chỉ kiểm `done >= 1`, chưa kiểm "chạy đúng một lần". Vòng 3 đã bổ sung kiểm `attempts = 1` cho mọi job.
  - `test_dead_post_job_…` chấp nhận mọi `ModelError`.

### 3. Bảng PASS/FAIL vòng 3

| # | Mục | KQ | Bằng chứng |
|---|---|---|---|
| 1 | Nâng cấp 98e1a1b → hiện tại | PASS | Ledger `schema_migration` của `xt_up` giống `xt`. Hai event trigger `O`. Migrator, `xt_app` và `xt_job` không có EXECUTE trên `log`/`append`/`log_truncate`/`log_meta`/`log_meta_truncate`. Migrator không đọc được `pepper`. Mọi bảng `xt_audit` thuộc `xt_audit_owner` |
| 2 | Migrator tấn công trực tiếp: DISABLE/ENABLE REPLICA/ALWAYS/RENAME/DROP trigger, RULE, trigger lạ, CONSTRAINT TRIGGER, gọi `append`/`log`, gắn `log()` vào bảng tự tạo, đọc `pepper`/`head`, sửa `event`, thay `verify_anchor`, tắt/xóa event trigger, `session_replication_role`, tắt/xóa trigger `xt_audit_meta`, RULE trên `pii_egress` | PASS | 26 tấn công × 3 đích (tenant mới ở `xt`, tenant mới ở `xt_up`, tenant **cũ** do mã 98e1a1b tạo): đều 42501/0A000/P0001. Trigger giữ nguyên `xt_audit`, `xt_audit_truncate`, `xt_touch` ở trạng thái `O` |
| 3 | Đọc khóa HMAC qua policy "gián điệp" | PASS | Policy chỉ thấy `xt.audit_hmac`, không có `xt.audit_key`, khóa hay giá trị (cả 3 đích) |
| 4 | **R3-01** Migrator ghi dòng model mà không có event | **FAIL (HIGH)** | 4 cách, trên cả `xt` lẫn `xt_up` (xem §4). Dòng hiện ra qua `RecordService.search` nhưng có 0 event |
| 5 | **R3-02** Giả `actor_id`/`effective_actor`/`client_azp`/`trace_id` trong event | **FAIL (MEDIUM)** | (a) Migrator là chủ `xt_meta.session_ctx` nên tự INSERT ngữ cảnh rồi ghi: event mang actor giả, không phải NULL. (b) Migrator là chủ `xt.actor()`: `CREATE OR REPLACE` làm mọi event của **`xt_app`** mang actor giả |
| 6 | **R3-03** G-M2 qua `xt_audit.install` | **FAIL (MEDIUM)** | `SELECT xt_audit.install('xt_tN','emp','{}')` của migrator gỡ cột sensitive khỏi trigger. Lần ghi lương tiếp theo của app nằm **plaintext** trong `xt_audit.event` (bất biến, không xóa được) |
| 7 | **R3-04** Event giả trong lịch sử bản ghi thật | **FAIL (MEDIUM)** | Đổi tên `emp`, tạo bảng `emp` giả, `install`, INSERT/DELETE với `id` của bản ghi thật. Lịch sử bản ghi thành `[INSERT, INSERT, DELETE]` với `model='emp'`, `verify` vẫn toàn vẹn |
| 8 | Checkpoint: superuser xóa đuôi + sửa `head` | PASS | DB `verify` báo intact, còn checkpoint báo `missing`. Ghi thêm event sau đó vẫn là `missing` |
| 9 | **R3-05** Checkpoint khi superuser thay luôn hàm kiểm | **FAIL (MEDIUM)** | Xóa đuôi + `CREATE OR REPLACE xt_audit.verify_anchor` trả `ok` thì `verify_against_checkpoint` báo `ok`. Việc kiểm chạy bằng hàm SECURITY DEFINER **trong** DB do chính đối thủ (superuser) kiểm soát |
| 10 | Pha 2: giết backend giữa `CREATE UNIQUE INDEX CONCURRENTLY` | PASS | Job còn `pending`, index INVALID còn lại. `apply` vN+1 → `post_pending`, `reconcile` → `pending`. `run_jobs` xóa INVALID rồi dựng lại (done = 1), sau đó `reconcile` → `validated` và apply được |
| 11 | Pha 2: job dead (trùng unique) | PASS | `reconcile` → `failed`, không còn index INVALID, `apply` bị chặn. Sửa dữ liệu + `retry_failed` → `validated` |
| 12 | 4 worker `run_jobs` song song (2 tenant × 2 index CONCURRENTLY, mỗi worker một instance/kết nối) | PASS | Mọi job `done` với `attempts = 1` (đúng một lần), 0 failed/dead, 4 index hợp lệ |
| 13 | Regex: ngữ nghĩa Rust ↔ PG | PASS | Fuzz 30.000 pattern ngẫu nhiên (seed cố định): 9.778 pattern qua `check_regex` × 36 chuỗi mẫu, **0** khác biệt khớp/không khớp |
| 14 | **R3-07** Regex Rust nhận nhưng PG không biên dịch | **FAIL (LOW)** | 585/9.778 pattern (6 %), ví dụ `^[A-Z]{2}{2}$`, `a+{1,3}`, `^{2}a`, `^a$+`, PG báo "quantifier operand invalid". Diễn tập ném `StoreError` thô (lên `internal_error`) thay vì BLOCKED/`invalid_request`. Fail-closed, không áp gì |
| 15 | Audit metadata cho bảng của 0007–0015 | PASS + INFO | Có audit: `file_object` (cột `name` HMAC), `file_event` (0009), `decision` (0010), `notification`, `telegram_link` (0014). Loại trừ hợp lý: `pii_token_source`/`pii_token_seen`/`pii_reveal_ticket` (0007, mỗi lần xem đã ghi `pii_egress` có audit), `workflow_timer_fire` (0012, sổ idempotency), `xt_identity.dpop_jti` (0013, cache replay tạm, không có tenant), `notification_request`/`notification_delivery`/`telegram_link_code`/`telegram_update` (0014), `notification_source` (0015) |
| 16 | **R3-06** Bảng quyền/chính sách không có audit | **FAIL (MEDIUM)** | Ở cả `xt` và `xt_up`: `person` (roles, `limit_amount`, unit/manager: nền của RBAC/SoD), `agent_grant` (autonomy, roles của agent), `tenant_profile` (`llm_egress` masked/local_only), `org_unit` (`head`), `login_tenant` (role của login BI), `catalog_version` (migrator UPDATE được `spec`), `tenant`. I-AUD-META 0006 chỉ phủ 8 bảng |
| 17 | Login BI: thoát ngữ cảnh | PASS | 13 lệnh bị từ chối: `set_context` 8 tham số và bản jsonb, INSERT `session_ctx`, `backend_cur()`, đọc `login_tenant`, `api_t<B>`, `xt_t<A>` trực tiếp, `xt_audit.event`, `SET ROLE xt_app/xt_authenticator`, `SET SESSION AUTHORIZATION`, TEMP, CREATE FUNCTION. Tự đặt `xt.tenant_id`/`xt.pgrst_ctx`/`xt.roles`/`xt.actor_id` → `xt.tenant()` vẫn A, `has_role('admin')` false. Không CONNECT được CSDL khác |
| 18 | Ngữ cảnh `xt_app` qua transaction/role | PASS | Transaction sau trên cùng kết nối: `xt.tenant()` NULL. `SET LOCAL ROLE xt_workflow` không thấy tenant B và không gọi được `set_context` |
| 19 | Hạ tầng test | **FAIL (LOW)** | `tests/integration/pg.py` và hai file trong `model_audit/` dùng `docker run --rm` + `docker rm -f` (không `-v`). Đã thử thực nghiệm: cách này **để lại volume ẩn danh** (mỗi lượt 3 volume), trái quy tắc CLAUDE.md |

### 4. Chi tiết lỗi mới

**R3-01 (HIGH): `ddl_guard` chỉ xét bảng đang nằm trong `xt_t<seq>` lúc lệnh kết thúc, và chỉ cấm trigger lạ, không bắt buộc phải có trigger audit.** Bốn cách migrator ghi dữ liệu model mà không để lại event:

1. **`set_schema`:** `ALTER TABLE xt_tN.emp SET SCHEMA xt_meta` → `DISABLE TRIGGER xt_audit` → INSERT → ENABLE → dời bảng về.
2. **`rename_schema`:** `ALTER SCHEMA xt_tN RENAME TO x` → DISABLE → đổi tên lại. `pg_namespace` không bị kiểm, nên trigger **giữ trạng thái tắt** ngay trong `xt_tN`. Từ đó mọi lần ghi, kể cả của `xt_app`, đều không có audit.
3. **`swap_table`:** `CREATE TABLE xt_tN.emp_r3 (LIKE emp INCLUDING ALL)`, đổi tên tráo vào `emp`. Bảng không có trigger và không có RLS nên còn mất luôn cô lập tenant với `xt_app` nếu được GRANT.
4. **`inherit_child`:** `CREATE TABLE xt_meta.kid () INHERITS (xt_tN.emp)`. Dòng hiện qua `emp` nhưng không kích trigger của `emp`.

Điều này trái với quyết định "migrator không tin cậy với audit" và với câu "Rủi ro chấp nhận" ở phản hồi vòng 2.

Hướng sửa:
- Bắt `ALTER SCHEMA` trên `xt_t*`.
- Từ chối bảng đã mang trigger `xt_audit` mà nằm ngoài `xt_t*`.
- Bắt buộc mọi bảng thường trong `xt_t*` có `xt_audit` + `xt_audit_truncate`.
- Cấm `INHERITS`/`ATTACH PARTITION` với cha nằm trong `xt_t*` (kiểm `pg_inherits`).
- Tốt nhất: bảng và schema `xt_t*` thuộc một role NOLOGIN; migrator chỉ `SET ROLE` sang role đó trong `PgMigrator.apply`, và event trigger chặn mọi DDL khác.

**R3-02 (MEDIUM):** `xt_meta.session_ctx`, `login_tenant` và các hàm `xt.cur`/`backend_cur`/`cur_meta`/`actor`/`tenant`/`has_role` đều thuộc `xt_migrator`. Hệ quả:
- Migrator giả được actor trong audit.
- Migrator đổi được `xt.cur()`, và qua đó đổi RLS của BI, `xt_web` và `xt_app` (cho họ đọc chéo tenant). Đây là năng lực vượt quá BYPASSRLS của chính nó.

Hướng sửa: chuyển sở hữu sang role NOLOGIN không cấp cho migrator, và event trigger chặn `CREATE OR REPLACE` trên các hàm này.

**R3-03 (MEDIUM):** `install()` tin danh sách cột sensitive do migrator đưa vào.

Hướng sửa: `xt_audit_owner` giữ registry riêng cho cột sensitive theo (tenant, bảng, cột). Registry chỉ được thu hẹp qua một đường đã duyệt; `install` hợp danh sách truyền vào với registry chứ không thay thế.

**R3-04 (MEDIUM):** event chỉ ghi `TG_TABLE_NAME`.

Hướng sửa: `install` ghi oid bảng vào registry; `log()` từ chối nếu `TG_RELID` không khớp.

**R3-05 (MEDIUM):** `verify_against_checkpoint` phải tự tính lại chuỗi SHA-256 ở client từ các dòng `xt_audit.event`, theo đúng `canon`, thay vì gọi `verify_anchor`/`verify` trong DB. Cách khác, ít nhất là so `prosrc` của các hàm audit với digest ghi trong checkpoint.

**R3-06 (MEDIUM):** gắn `install_meta` cho `person`, `agent_grant`, `tenant_profile`, `org_unit`. Với `login_tenant` (khóa kiểu text) và `catalog_version` (`tenant_seq`) cần một biến thể của `install_meta`: suy `record_id` bằng uuid v5 và `tenant_id` qua `xt_meta.tenant`. Sửa `test_audit_meta_pg` thành kiểm "mọi bảng `xt_meta` hoặc có audit hoặc nằm trong allowlist có lý do".

**R3-07 (LOW):**
- `check_regex` phải cấm lượng từ đứng ngay sau lượng từ, và lượng từ sau `^`, `$`, `(`, `|`.
- `migrator.plan` phải dịch `InvalidRegularExpression` thành BLOCKED hoặc `invalid_request`.

### 5. Kết luận vòng 3

**CHƯA ĐẠT** cho mục tiêu của quyết định chủ dự án ("`xt_migrator` không được tin cậy với audit") và cho neo checkpoint trước superuser.

Đã đóng và kiểm thực nghiệm:
- R2-01 (khóa không vào DB).
- R2-02 và R2-03 (pha 2: kill, dead, 4 worker song song đều đúng một lần).
- R2-05 (0 khác biệt ngữ nghĩa trên gần 10.000 pattern).
- Phần trực tiếp của R2-04 (26 tấn công × 3 đích, kể cả CSDL nâng cấp).
- AUD-13 (BI/`set_context`).

Còn mở:
- **R3-01 (HIGH):** né trigger audit bằng DDL ngoài tầm event trigger.
- **R3-02…R3-06 (MEDIUM):** giả actor, plaintext qua `install`, event giả, checkpoint tin hàm trong DB, bảng quyền chưa có audit.
- R3-07 và hạ tầng test (LOW).

Lõi model engine (G-M1, RLS/BI, pha 2, regex) **ĐẠT**. Container `xt-audit-r3-model-*` đã xóa cùng volume.

## Phản hồi tác giả, vòng 3 (16/10/2026)

| Mã | Cách sửa (`src/xt/adapters/db/sql/audit_meta.sql`, phiên bản 2, superuser, idempotent) |
|---|---|
| R3-01 | `ddl_guard` (event trigger SECURITY DEFINER của superuser) có 4 cơ chế: (a) mọi bảng thường trong `xt_t<seq>` **tự được gắn** trigger audit khi CREATE TABLE/ALTER TABLE, nên bảng tráo tên (`LIKE … INCLUDING ALL`) vẫn có audit; (b) bảng mang trigger `xt_audit` không được rời schema tenant (chặn `SET SCHEMA`); (c) `ALTER SCHEMA` (đổi tên/chủ) trên schema chứa bảng có audit hoặc trên schema nền tảng bị chặn với role không phải superuser; (d) cấm `INHERITS`/`ATTACH PARTITION` dính tới bảng của `xt_t*` hoặc bảng đã đăng ký, cấm bảng partition trong `xt_t*`. Bảng metadata có audit không được đổi tên/dời schema/xóa (trừ superuser) |
| R3-02 | Actor/trace của event lấy từ `xt_audit.session_ctx`. Bảng này chỉ `xt.set_context` ghi; hàm đã chuyển chủ sang `xt_audit_owner`, và chỉ superuser được CREATE/ALTER/DROP nó cùng mọi hàm `xt_audit.*` (event trigger chặn). Migrator tự INSERT `xt_meta.session_ctx` thì event của nó vẫn mang actor NULL. Thay `xt.actor()` không đổi được actor trong audit |
| R3-03 | Registry cột sensitive theo `(relid, attnum)` (`xt_audit.sensitive`, sống qua rename cột). `install` chỉ thêm vào registry; cột bị bỏ khỏi danh sách chuyển `inactive`, vẫn HMAC nếu app cấp, không thì lưu `"redacted"`, không bao giờ plaintext. Hạ nhãn thật sự cần superuser gọi `xt_audit.declassify(bảng, cột)` |
| R3-04 | `rel_registry` (oid bảng). `log()`/`log_meta()` từ chối bảng chưa đăng ký. Event có cột `relid`, và `append` từ chối event cho một `record_id` đã thuộc bảng khác. Khóa meta không phải `id` được băm theo tên bảng để tránh trùng giữa các bảng |
| R3-05 | `verify_against_checkpoint` đọc dòng thô (`xt_audit.chain`), tự tính lại SHA-256 theo `canon` ở client và so với đầu chuỗi đã ký. Checkpoint còn ghi digest của mọi hàm `xt_audit.*` + `xt.set_context`, đọc thẳng từ `pg_proc`; digest khác thì trạng thái là `functions_changed` |
| R3-06 | Migration `0016_audit_meta_permissions.sql` gắn audit cho `person`, `agent_grant`, `tenant_profile`, `org_unit`, `login_tenant`, `catalog_version` (tenant theo `seq:`) và `tenant` (`self:`). Hai biến thể mới đi kèm: `install_meta2(bảng, cột_khóa[], redact[], tenant_col)` cho khóa ghép hoặc khóa không phải uuid, và `xt_audit.install_meta` giữ chữ ký cũ để tương thích |
| R3-07 | `check_regex` cấm lượng từ không có toán hạng hợp lệ: ở đầu pattern, sau `( | ^ $`, hoặc ngay sau lượng từ khác (`{2}{2}`, `a+{1,3}`). `plan` dịch `InvalidRegularExpression` thành `invalid_request` |
| Hạ tầng test | `tests/integration/pg.py` và 2 fixture trong `model_audit/` dùng `docker rm -f -v` và bỏ `--rm`. Đã dọn 22 volume ẩn danh sót trong ngày. `test_audit_meta_pg` kiểm theo catalog thật với danh sách miễn có lý do (`xt.adapters.db.audit.META_AUDIT_EXEMPT`) |

**Sửa test của tester:**
- Gỡ toàn bộ xfail.
- `test_pg_rejected_regex_is_blocked_at_rehearsal_not_500`: spec giờ bị chặn ngay ở validate (`ValueError`), trước cả diễn tập. Đây là yêu cầu "reject at validation".
- `test_meta_audit_inventory`: sau R3-06 khẳng định `gaps == []`.
- `composition/test_fixtures.py::test_rls_two_tenants_fail_closed`: bảng thử nghiệm trong `xt_t<seq>` dùng `id uuid`, vì mọi bảng trong schema tenant giờ luôn có audit.

**Kết quả:**
- `tests/unit/model`: 166 passed.
- `test_model_engine_pg` + `model_audit` + `model_audit_r3` (cài mới và nâng cấp từ 98e1a1b): 85 passed.
- `composition`: 59/60. Lỗi còn lại `test_pack_ops::test_start_process_scans_refs_nested_in_object_inputs` thuộc phần đang sửa dở của agent khác (input schema tool), không liên quan audit.

**Còn chấp nhận theo quyết định chủ dự án** ("migrator tin cậy với schema"):
- Migrator vẫn sửa được hàm `xt.cur()` và qua đó sửa RLS. Đó là quyền schema, không phải audit; mọi lần ghi dữ liệu vẫn có event.
- Superuser kiểm soát mọi đường đọc của verifier. Việc kiểm phía client cộng digest hàm phát hiện được việc thay hàm kiểm và tính lại chuỗi, nhưng không phát hiện được superuser giả cả `pg_proc`.
