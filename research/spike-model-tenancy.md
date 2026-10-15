# Spike: bố trí vật lý đa tenant cho model engine (đóng B4) + G-M1, G-M2 (một phần B6)

Ngày chạy: 2026-10-15. Môi trường: `/tmp/xt-spike-model2` với Docker `postgres:17-alpine` (PG 17) ở cổng 15450 và `postgrest/postgrest:latest` (**PostgREST 16.4**) ở cổng 15451/15452, chạy `--network host`. DuckDB 1.5.6 dùng `postgres_scanner`. Mọi số liệu dưới đây là output thật, bản đầy đủ ở `research/spike-model-tenancy/outputs/`. Đã xóa container, venv và extension DuckDB tải về.

Engine dùng lại `spike-model-engine`, có 4 thay đổi:
- `compiler.Layout` (ContextVar) quyết định schema, tiền tố bảng và tiền tố index.
- `catalog_version` có thêm `tenant_seq`.
- Pha 2 của apply chạy qua hàng đợi `xt_meta.apply_job`.
- Thêm `pii` và `managed_by` (G-M1, G-M2).

Ba biến thể được so sánh:

| Mã | Bố trí | Ghi chú |
|---|---|---|
| **A** | `xt_data.<model>` dùng chung, có `tenant_id` + RLS | Bản của spike-model-engine. Toàn deployment chung một catalog |
| **B1** | `xt_data.t<seq>__<model>` (đúng chữ D6), có `tenant_id` + RLS | Index phải có tiền tố `t<seq>_` |
| **B2** | `xt_t<seq>.<model>` (schema theo tenant), có `tenant_id` + RLS; view nằm ở `api_t<seq>` | Tên index không cần tiền tố |

---

## 0. Khuyến nghị cho D6

**Chọn B2: schema riêng cho từng tenant chứa bảng model** (`xt_t<seq>.<model>`, view PostgREST ở `api_t<seq>`). Vẫn giữ `tenant_id` + RLS FORCE để phòng thủ nhiều lớp. Bảng lõi (meta, audit, jobs, org, workflow) vẫn dùng chung, có RLS. Không dùng B1 `t<seq>__<model>`.

Lý do, rút từ số liệu thật:
1. **Chỉ B mới cho mỗi tenant có model khác nhau.** Đây là yêu cầu gốc của "nghiệp vụ là dữ liệu" và của test B4(a). A buộc cả deployment chung một catalog. Ở §1, tenant 2 có thêm `nganh_nghe`, bỏ `email`, thêm model `du_an`, và chạy được trên B1/B2.
2. **Migration bị cô lập theo tenant.** Ở A, một dòng vi phạm của tenant 37, hoặc một giao dịch đọc dài của tenant 12, chặn cả 50 tenant. Ở B, 48/50 tenant lên v2; tenant 12 retry thành công sau 59 ms; tenant 37 ở lại v1 cho tới khi sửa dữ liệu (§2).
3. **B2 hơn B1 ở mọi điểm vận hành, với cùng chi phí catalog.**
   - B1 không có tiền tố thì va chạm tên index (`relation "pk_m1" already exists`).
   - `pg_dump -t 't7__*'` + restore làm **mất view `api_t7`**, còn B2 `pg_dump -n xt_t3 -n api_s3` khôi phục đủ.
   - Xóa tenant là một lệnh `DROP SCHEMA … CASCADE` (326 ms), phục vụ quyền xóa theo Luật BVDLCN.
   - GRANT theo schema cho BI/PostgREST.
4. **BI / SQL trực tiếp an toàn hơn.** Ở A, client SQL tự `SET xt.tenant_id = T8` là đọc được dữ liệu tenant 8 (đã chứng minh). Phải vá bằng cách gắn tenant theo `session_user` (đã chạy được). Ở B, role BI chỉ có quyền trên `api_t7`; đổi GUC chỉ cho 0 dòng, còn đọc `api_t8` thì bị `permission denied`.

