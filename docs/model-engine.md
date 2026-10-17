<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->
# Model engine

Model nghiệp vụ là **dữ liệu**: agent soạn spec JSON, người duyệt, engine áp an toàn vào Postgres theo từng tenant. Nguồn thiết kế: `docs/architecture-v2.md` (D5, D6, §3.3(c), §5.3, §5.6, §6.1, §6.3, §9.1), `docs/tool-contracts-v1.md` (§3, §5.2, §5.17, §8), `research/spike-model-engine.md`, `research/spike-model-tenancy.md`.

## 1. Bố cục mã (theo lớp import-linter)

| Lớp | Module | Vai trò |
|---|---|---|
| domain | `xt.domain.model.ident` | Whitelist định danh (snake_case ASCII ≤ 40, không từ khóa PG, không cột hệ thống), id ổn định, tên role |
| domain | `xt.domain.model.expr` | DSL `Cmp/And/Or/Not` (≤ 8 cấp, `in` ≤ 100), kiểm kiểu, logic 3 trị, parser chuỗi `rule.expr` của TCV1 (viết tay, không regex, không eval) |
| domain | `xt.domain.model.spec` | `FieldSpec/ModelSpec/Catalog` (pydantic `extra=forbid`), regex, gợi ý DLCN, đọc JSON chặt, xuất JSON Schema |
| domain | `xt.domain.model.checks` | Ràng buộc và tên constraint/index/policy theo id ổn định, `constraint_map` để dịch lỗi DB |
| domain | `xt.domain.model.plan` | Diff theo id → `Change` đã phân loại, `Probe` dữ liệu, `finalize`, `plan_hash`, `check_gate` |
| domain | `xt.domain.model.values` | Payload CRUD sinh lúc chạy, dịch lỗi pydantic sang tiếng Việt, chặn trường workflow, rule 3 trị |
| contracts | `xt.contracts.model` | `describe_model`, input/output `propose_model_change` đúng TCV1 |
| application | `xt.application.model_engine` | `RecordService` (CRUD), `ModelService` (describe/propose/duyệt-áp/rollout), `operations` (ops TCV1 → catalog), port |
| adapters | `xt.adapters.db` | Bootstrap SQL, compiler DDL, migrator 2 pha, catalog/proposal store, CRUD repo, audit, kết nối |

Chỉ `xt.adapters.db` (và `xt.adapters.queue` cho hàng đợi) được import `psycopg` (contract import-linter).

## 2. Spec

- **Chỉ JSON.** `parse_catalog_json` dùng `json.loads` với `object_pairs_hook` từ chối khóa trùng, cấm `NaN/Infinity`, số thực đọc thành `Decimal`. YAML bị từ chối (bẫy YAML 1.1 `no` → `False`).
- 11 kiểu: `text, number (numeric), money (bigint VND), date, datetime (bắt buộc múi giờ), bool, enum (text + CHECK IN), ref (uuid + FK ghép (tenant_id, col)), user, file, json (jsonb ≤ 64 KB)`.
- Mọi model/field/index/rule có `id` ổn định (`[a-z0-9][a-z0-9_]{0,23}`). Rename giữ id nên planner sinh `RENAME` thật.
- **Regex:** chỉ nhận pattern thỏa đủ ba điều kiện:
  - Engine Rust của pydantic-core biên dịch được (không backreference/lookaround, nên không ReDoS).
  - Nằm trong tập con mà hai engine hiểu giống nhau: escape chỉ dùng cho ký tự đặc biệt (cấm `\d \w \s \b \x41 \u…`), cấm lớp POSIX `[[:alpha:]]`, cấm cờ/nhóm có tên `(?…)` (trừ `(?:`), lặp tối đa 255, cấm lazy/possessive.
  - Không lặp một nhóm có lượng từ không giới hạn bên trong (cấm `(a+)+`, `([a-z]*){200}`).