Cái giá phải trả, kèm cách chặn:
- **Catalog phình.** 50 tenant × 30 model sinh 18.749 `pg_class`, 117.931 `pg_attribute`, catalog 114 MB, `pg_dump -s` mất 2,8 s. Một kết nối chạm cả 1.500 bảng tốn **115 MB relcache**. Một transaction đọc 1.500 bảng thì **`out of shared memory`**.
  - Đặt trần, ví dụ ≤ 200 tenant × 30 model ≈ 6k bảng mỗi DB [suy luận từ số đo]; vượt thì thêm DB.
  - Pool kết nối có `max_lifetime`.
  - Không truy vấn xuyên tenant trong một transaction.
  - Phân tích xuyên tenant làm qua export Parquet.
- **Mỗi lần nâng model phải rollout theo từng tenant** (khoảng 44–81 ms/tenant), và phải quản lý trạng thái **lệch phiên bản** (49 tenant ở v2, 1 ở v1).
- **PostgREST cần `db-schemas` động** (in-DB config + `NOTIFY`, mất 3 s) cộng pre-request ràng profile với tenant trong token.

**Sửa tài liệu kiến trúc:**
- D6: đổi thành "schema theo tenant `xt_t<seq>.<model>` + view `api_t<seq>`", gắn nguồn là file này.
- D5: bỏ "Schema-per-tenant" khỏi danh sách bị loại. Chỉ còn loại DB-per-tenant.
- §3.3(c): view **`security_invoker=false`**, chủ `xt_api_owner`, và `xt_web` không là thành viên `xt_app`; lý do ở §5.2.
- §6.3: thêm các giới hạn nêu trên.

Nếu chỉ triển khai self-host một doanh nghiệp/deployment, A đơn giản hơn: migration một lần 168 + 152 ms, phân tích xuyên tenant 19 ms so với 333 ms. Nhưng lúc đó cũng chỉ có một tenant, nên B2 với 1 tenant không tốn thêm gì. **B2 phủ được cả hai mô hình.**

---

## 1. Cô lập (t1_isolation)

```
tenant 2 B1 cột khach_hang: ['ma_kh', 'ten', 'hang', 'ghi_chu', 'nganh_nghe']
tenant 1 B1 cột khach_hang: ['ma_kh', 'ten', 'email', 'hang', 'ghi_chu']
### B1 KHÔNG có tiền tố index -> va chạm tên index trong cùng schema
  -> DuplicateTable relation "pk_m1" already exists
  tên index dài nhất (id 24+24 ký tự, seq 7 chữ số): 62 byte <= 63
### Dữ liệu qua Engine (CRUD tổng quát chạy dưới layout của tenant)
  A/B1/B2: tạo KH0001 ok; trùng lần 2 -> DUPLICATE: 'Mã khách hàng' đã tồn tại, phải là duy nhất   (ánh xạ lỗi vẫn đúng với tên index có tiền tố)
  B1/B2: tenant 2 tạo du_an trỏ khach_hang của tenant 1 -> BAD_REFERENCE
  A : tenant 1 tạo đề xuất trỏ khach_hang của tenant 2 (cùng bảng) -> BAD_REFERENCE
### Tấn công/nhầm lẫn ở tầng SQL (xt_app, pool 1 kết nối)
  BẪY psycopg: sau 'with transaction()' lồng trong transaction ngầm, xt.tenant_id còn = '00000000-0000...' | status: INTRANS
  A : quên đặt tenant -> 0 dòng | ngữ cảnh T1 đọc bảng của T2 (A: cùng bảng) -> 1 (chỉ dòng T1) | T1 đọc bảng mình -> 1
  B1: quên đặt tenant -> 0 dòng | ngữ cảnh T1 đọc bảng của T2 (lỗi routing) -> 0 | T1 đọc bảng mình -> 1
  B2: quên đặt tenant -> 0 dòng | ngữ cảnh T1 đọc bảng của T2 (lỗi routing) -> 0 | T1 đọc bảng mình -> 1
  A/B1/B2: chèn tenant_id=T2 vào bảng T1 khi ngữ cảnh T1 -> InsufficientPrivilege
  B1 KHÔNG RLS: ngữ cảnh T1 đọc nhầm bảng T2 -> (1, 'Khách của tenant 2') <- rò dữ liệu
```

Kết luận:
- **B vẫn cần RLS.** Bỏ RLS thì chỉ một lỗi route sai bảng là rò toàn bộ dữ liệu.
- FK ở B bị cô lập theo cấu trúc: FK trỏ vào bảng của chính tenant.
- **Bẫy mới phát hiện.** Với kết nối psycopg non-autocommit, `execute` chạy ngoài transaction sẽ mở một transaction ngầm. `with conn.transaction()` sau đó chỉ là SAVEPOINT, nên `set_config(…, true)` vẫn sống tới hết transaction ngầm. Kết quả là tenant của request trước rò sang request sau. Đã sửa trong engine: `Engine.tx()` từ chối kết nối không ở trạng thái `IDLE`.

## 2. Migration một model trên 50 tenant (t2_migrate50)

Dữ liệu: 50 tenant × 2.000 dòng. A có 1 bảng 100k dòng, B1 có 50 bảng × 2k dòng. Thay đổi v2 gồm: thêm cột trống được, thêm cột bắt buộc có default, siết `so_tien ≤ 300tr` (tenant 37 có một dòng 400tr), thêm index. Trong lúc migration, một luồng ghi liên tục vào tenant 5.

```
Cấp phát v1: A 88 ms (1 lần) | B1 4075 ms cho 50 tenant (81 ms/tenant)
### A: một bảng chung
  lần 1: ApplyError: Còn thao tác BỊ CHẶN: ['check:m2:ck_m2_f2_max'] -> 1 dòng của tenant 37 chặn cả 50 tenant
  lần 2 (một giao dịch đọc dài của tenant 12 đang mở): LockNotAvailable sau 2.2s -> KHÔNG tenant nào được nâng cấp
  lần 3 (đã sửa dữ liệu): pha 1 168 ms (12 câu, 3 job) | pha 2 152 ms | ghi tenant 5: p50 4.6 ms, p99 81.1 ms, max 81 ms, lỗi 0
### B1: 50 bảng, mỗi tenant 1 transaction pha 1 + job pha 2
  pha 1: 4141 ms tổng, p50 44 ms/tenant | ok 48/50 | lỗi: {12: 'LockNotAvailable', 37: "BLOCKED: … ck_m2_f2_max"}
   bị chặn bởi: VALIDATE CONSTRAINT … <- 'autovacuum: VACUUM xt_data.t20__de_xuat_tam_ung'
  pha 2 lượt 1: 2061 ms {'done': 141, 'failed': 1} -> job sau của cùng tenant giữ 'pending' (thứ tự được bảo toàn)
  pha 2 lượt 2 (retry): {'done': 3}
  retry tenant 12 sau khi khóa nhả: ok trong 59 ms
  ghi tenant 5: 695 lệnh, p50 4.1 ms, p99 20.5 ms, max 44 ms, lỗi 0
  phân bố phiên bản catalog B1: [(v1, 1 tenant), (v2, 49 tenant)] | index invalid: 0 | constraint NOT VALID: 0
### B1: thêm 1 cột cho cả 50 tenant trong MỘT transaction: 55 ms, giữ 52 khóa
```

Rút ra cho **apply 2 pha ở v2**:
- **Pha 1 theo tenant.** Mỗi tenant một transaction, advisory lock `xt_meta:catalog:<seq>`, mỗi tenant có `catalog_version` riêng. Job pha 2 được **enqueue trong cùng transaction pha 1** (`xt_meta.apply_job`), nên commit pha 1 thì chắc chắn có job, không mất job nào.
- **Worker pha 2.**
  - Chạy job theo `(tenant, version, ord)`.
  - Job `post` chạy trong transaction ngắn có `lock_timeout`; job `concurrent` chạy autocommit.
  - Lỗi thì `failed` rồi retry; quá `max_attempts` thì `dead`. Job sau của cùng tenant/version bị giữ lại.
  - Trước khi tạo lại index, xóa index INVALID còn sót.