- **Trần:** spec JSON ≤ 1 MiB; ≤ 3.000 field mỗi catalog. Nhãn, thông điệp, mô tả và giá trị enum cấm ký tự Unicode Cc/Cf/Zl/Zp (bidi, zero-width, tách dòng). Kiểm bằng duyệt ký tự, không đưa pattern vào `re` của Python. Test vi sai Rust ↔ PG `~` chạy trên mọi regex của 3 gói.
- **G-M1** `managed_by: "workflow"`: trường không có trong payload `create/update`; trường managed + required phải có `default`.
- **G-M2** `pii: none | basic | sensitive`: thiếu thì engine gợi ý theo NĐ 356/2025 (tên + nhãn tiếng Việt) và đặt `pii_suggested: true` để người duyệt xác nhận. Catalog luôn lưu tường minh.
- JSON Schema: `catalog_json_schema()` / `model_spec_json_schema()` (Draft 2020-12, `additionalProperties: false`).

## 3. Postgres (bố trí B2, D6)

- Mỗi tenant hai schema: `xt_t<seq>` (bảng, chủ `xt_migrator`) và `api_t<seq>` (view, chủ `xt_api_owner`). Bảng vẫn có `tenant_id` + policy RESTRICTIVE theo tenant + PERMISSIVE theo role/scope, `ENABLE` + `FORCE ROW LEVEL SECURITY`.
- View `api_t<seq>.<model>`: `security_invoker = false`, chủ `xt_api_owner` (NOBYPASSRLS), **không** chứa field `sensitive`, `GRANT SELECT` cho `xt_web` và login BI của đúng tenant.
- Role (bootstrap `src/xt/adapters/db/sql/bootstrap.sql`): `xt_migrator` (BYPASSRLS, chạy DDL đã duyệt), `xt_app` (NOBYPASSRLS, runtime), `xt_workflow` (NOLOGIN; `xt_app` chỉ `SET ROLE`, không INHERIT), `xt_audit_owner`, `xt_api_owner`, `xt_web` (không là thành viên `xt_app`). Mật khẩu và khóa HMAC truyền vào `bootstrap()` từ secret, không sinh/hard-code trong mã.
- **Quyền cột (G-M1):** `xt_app` chỉ `INSERT/UPDATE (cột tự do)`; `xt_workflow` UPDATE cả bảng; đường transition dùng `Database.tenant_tx(workflow=True)` → `SET LOCAL ROLE xt_workflow`, vẫn chịu RLS.
- **Ngữ cảnh RBAC (không đặt được từ client, AUD-13):**
  - Mỗi lời gọi là một transaction thật. Backend gọi `xt.set_context(tenant, actor, roles[], teams[], depts[], effective_actor, azp, trace)`. Hàm này là SECURITY DEFINER, chỉ `xt_app` được EXECUTE.
  - Ngữ cảnh được ghi vào `xt_meta.session_ctx`, khóa theo `(pg_backend_pid(), pg_current_xact_id())`, nên hết hiệu lực khi transaction kết thúc.
  - Policy đọc `xt.cur()`, **không** đọc GUC. Client có `set_config('xt.roles', …)` cũng không có tác dụng. Không có ngữ cảnh thì thấy 0 dòng.
  - Pool `autocommit=True`, có `max_lifetime`, tắt prepared statement phía server (DDL đổi kiểu kết quả `SELECT *`), từ chối kết nối không IDLE (bẫy psycopg, SMT §1).
- **Login BI:** `bind_bi_login(m, login, tenant, roles=…, actor_id=…, team_ids=…, department_ids=…)` ghi tenant, role và phạm vi CỐ ĐỊNH theo `session_user`. Login chỉ có quyền `api_t<seq>` của đúng tenant đó. Không cấp role thì không thấy gì.
- **Audit:**
  - `xt_audit.event` append-only.
  - Chuỗi hash theo tenant, nối qua `xt_audit.head ... FOR UPDATE`. Ở REPEATABLE READ, transaction đến sau phải thử lại thay vì làm chuỗi rẽ nhánh.
  - `xt_audit.verify(tenant) → (n, first_bad_seq)` chỉ phát hiện sửa đổi ở giữa chuỗi. Superuser xóa đuôi chuỗi hoặc tính lại toàn bộ hash thì không phát hiện được (neo Merkle ở v1.0).
  - Trigger audit được sinh lại từ catalog cho mọi model bị chạm trong cùng migration, nên rename field sensitive vẫn chỉ lưu HMAC.