- **Phát hiện:** `VALIDATE CONSTRAINT` (SHARE UPDATE EXCLUSIVE) bị **autovacuum** chặn. Autovacuum chỉ tự nhường sau `deadlock_timeout` (1 s); ở đây `lock_timeout` cũng 1 s nên hết giờ trước. Quy tắc: `lock_timeout` của pha 2 phải lớn hơn `deadlock_timeout` (ví dụ 5 s). Giao dịch đọc dài **không** chặn VALIDATE (đã kiểm riêng).
- **Lệch phiên bản là trạng thái hợp lệ.** Cần màn hình rollout gồm số tenant ở mỗi version, lý do kẹt (BLOCKED do dữ liệu / lock), và nút retry. Engine phải luôn nạp catalog theo tenant.
- **Rollout nguyên tử xuyên tenant** chỉ nên dùng cho DDL nhẹ. 49 ALTER trong một transaction ổn (52 khóa). 1.500 ALTER cũng chạy được (1.503 khóa, 1,5 s), nhưng đọc 1.500 bảng thì vượt bảng khóa (§3).

## 3. Catalog bloat (t3_bloat, t3b)

```
trống (bootstrap)        pg_class=   440 index=   176 pg_attribute=   3283 | catalog 8632 kB | pg_dump -s 0.2s
A: 30 model              pg_class=   799 index=   475 pg_attribute=   5531 constraint=   450 policy=   91 | catalog 10 MB | pg_dump -s 0.3s (0.5 MB)
+B1: 50x30 model         pg_class= 18749 index= 15425 pg_attribute= 117931 constraint= 16950 policy= 4591 trigger= 3062 | catalog 114 MB, DB 230 MB | pg_dump -s 2.8s (25.6 MB)
Thời gian cấp phát: A 1.2s | B1 37.6s (0.75s/tenant cho 30 model x 30 trường)
kết nối mới, 1 tenant (30 bảng B1)   : lần đầu 96 ms, lần 2 36 ms; relcache 2229 KB/kết nối
kết nối mới, A (30 bảng chung)       : lần đầu 111 ms, lần 2 38 ms; relcache 2229 KB/kết nối
  toàn bộ 1500 bảng B1: đọc trong 1 transaction -> OutOfMemory: out of shared memory (You might need to increase "max_locks_per_transaction".)
kết nối mới, toàn bộ 1500 bảng B1    : 2608 ms (mỗi bảng 1 transaction); relcache 115255 KB/kết nối
ALTER cả 1500 bảng B1 trong MỘT transaction: ok 1.5s, giữ 1503 khóa
```

Mỗi tenant 30 model tốn khoảng 360 `pg_class`, 2.250 `pg_attribute`, 2 MB catalog. Một kết nối đã chạm tới tenant nào thì giữ khoảng 2,3 MB relcache cho tenant đó.

Ngoại suy [suy luận, chưa đo]: 500 tenant cho khoảng 180k `pg_class` và catalog khoảng 1 GB. Nếu mỗi kết nối trong pool lần lượt phục vụ mọi tenant thì relcache có thể lên tới 1 GB/kết nối.

Biện pháp bắt buộc khi chọn B:
- Trần tenant mỗi DB.
- `max_lifetime`/`max_idle` cho pool.
- Đặt `max_locks_per_transaction` ≥ 256 nếu có job bảo trì đụng nhiều bảng.
- Mọi job xuyên tenant phải mỗi tenant một transaction.

Với 1 tenant, chi phí truy vấn của A và B như nhau (96 ms so với 111 ms ở lần chạm đầu).

## 4. Hiệu năng truy vấn có/không RLS (t4_query)

Tenant 7, 2.000 dòng. A là bảng chung 100k dòng, B1 là bảng riêng. Số liệu là median 30 lần (ms). Cột "+RLS" có cả round-trip `set_config`.

```
truy vấn                        A+RLS  A noRLS   B1+RLS  B1 noRLS
trang 20 dòng mới nhất           0.70     0.99     0.85     1.83
đếm so_tien >= 50tr              1.30     0.39     1.42     0.22
staff own: đếm + trang           1.04     0.64     1.22     0.88
tổng tiền theo trạng thái        1.92     0.78     3.11     0.99
EXPLAIN A+RLS: Index Scan using ix_m2_i2 … Index Cond: ((tenant_id = …current_setting('xt.tenant_id')…) AND (so_tien >= 50000000)) — 0.669 ms
```

Ở quy mô doanh nghiệp vừa và nhỏ, bố trí vật lý **không** tạo khác biệt hiệu năng. Index `(tenant_id, …)` của A cho cùng độ chọn lọc với bảng riêng của B. RLS tốn khoảng 0,3–2 ms, phần lớn là round-trip `set_config` cộng điều kiện role. Không có lý do hiệu năng để bỏ RLS ở B.

## 5. PostgREST thật (t5, t5b, t5c)

```
tạo view: A 30 view schema api; B 1470 view trong 49 schema api_t<seq> (6.0s)
PostgREST 16.4 | A schema cache: in 15.6 milliseconds | B schema cache: in 104.9 milliseconds
### A: /model_00 trên schema api (bảng chung)
  token T7 -> 200 ['A-T7-a', 'A-T7-b'] | token T8 -> 200 ['A-T8-a', 'A-T8-b'] | không token -> 401 PGRST302
### B: Accept-Profile api_t<seq>
  T7 + api_t7                        -> 200 ['B1-T7-a', 'B1-T7-b']
  T8 + api_t8                        -> 200 ['B1-T8-a', 'B1-T8-b']
  T7 + api_t8 (đổi profile)          -> 403 ('42501', 'schema api_t8 không thuộc tenant trong token')
  T7 không header (mặc định api_t1)  -> 403 ('42501', 'schema api_t1 không thuộc tenant trong token')
  T7 + api_t50 (chưa có)             -> 406 ('PGRST106', 'Invalid schema: api_t50')
### B: thêm tenant 50 lúc chạy
  trước reload: 406
  ALTER ROLE xt_pgrst SET pgrst.db_schemas = '…,api_t50'; NOTIFY pgrst 'reload config' + 'reload schema'
  sau reload: 200 [{'truong_0': 'B1-T50-a'}] sau 2956 ms
  độ dài db_schemas: 390 ký tự cho 50 tenant
```

### 5.1. Cách lộ dữ liệu theo từng biến thể

- **A:** một schema `api`, mỗi model một view. Cách này đơn giản nhất.
- **B:** mỗi tenant một schema `api_t<seq>`, client gửi `Accept-Profile`.
  - Danh sách `db-schemas` nằm trong DB (`db-config=true`, tức `ALTER ROLE … SET pgrst.db_schemas`). Thêm tenant thì `NOTIFY pgrst, 'reload config'` và `'reload schema'`, mất khoảng 3 s.
  - Pre-request `xt.pgrst_pre()` so `current_schema()` với `tenant_seq` trong JWT. Nhờ đó token T7 không đọc được `api_t8`, kể cả khi không gửi header (schema mặc định).
  - Lớp thứ hai là RLS. `xt_web` đứng ở ngữ cảnh T7 đọc thẳng `api_t8.model_00` được **0 dòng** (t5b).
  - Mỗi lần reload, PostgREST nạp lại cả cache: khoảng 105 ms cho 1.470 view. Chuỗi `db_schemas` dài khoảng 8 ký tự/tenant.
- **Hướng thay thế còn lại** (tự sinh `/data/v1` trong xt-api) **không cần thiết**. B4(b) đóng được với PostgREST + Accept-Profile.

### 5.2. Lỗ hổng đã tìm ra và đã sửa

Thiết kế V2 §3.3(c) dùng view `security_invoker=true`. Muốn thế thì `xt_web` phải có quyền trên bảng gốc, và cách dễ nhất là `GRANT xt_app TO xt_web`. Nhưng t5b cho thấy cách này mở đường leo quyền:

```
xt_web SET ROLE xt_workflow -> ĐƯỢC (lỗi!)      # chuỗi xt_pgrst → xt_web → xt_app → xt_workflow (SET TRUE)
```

Sau khi sửa (t5c): `REVOKE xt_app FROM xt_web`; view `security_invoker=false`, chủ `xt_api_owner` (NOBYPASSRLS); policy SELECT + tenant thêm `TO … xt_api_owner`.

```
HTTP T7 + api_t7 -> 200 [B1-T7-a, B1-T7-b] | HTTP T8 + api_t8 -> 200 [B1-T8-a, B1-T8-b]
xt_web ngữ cảnh T7 đọc api_t8.model_00 -> 0 dòng; api_t7.model_00 -> 2
xt_web SELECT xt_data.t7__model_00 -> permission denied for schema xt_data
xt_web SET ROLE xt_workflow / xt_app -> permission denied to set role
```