- **Mô hình đe dọa của audit (quyết định 16/10/2026, `docs/decisions.md`):** `xt_migrator` được tin để đổi schema, KHÔNG được tin với audit.
  - Bảng, hàm và trigger audit thuộc `xt_audit_owner` (NOLOGIN). Trigger được cài qua `xt_audit.install(schema, bảng, cột_sensitive[])` (SECURITY DEFINER, `CREATE OR REPLACE TRIGGER`). Migrator chỉ được gọi `install`, không có EXECUTE trên `xt_audit.log`/`append` nên không gắn được trigger audit vào bảng tự tạo để giả event.
  - `xt_audit.log()` kiểm tên trigger và schema `xt_t<seq>`, đồng thời `tenant_id` của dòng phải đúng tenant của schema.
  - Event trigger của superuser (`xt_audit_ddl_guard`, `xt_audit_drop_guard`) giữ cho bảng `xt_t<seq>.*` chỉ có `xt_touch` (`xt.row_touch`) và `xt_audit`/`xt_audit_truncate` (`xt_audit.*`), luôn bật. Các thao tác sau đều bị chặn: `DISABLE TRIGGER`, `DROP TRIGGER xt_audit` ngoài `xt_audit_owner`, trigger lạ và `CREATE RULE`. `TRUNCATE` được ghi thành event riêng.
- **Vòng 3 (R3-01…07):**
  - Mọi bảng thường trong `xt_t<seq>` luôn có trigger audit (event trigger tự gắn). Bị chặn: SET SCHEMA, ALTER SCHEMA, INHERITS/partition, RULE.
  - Actor của event lấy từ `xt_audit.session_ctx`; chỉ `xt.set_context` (chủ `xt_audit_owner`) ghi được.
  - Registry cột sensitive chỉ được mở rộng; hạ nhãn thật sự cần superuser gọi `xt_audit.declassify`.
  - Mỗi `record_id` chỉ thuộc một bảng.
  - Checkpoint tính lại chuỗi ở client và kèm digest hàm audit.
  - Bảng quyền, danh tính và catalog có audit (migration 0016).
- **Audit bảng metadata `xt_meta.*` (I-AUD-META):** `xt_audit.install_meta(bảng, cột_khóa, redact[])` gắn trigger `xt_audit_meta` thuộc `xt_audit_owner`. Event nối vào chuỗi của tenant, `model = 'xt_meta.<bảng>'`; `TRUNCATE` nối vào chuỗi nền tảng (tenant `00000000-…`). Checkpoint ngoài DB phủ luôn metadata. Migration `0006_audit_meta.sql` gắn audit cho các bảng chính sách, định nghĩa quy trình, gói, PII egress và receipt. CSDL cũ chạy `install_meta_audit(superuser)` trước. Chi tiết: `docs/integration-notes.md`.
- **Khóa HMAC không bao giờ vào Postgres:**
  - xt-api/xt-worker giữ `AuditKey(kid, key)` từ secret và tính HMAC trong tiến trình (`xt.domain.model.audit`).
  - Chỉ transaction **có ghi field sensitive** mới nhận chuỗi HMAC, qua GUC `xt.audit_hmac = {"new":{cột: "hmac:kid:hex"}, "old":{…}}`. Giá trị cũ được đọc `FOR UPDATE` trong cùng transaction.
  - Mã do migrator kiểm soát (policy, hàm `xt.*`, default, CHECK) chạy trong transaction đó cũng chỉ thấy HMAC (đã có test policy "gián điệp").
  - Trigger từ chối ghi khi thiếu HMAC hoặc kid không phải kid đang dùng.
  - `xt_audit.pepper` chỉ giữ `sha256(khóa)` theo kid, chỉ superuser ghi được (`bootstrap`, `rotate_audit_key`).
  - `verify_hmac(key, kid, giá_trị, chuỗi_lưu)` kiểm theo dạng chuẩn hóa: Decimal bỏ số 0 thừa, datetime quy về UTC.