Quy tắc rút ra: **không role nào ngoài code xT được là thành viên của `xt_app`.** Compiler cần thêm `xt_api_owner` vào danh sách role của policy SELECT, không sửa tay như trong spike.

## 6. Sao lưu/khôi phục một tenant (t6, t6b)

10 tenant × 30 model × 1.000 dòng.

```
B1: pg_dump -Fc -t 'xt_data.t7__*': dump 1.33s, 2.0 MB | xóa+restore 3.66s | dữ liệu (30002, '0140f5794329') -> KHỚP
    sau restore: policy=90, trigger=60, quyền cột xt_app (UPDATE truong_1, UPDATE cả bảng)=(True, False)
    NHƯNG: api_t7.* bị MẤT (DROP TABLE … CASCADE xóa view phụ thuộc, -t không dump schema api_t7)   [t7: lần chạy đầu]
B2: pg_dump -n xt_t3 -n api_s3: 1.06s, 1.8 MB | DROP SCHEMA … CASCADE (xóa tenant): 326 ms | restore 2.30s | KHỚP | view api=30, policy=90
A : COPY (SELECT … WHERE tenant_id=T7) x30: export 0.16s, 7.2 MB | DELETE+COPY 1.61s | KHỚP
    cần superuser (session_replication_role) để không kích trigger audit; 2000 dead tuple trên bảng chung;
    bản sao lưu không chứa schema/policy/quyền -> không khôi phục được tenant về spec cũ
```

B2 cho bản sao lưu tự chứa: dữ liệu, cấu trúc, policy, quyền cột và view của đúng một tenant. Thêm `catalog_version WHERE tenant_seq=…` và các dòng audit của tenant là đủ để chuyển tenant sang DB khác. A export nhanh hơn vì không phải đọc catalog lớn, nhưng khôi phục cần đặc quyền và không mang theo được spec.

## 7. BI / DuckDB (t7, t7b)

```
A  (role xt_bi_a,  bảng chung):   DuckDB đếm api.model_00 = 1002
xt_bi_a   tự `SET xt.tenant_id = T8` rồi đọc api.model_00     -> 1002 dòng, tenant …7e000008  <- RÒ tenant 8
xt_bi_t7  tự `SET xt.tenant_id = T8` rồi đọc api_t7.model_00  -> 0 dòng
xt_bi_t7  tự `SET xt.tenant_id = T8` rồi đọc api_t8.model_00  -> InsufficientPrivilege: permission denied for schema api_t8
Xuất snapshot Parquet tenant 7 (30 model): B qua role tenant 0.85s, 1.30 MB | A qua migrator + WHERE tenant_id 0.74s, 1.30 MB; đọc lại 30002 dòng
Xuyên tenant: A 1 bảng GROUP BY 19 ms | B1 UNION ALL 50 bảng 333 ms (model lệch giữa tenant thì không UNION được)
Vá cho A (t7b): xt.tenant() = coalesce(xt.login_tenant() [SECURITY DEFINER, theo session_user], GUC)
  xt_bi_a (gắn T7) SET xt.tenant_id=T8 -> (1002, …7e000007)  — hết rò; xt_app vẫn dùng GUC, 0.57 ms/truy vấn, vẫn Index Only Scan
```

**GUC chỉ là ngữ cảnh do backend tin cậy đặt.** Bất kỳ client nào được chạy SQL tự do (DuckDB, Metabase, psql) đều đổi được GUC. Với B2, đặc quyền schema chặn sẵn. Với A, phải gắn tenant theo `session_user`, và gắn cả `xt.roles`.

Khuyến nghị BI: tenant dùng role `xt_bi_t<seq>` chỉ có quyền trên `api_t<seq>`, hoặc nhận snapshot Parquet do xT xuất. Phân tích xuyên tenant của nền tảng đi qua Parquet + DuckDB, không UNION trên DB sống.

## 8. G-M1: trường do workflow quản (t8, layout B2)

Thiết kế:
- `FieldSpec.managed_by: "workflow" | null`. Nếu trường managed vừa `required` thì bắt buộc có `default`.
- **Lớp app:** `create_record`/`update_record` từ chối với `PRECONDITION_FAILED` kèm continuation `transition_record`. `values_schema` của CRUD không chứa các trường này.
- **Lớp DB:** compiler không còn `GRANT UPDATE` cả bảng cho `xt_app`. Thay vào đó chỉ `GRANT INSERT (cột tự do)` và `GRANT UPDATE (cột tự do)`. Role `xt_workflow` (NOLOGIN) có UPDATE đầy đủ. `xt_app` được `GRANT xt_workflow WITH INHERIT FALSE, SET TRUE`, và chỉ đường transition gọi `SET LOCAL ROLE xt_workflow` (`Engine.workflow_write`).
- Policy RLS áp cho cả `xt_app, xt_workflow`.
- Planner: bỏ `managed_by` thì sinh op **RISKY**; thêm cột thì cấp lại quyền cột.

```
describe_model de_xuat managed_by: {'noi_dung': None, 'so_tien': None, 'trang_thai': 'workflow', 'nguoi_duyet': 'workflow', 'thoi_diem_duyet': 'workflow'}
values_schema update (CRUD): ['department_id', 'noi_dung', 'owner_id', 'so_tien', 'team_id']
create kèm trang_thai=da_duyet -> PRECONDITION_FAILED "Trường 'Trạng thái' do quy trình (workflow) quản lý, không sửa trực tiếp được", hint: Dùng transition_record / decide_request
update_record ['trang_thai'] / ['nguoi_duyet'] / ['so_tien','thoi_diem_duyet'] -> PRECONDITION_FAILED
update_record so_tien (trường thường) -> True revision 2
SQL thô xt_app: UPDATE … SET trang_thai = 'da_duyet'      -> InsufficientPrivilege: permission denied for table de_xuat
SQL thô xt_app: INSERT … (noi_dung, so_tien, trang_thai)  -> InsufficientPrivilege
SQL thô xt_app: UPDATE … SET noi_dung = 'sửa SQL thô'     -> OK (1 dòng)
workflow_write (SET LOCAL ROLE xt_workflow) -> True da_duyet rev 4
workflow_write của tenant 12 vào bảng tenant 11 (lỗi routing) -> NOT_FOUND   (RLS vẫn áp cho xt_workflow)
planner bỏ managed_by của trang_thai -> RISKY "Bỏ quản lý workflow cho ['trang_thai']: CRUD tổng quát sẽ ghi được"
```

Giới hạn còn lại:
- Code trong tiến trình xt-api vẫn `SET ROLE xt_workflow` được, vì đây chính là đường transition hợp lệ. Lớp DB chặn được agent, CRUD tổng quát, PostgREST và BI (t5c), nhưng không chặn được một tiến trình xt-api đã bị chiếm.
- Cần lint/CI: chỉ module `workflow/` được gọi `workflow_write`.
- Field `state` theo TCV1:1779 nên là trường `managed_by: workflow`, tức một cơ chế chung cho state, người duyệt và bước hiện tại.

## 9. G-M2: nhãn DLCN trong spec / catalog / describe / audit (t8)

Thiết kế:
- `FieldSpec.pii: none | basic | sensitive` (bắt buộc trong catalog) và `pii_suggested: bool`.
- Thiếu nhãn thì engine gợi ý theo NĐ 356/2025, khớp `spike-pii-lane.md`:
  - Đ.3 k.7 (SĐT, số định danh, hộ chiếu), k.4 (địa chỉ), họ tên, email → `basic`.
  - Đ.4 k.1 (sức khỏe, tài khoản/thẻ, ảnh CCCD) và lương (xT xếp thận trọng) → `sensitive`.
  - Nhãn gợi ý hiện cờ để người duyệt xác nhận.
- Audit: trigger nhận danh sách cột sensitive qua `TG_ARGV` và lưu `hmac:` (HMAC-SHA256 cắt 128 bit, khóa ở `xt_audit.pepper`), không lưu giá trị. Chuỗi hash vẫn kiểm chứng được.
- Planner: **hạ** mức thì RISKY; đổi nhãn thì tạo lại trigger audit.