- **Neo chuỗi audit ra ngoài DB:**
  - `xt audit checkpoint` đọc `xt_audit.heads()`, ký Ed25519 rồi ghi tệp `audit-checkpoint-<UTC>.json` vào `XT_AUDIT_CHECKPOINT_DIR`. Thư mục này phải nằm ngoài Postgres (ổ gắn kho đối tượng/WORM); tệp ghi một lần, không ghi đè.
  - `xt audit verify --against-checkpoint latest|<tệp>` kiểm chữ ký và đối chiếu từng đầu chuỗi: `missing` = đuôi chuỗi bị xóa, `mismatch` = chuỗi bị tính lại, `broken` = sửa giữa chuỗi. Mã thoát 0/1/2.
  - Worker phải chạy `checkpoint` định kỳ (đề xuất mỗi giờ).
- **Trần:** 200 tenant mỗi CSDL (`provision_tenant`), 6.000 bảng model mỗi CSDL (kiểm khi plan có `add_model`) → `resource_constraint`.
- Xóa tenant: `drop_tenant()` = `DROP SCHEMA api_t<seq>, xt_t<seq> CASCADE` + xóa dòng lõi; audit không bị đụng.

## 4. Planner và apply

| Hạng | Ví dụ |
|---|---|
| SAFE | thêm model, thêm trường cho phép trống, đổi nhãn/mặc định, nới min/max, mở rộng enum, bỏ bắt buộc/unique, tăng nhãn DLCN, thu hẹp quyền |
| RISKY | trường bắt buộc có default, siết ràng buộc khi 0 vi phạm, unique/index unique khi 0 trùng, mở rộng quyền, **hạ nhãn DLCN**, **bỏ `managed_by`** |
| DESTRUCTIVE | xóa trường/model, đổi tên trường/model (theo id ổn định) — cần xác nhận từng mã |
| BLOCKED | vi phạm > 0, trường bắt buộc không default trên bảng có dữ liệu, đổi kiểu, hoán đổi tên, diễn tập DDL lỗi |

- **`PgMigrator.plan(rehearse=True)`:**
  - Probe trên dữ liệu hiện tại: tên cột cũ, trường mới thay bằng default/NULL.
  - Có `statement_timeout` 5 s.
  - Diễn tập **chỉ các bước DDL** `tx` trong transaction `force_rollback`. Không bao giờ chạy backfill hay sửa dữ liệu.
  - Khóa bận khi diễn tập báo `timeout`, không thành BLOCKED.
- **Backfill** (đặt bắt buộc có default) là job pha 2 loại `backfill`: chạy theo lô 1.000 dòng, mỗi lô một transaction ngắn, tiến độ ghi ở `xt_meta.apply_job.progress`. Sau đó mới `ADD CONSTRAINT … NOT VALID` → `VALIDATE` → `SET NOT NULL`.
- **Worker pha 2:** mỗi tenant chỉ một worker xử lý tại một thời điểm (`pg_try_advisory_lock('xt_meta:jobs:<seq>')`, R2-03). Job chạy theo (version, ord); lỗi thì dừng tenant đó.
- **Lệch catalog ↔ CSDL:** `apply` từ chối khi BẤT KỲ job pha 2 nào của tenant (mọi version) chưa `done`: `post_pending` hoặc `post_failed` (R2-02).
  - Job chết thì xóa index INVALID và đặt `post_state=failed`.
  - `apply` từ chối version mới (`post_failed`) cho tới khi quản trị sửa dữ liệu rồi gọi `retry_failed(tenant)`.
  - `reconcile()` (gọi lúc khởi động worker) đối chiếu index INVALID, constraint NOT VALID và job dead với catalog.