```
describe_model nhan_vien: [('ho_ten','basic','gợi ý'), ('so_cccd','basic','khai báo'), ('so_dien_thoai','basic','gợi ý'),
                           ('luong','sensitive','gợi ý'), ('suc_khoe','sensitive','khai báo'), ('ghi_chu','none','gợi ý'), ('phong_ban','none','gợi ý')]
catalog_version lưu: {'ho_ten': 'basic', 'so_cccd': 'basic', 'so_dien_thoai': 'basic', 'luong': 'sensitive', 'suc_khoe': 'sensitive', 'ghi_chu': 'none', 'phong_ban': 'none'}
audit INSERT: {'ho_ten': 'Nguyễn Văn A', 'so_cccd': '001203004567', 'luong': 'hmac:49b2544f…', 'suc_khoe': 'hmac:3653dda3…', 'ghi_chu': None}
audit UPDATE: {'luong': 'hmac:1bcc5ae6…', 'ghi_chu': 'tăng lương'} | old: hmac:49b2544f…
verify chuỗi audit tenant: (2, None)
[RỦI RO] HẠ mức DLCN: ['luong'] (…); đổi nhãn DLCN: ['luong: sensitive→basic', 'ghi_chu: none→basic']; cột audit chỉ lưu HMAC: ['suc_khoe']
```

Vấn đề mở của G-M2:
- **Field `basic`** (CCCD, SĐT) vẫn nằm nguyên văn trong audit, đúng V2:514 (chỉ sensitive được hash). Nếu muốn chặt hơn, đổi thành HMAC cho cả basic.
- **Khóa HMAC** phải đưa vào OpenBao hoặc Compose secret và có quy trình xoay khóa. Hash thuần (không khóa) của lương thì dò ngược được.
- **"Không được hạ dưới mức của gói chuẩn"** mới chỉ là RISKY, chưa chặn cứng.

## 10. Các phát hiện khác cần đưa vào thiết kế

| # | Phát hiện | Bằng chứng | Hành động |
|---|---|---|---|
| 1 | Savepoint lồng trong transaction ngầm giữ `set_config` | t1 "BẪY psycopg" | `Engine.tx()` kiểm `IDLE` (đã làm). Pool cấu hình `autocommit=True` hoặc `check`/`reset` |
| 2 | Chuỗi membership cho `SET ROLE` xuyên role | t5b | `xt_web`/BI không là thành viên `xt_app`. View `security_invoker=false` |
| 3 | GUC bị client SQL tự do ghi đè | t7 | B2 + quyền schema. Với A thì gắn theo `session_user` (t7b) |
| 4 | `pg_restore -t` làm mất view phụ thuộc | t6/t7 | B2: dump `-n xt_t<seq> -n api_t<seq>` |
| 5 | Autovacuum chặn VALIDATE | t2 | `lock_timeout` pha 2 > `deadlock_timeout`; retry |
| 6 | Đọc nhiều bảng trong một transaction làm cạn bảng khóa | t3b | Job xuyên tenant: mỗi tenant 1 transaction; tăng `max_locks_per_transaction` |
| 7 | Relcache khoảng 2,3 MB mỗi tenant đã chạm, tính trên mỗi kết nối | t3b | `max_lifetime` cho pool, trần tenant/DB |
| 8 | B1 cần tiền tố index, tối đa 62/63 byte với seq 7 chữ số | t1 | Dùng B2 |

## 11. Mã và cách chạy lại

- Mã (có header SPDX Apache-2.0):
  - `research/spike-model-tenancy/xtmodel/` là engine đã sửa: `Layout`, `iname`, `grant_steps`, `audit_trigger`, `run_jobs`, `workflow_write`, `describe_model`, `pii`/`managed_by`.
  - `tests/t1…t8` là các kịch bản chạy thật. `tests/common.py` gom kết nối, `reset()`, layout và `gen_catalog`.
- Thứ tự chạy: `t1` (tự reset) → `t2` (tự reset) → `t4` → `t3_bloat` (tự reset) → `t3b` → `t5_postgrest` → `t5b` → `t5c` → `t6_backup` → `t7_duckdb` → `t7b` → `t6b_backup_b2` → `t8_engine_gm`.
- Cần container `postgres:17-alpine` tên `xt-spike-model2-pg` ở cổng 15450, và image `postgrest/postgrest:latest`.
- Output: `research/spike-model-tenancy/outputs/out_*.txt`.