- `apply` pha 1 (một transaction/tenant): advisory lock `xt_meta:catalog:<seq>`, `lock_timeout`, `statement_timeout`, **lập lại kế hoạch**, `check_gate` (mã kế hoạch, BLOCKED, xác nhận DESTRUCTIVE), DDL với CHECK/FK `NOT VALID`, ghi `xt_meta.catalog_version`, enqueue job pha 2 cùng transaction, `NOTIFY pgrst` + `xt_catalog`.
- `run_jobs` pha 2: `VALIDATE CONSTRAINT` (transaction ngắn), `CREATE/DROP INDEX CONCURRENTLY` (autocommit), `lock_timeout` 5 s > `deadlock_timeout` 1 s, retry → `dead`, giữ job sau của cùng tenant/version, xóa index INVALID trước khi tạo lại; `catalog_version.post_state = validated | pending | failed`.
- `ModelService.rollout`:
  - Mỗi tenant một transaction. Tenant lỗi hoặc BLOCKED giữ version cũ; lệch phiên bản là hợp lệ (`version_distribution()`).
  - Mặc định chỉ tự áp SAFE. RISKY phải được `allow` tường minh. Mã PHÁ HỦY phải nằm trong `confirm`, không bao giờ tự xác nhận.

## 5. Bề mặt tool

- `describe_model`: field theo TCV1 (`id` = tên, kiểu chiếu sang `string/text/decimal/money/boolean/date/datetime/enum/ref/person/file`), `pii`/`sensitive` boolean (`basic` ⇔ `pii`; `sensitive` ⇔ `pii + sensitive`), kèm `pii_level`, `pii_suggested`, `managed_by`, `workflow_fields`, `revision` = version catalog của tenant, `values_schema` (create) + `update_values_schema`.
- `propose_model_change`: ops `create_model, add_field, update_field, remove_field, add_rule, remove_rule`; `add_state/add_transition/remove_transition/add_metric`, `multiple`, `impact: high`, primitive khác `record` → `capability_unavailable`. `base_revision` lệch → `revision_conflict` (0 = model mới). `dry_run=false` lưu đề xuất bất biến (`xt_meta.model_proposal`, idempotent theo key + fingerprint). Duyệt bằng `approve_and_apply`: người đề xuất không tự duyệt (`access_denied`, reason `sod`).
- CRUD (`RecordService`): `create/get/update/delete/search` + `workflow_write`. Thứ tự: model → quyền → trường workflow → validate → CAS `expected_revision` → rule 3 trị → ghi. Lỗi theo mã TCV1: `invalid_request` (kèm `fields`, gợi ý "có phải ý bạn là…", `valid_fields`), `rule_violation` (unique/CHECK, thông điệp từ `constraint_map`), `precondition_failed` (trường workflow, continuation `transition_record`; xóa bản ghi đang được tham chiếu), `revision_conflict` (`current_revision`), `access_denied`, `not_found`, `timeout`.

## 6. Kiểm thử

```sh
uv run pytest tests/unit/model            # 145 test: spec, 22 tên độc hại × model/field + role/id, planner, Poka-yoke, ops, service
make test-integration                    # gồm tests/integration/test_model_engine_pg.py (postgres:17-alpine, cổng 19700)
```

Integration (`tests/integration/test_model_engine_pg.py` 12 test + `tests/integration/model_audit` 28 test, Postgres 17 thật, container tự xóa):
1. 3 gói (19 model) vào 2 tenant khác phiên bản: A v1 → v2 (13 `add_model` + `grants`), B v1 có thêm trường riêng; 32/32 bảng RLS FORCE, 32 view `security_invoker=false` chủ `xt_api_owner`, view không có field sensitive.
2. Cô lập: quên tenant → 0 dòng; ngữ cảnh A đọc bảng B → 0 dòng; chèn `tenant_id` khác → từ chối; FK chéo tenant → `bad_reference`; `xt_web` không `SET ROLE` sang `xt_app/xt_workflow/xt_api_owner`; login BI gắn A đổi GUC sang B vẫn chỉ thấy A, đọc `api_t<B>` hoặc `xt_t<A>` → `permission denied`.
3. G-M1: CRUD ghi `state` → `precondition_failed`; SQL thô của `xt_app` UPDATE/INSERT cột workflow → `InsufficientPrivilege`, cột tự do → được; `workflow_write` → được.
4. G-M2: `describe_model` có `pii/sensitive`; audit lưu `hmac:1:…` cho lương, `verify_hmac` khớp; hạ nhãn là RISKY.
5. Audit: `xt_app`/`xt_migrator` không sửa được; superuser tắt trigger sửa diff → `verify` trả đúng seq bị sửa, tenant khác vẫn toàn vẹn.
6. Rollout 5 tenant trong lúc có luồng ghi liên tục: tenant 13 bị giữ khóa (lỗi tiêm) → `failed`, tenant 14 có dữ liệu vi phạm → `blocked`, 3 tenant lên v2; retry tenant 13 thành công; 0 constraint NOT VALID, 0 index INVALID.
7. Rename theo id giữ dữ liệu; thiếu xác nhận/sai mã kế hoạch bị chặn. 8. `propose_model_change` đầu-cuối. 9. Regex Rust ↔ PG. 10. Xóa tenant.

## 7. Giới hạn còn lại

- Tiến trình xt-api bị chiếm vẫn `SET ROLE xt_workflow` được (đường transition hợp lệ); cần lint CI chỉ module workflow gọi `workflow_write`.
- `states/transitions/metrics` của TCV1 chưa có trong engine (trả `capability_unavailable`); đổi kiểu trường vẫn BLOCKED (expand/contract sau v1.0).
- Field `basic` vẫn nguyên văn trong audit; neo hash ra ngoài (Merkle) ở v1.0.
- Pre-request PostgREST (`xt.pgrst_pre`) và cấu hình `db_schemas` động chưa nằm trong module này.


## 7a. Mô hình đe dọa của `xt_migrator` (quyết định 16/10/2026: phòng ngừa + phát hiện)

**Cô lập credential — mục tiêu chưa hoàn tất:**
- Mục tiêu thiết kế: chỉ executor đặc quyền nhận mật khẩu `xt_migrator`; API và worker nghiệp vụ chỉ dùng `xt_app`. **Triển khai hiện tại chưa có ranh giới này:** Compose vẫn truyền DSN migrator cho API/worker. Các kiểm thử guard/checkpoint không chứng nhận cô lập credential.
- Không thể chỉ gỡ biến Compose: API hiện cần migrator cho rehearsal/proposal/replan khi duyệt; worker dùng cho `model.apply` và phục hồi pha 2 trong relay. Công việc tiếp theo là tách executor nội bộ nhận tác vụ có kiểu (plan/rehearse/apply/run-jobs), giữ kiểm hash/phê duyệt/audit ở executor, chuyển đọc proposal sang transaction tenant của `xt_app`. Hàng đợi phải tách claim: hiện mọi `xt.dispatch` dùng chung queue `xt`, nên chỉ tách handler vẫn để worker thường nhận nhầm job đặc quyền. One-shot sau lúc khởi động cần launcher tin cậy bên ngoài; executor sống lâu cần quyết định thiết kế cập nhật rõ.
- Tiêu chí đóng: API/worker chạy không có credential migrator; luồng propose → duyệt → apply/pha 2, xác nhận thay đổi phá hủy và từ chối plan cũ vẫn pass; worker thường không claim job đặc quyền; restart/retry executor không áp hai lần; kiểm chứng cấu hình render và mount secret của container thật.
- Lộ credential migrator được coi tương đương lộ DBA: xoay mật khẩu, chạy `xt audit verify --against-checkpoint latest`, điều tra theo danh sách đối tượng mà lệnh in ra.

**Phòng ngừa** (`sql/audit_meta.sql` v4, event trigger của superuser): trigger/RULE trên `xt_meta` chỉ trong allowlist; bảng ngữ cảnh `session_ctx`/`login_tenant` không đổi được; `tenant_id` và policy RESTRICTIVE theo tenant không gỡ được (kể cả CASCADE); mọi hàm `xt`/`xt_audit`/`xt_meta` chỉ superuser đổi (định nghĩa y hệt thì cho chạy lại); biểu thức trên bảng model chỉ dùng hàm `pg_catalog` + `xt.tenant()`/`xt.actor()`; `xt.set_context` chỉ nhận câu `SELECT xt.set_context(...)` gửi thẳng từ client, không có hàm nào khác trên ngăn xếp.

**Phát hiện:** checkpoint (`xt audit checkpoint`) ký Ed25519 gồm đầu chuỗi audit, digest hàm và `security_catalog`: dấu vân tay của schema, hàm, bảng (chủ, RLS, ACL), policy, trigger, rule, constraint, default/generated, cột `tenant_id`/ACL cột, index, event trigger, role `xt_*` và membership. `xt audit verify --against-checkpoint latest` in từng dòng `catalog đổi|thêm|mất: <loại>:<tên>` và trả exit 1 nếu có. Sau mỗi migration/tenant mới đã duyệt, chạy lại `xt audit checkpoint` để lấy mốc mới.

## 8. Cấu hình Postgres khi triển khai (bắt buộc, để không log giá trị nhạy cảm)

Khóa HMAC không bao giờ đi vào Postgres. Tuy vậy, chuỗi HMAC, mật khẩu `ALTER ROLE` của `bootstrap()` và `DETAIL` của lỗi CHECK/UNIQUE (có giá trị sensitive) vẫn có thể bị ghi ra log. Agent deploy áp đúng như sau.

`postgresql.conf` hoặc `ALTER SYSTEM` (superuser), rồi `SELECT pg_reload_conf();`:

```sql
ALTER SYSTEM SET log_statement = 'none';
ALTER SYSTEM SET log_parameter_max_length = 0;
ALTER SYSTEM SET log_parameter_max_length_on_error = 0;
ALTER SYSTEM SET log_error_verbosity = 'terse';
ALTER SYSTEM SET log_min_duration_statement = -1;
ALTER SYSTEM SET log_min_duration_sample = -1;
ALTER SYSTEM SET log_transaction_sample_rate = 0;
-- KHÔNG nạp auto_explain (shared_preload_libraries/session_preload_libraries không chứa auto_explain);
-- nếu buộc phải nạp: ALTER SYSTEM SET auto_explain.log_parameter_max_length = 0;
```

Chốt thêm ở mức role (đều là tham số SUSET nên app không tự đổi được):

```sql
ALTER ROLE xt_app      SET log_statement = 'none';
ALTER ROLE xt_app      SET log_parameter_max_length = 0;
ALTER ROLE xt_app      SET log_parameter_max_length_on_error = 0;
ALTER ROLE xt_app      SET log_error_verbosity = 'terse';
ALTER ROLE xt_migrator SET log_statement = 'none';
ALTER ROLE xt_migrator SET log_parameter_max_length = 0;
ALTER ROLE xt_migrator SET log_parameter_max_length_on_error = 0;
ALTER ROLE xt_migrator SET log_error_verbosity = 'terse';
```

Smoke test sau triển khai (phải đúng cả 4 dòng):

```sql
SHOW log_statement;                       -- none
SHOW log_parameter_max_length;            -- 0
SHOW log_error_verbosity;                 -- terse
SELECT current_setting('shared_preload_libraries') NOT LIKE '%auto_explain%';  -- t
```

`pg_stat_statements` dùng được, vì nó chuẩn hóa tham số thành `$n`. Khi chạy `bootstrap()`, kết nối superuser phải có `SET log_statement = 'none'` để mật khẩu role không vào log. Bản sao lưu schema `xt_audit` không chứa khóa (chỉ có dấu vân tay), nhưng có HMAC: lưu giữ như dữ liệu nhạy cảm.
