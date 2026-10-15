<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->
# Audit dịch vụ tệp `/files/v1` (16/10/2026, tester Opus 5.5)

Phạm vi: `docs/files-api.md`, D-FILES-1..8, `src/xt/application/files/*`, `src/xt/adapters/blob/*`, `src/xt/adapters/http/files_api.py`, `src/xt/entrypoints/files_http.py`, migration `0009_files.sql`, gói `tep-dinh-kem`, tool `upload_file`, `deploy/compose.yaml` (seaweedfs), route APISIX `api-files`, scope Keycloak `xt-files`. Không sửa `src/` hay `deploy/`.

Môi trường: 1 Postgres 17 + 1 SeaweedFS 4.48 (container tên riêng, cổng loopback ngẫu nhiên, tmpfs, `docker rm -f -v`), uvicorn loopback, JWKS test. Không chạy compose đầy đủ. Image SeaweedFS đã bị xóa giữa phiên, phải pull lại đúng digest pin.

## 1. Chạy lại test có sẵn

| Bộ | Kết quả |
|---|---|
| `tests/unit/files` + `tests/unit/http/test_files_api.py` | **40 passed** |
| `tests/integration/composition/test_files_api.py` (`-m integration`) | **13 passed** (20 s) |
| Audit mới `tests/integration/files_audit/` | **18 passed, 9 xfailed (strict)** (34 s) |

```bash
uv run pytest -m integration tests/integration/files_audit --no-cov -rxX
```

## 2. Kết quả theo hạng mục

| # | Hạng mục | KQ | Bằng chứng |
|---|---|---|---|
| 1 | Giả mạo / sửa link ký sẵn (đổi MAC, bỏ dấu `.`, đổi file/tenant/người/hạn, đảo thứ tự trường, thêm trường, token 600 ký tự) | PASS | `test_upload_token_tampering_rejected`: cả 10 biến thể đều 403 `upload_url_invalid`. HMAC phủ toàn bộ payload, có nhãn riêng `xt-files-upload-v1` |
| 2 | Thêm query / `/../`; dùng link của tệp A cho tệp B; dùng lại link | PASS | Query không đổi được đích. Link chỉ dùng một lần (409). Tệp B vẫn `awaiting_upload` |
| 3 | Đổi method PUT↔GET | PASS | GET chỉ trả trang HTML hoặc 409, không bao giờ trả byte. POST/DELETE/PATCH ra 405 |
| 4 | Biên hạn link | PASS | `grant.expires = floor(upload_expires_at)`, hai bên cùng so `<=`. Test có sẵn xác nhận PUT sau TTL → 403 và tệp `expired` |
| 5 | Token chứa ký tự không phải ASCII | **FAIL** | FB-05 (500) |
| 6 | Path traversal trong tên (`../`, `\`, `..`, NUL, U+202E, CRLF, rỗng, >255) | PASS | 400. `%2e%2e%2f…` và `．．∕` được lưu như tên thường. Khóa object luôn là `t/<tenant>/sha256/<hh>/<hex>`, không dùng tên. `Content-Disposition` không có CR/LF |
| 7 | Giả kiểu tệp (SVG/HTML/XML khai là ảnh hoặc text, ELF, gzip, OLE2, shebang, GIF khai là PNG) | PASS | 9/9 ca bị 415 |
| 8 | Polyglot lọt magic byte (PDF+HTML, GIF+JS, text có HTML giữa tệp, `<!DOCTYPE svg`, ZIP+HTML, PDF+ZIP) | PASS (vô hại) | Tải về với Content-Type là kiểu đã kiểm, `nosniff`, `attachment`, `CSP: default-src 'none'; sandbox` |
| 9 | Kiểm UTF-8/NUL của `text/*` | **FAIL** | FB-07 (chỉ kiểm 8 KiB đầu) |
| 10 | Zip bomb | PASS | xT không giải nén gì. Tệp 64 MiB nén còn 64 KiB đi khứ hồi nguyên vẹn dưới 10 s |
| 11 | Quá hạn mức: CL > MAX, chunked không có CL, CL nói dối, CL+TE trên PUT | PASS | 413 ngay / 411 / chỉ đọc đúng CL byte / Spool cắt ở MAX → 413 |
| 12 | CL+TE trên multipart | **FAIL** | FB-06 |
| 13 | TOCTOU: 8 PUT song song; finalize khi PUT thứ hai đang gửi dở | PASS | 1×200 + 7×409. PUT chậm thua (409), byte tải về đúng là bản thắng. Nhờ `SELECT … FOR UPDATE` và HEAD đối chiếu size/sha |
| 14 | Chéo tenant qua REST (GET/content/finalize/DELETE) | PASS | Cả bốn đều 404 giống id bịa |
| 15 | RLS `file_object` | PASS | `ENABLE` + `FORCE` RLS. Repo của tenant A không thấy hàng của B. `xt_app` không UPDATE được `tenant_id/id/name/created_by` |
| 16 | FK bản ghi → tệp tenant khác (tệp B có thật) | PASS | `create_record` bị từ chối (FK ghép) |
| 17 | FK bản ghi → tệp của người khác cùng tenant | **FAIL** | FB-09 |
| 18 | Đoán khóa S3 | PASS ở cổng S3 / **FAIL** trong mạng | S3 :8333 không chữ ký hoặc chữ ký sai → 400/403. Không publish cổng, không có route APISIX. Nhưng filer/volume không xác thực (FB-03) |
| 19 | Header tải về chống stored XSS | PASS | Đủ bộ header như mục 8. Host Desk không có route `/files`. Origin `xt-api` dùng chung với `sandbox.html` nhưng nội dung tải về có CSP sandbox |
| 20 | Tải tệp chưa quét (`uploader`) | PASS | Chỉ người tải lên lấy được byte. dept_head/đồng nghiệp nhận 409/404 |
| 21 | Xóa mềm rồi tải về / finalize / dùng link | PASS | 404. Link còn hạn → 403. Người duyệt cũng nhận 404 |
| 22 | Audit đủ sự kiện + hash-chain | PASS | `created, rejected, uploaded, finalized, downloaded`. `xt_audit.verify` sạch. Thiếu sự kiện cho 413 / link sai / `already_uploaded` (Info) |
| 23 | Băm tên tệp trong audit | **FAIL** | FB-04 |
| 24 | SigV4: canonical, credentials, TLS | PASS (Info) | Canonical request đúng cho 6 thao tác (không query, path quote từng ký tự, header `x-amz-*` sort). `FilesConfig` để `repr=False` cho khóa. `BlobError` chỉ mang status + `<Code>`, không có header. S3 là `http://` nội bộ, chưa có TLS (chấp nhận trong compose) |
| 25 | DoS: nhiều ý định, quota, dọn rác | **FAIL** | FB-02 |
| 26 | Slowloris | PASS có điều kiện | xt-api không publish cổng. Cloudflare/APISIX đứng trước (`client-control` 27 MiB, `limit-req` 10 r/s, timeout 300 s). Bản thân uvicorn không có timeout cho thân request |
| 27 | HEAD `/content` làm rò kết nối S3 (giả thuyết) | PASS (đã bác) | Sau 110 HEAD, GET vẫn chạy (`test_head_content_does_not_exhaust_s3_pool`) |
| 28 | IAM `file.read` / `file.delete` | PASS / **thiết kế yếu** | Đồng nghiệp 404. dept_head đọc được nhưng xóa bị 403. Phạm vi đọc quá rộng (FB-10) |
| 29 | Cách ly audience (D-FILES-1) | **FAIL** (một chiều) | `/files` từ chối aud MCP/workflow/data. Nhưng MCP và workflow nhận token có kèm `xt-files` (FB-08) |
| 30 | Quét lại khi clamd lỗi | **FAIL** (đọc code) | FB-01 |
| 31 | Keycloak scope `xt-files` | PASS (đọc code) | `provision_data.py`: scope optional, mapper audience `xt-files`, chỉ cấp cho `XT_FILES_CLIENTS` (mặc định `xt-cli`). Phiên này không chạy Keycloak |

## 3. Lỗi

### FB-02 — Cao: không có quota, không dọn quarantine, kho đầy sau ~21 lượt tải

- **Hiện trạng:**
  - SeaweedFS trong compose chạy `-volume.max=8 -master.volumeSizeLimitMB=64`, tức tổng dung lượng khoảng **512 MB**. Đã đo: `/dir/status` → `"Max":8`.
  - App không giới hạn số ý định hay tổng byte theo người/tenant. Test tạo liền 200 ý định, tất cả 201.
  - Hạn tải lên chỉ áp kiểu "lười" (`FileManager.current`): object `quarantine/<id>` của lượt PUT không finalize chỉ bị xóa khi có ai chạm vào hàng đó. Không có sweeper (grep cho thấy `rescan`/expire không được gọi từ worker).
  - Xóa mềm không bao giờ xóa byte. "Tác vụ retention" nói trong docs chưa tồn tại.
- **Tái hiện:** `test_abandoned_upload_bytes_are_reclaimed`. PUT 256 KiB, không finalize, chờ quá TTL → `HEAD t/<tenant>/quarantine/<id>` vẫn còn object. Ở cấu hình thật, một employee (có `file.create`) làm 21 lần ý định + PUT 25 MiB ngẫu nhiên (không trùng sha) là kho đầy, toàn bộ dịch vụ tệp của mọi tenant ngừng.
- **Sửa:**
  - Thêm job worker định kỳ: chọn `status IN (awaiting_upload, uploaded) AND upload_expires_at < now()`, chuyển `expired`, xóa quarantine.
  - Thêm quota: số ý định đang mở trên mỗi người (vd 20), byte pending + tổng byte trên mỗi tenant (`XT_FILES_TENANT_QUOTA_BYTES`).
  - Thêm job retention xóa object `sha256/` khi không còn hàng nào chưa xóa trỏ tới.
  - Ghi rõ dung lượng 512 MB trong `docs/files-api.md` hoặc nâng `volume.max`.

### FB-03 — Trung bình: master/volume/filer SeaweedFS không xác thực trong mạng compose

- **Hiện trạng:** chỉ cổng S3 :8333 đòi SigV4. `weed server` còn mở :9333 (master), :8080 (volume) và :8888 (filer) trên `-ip.bind=0.0.0.0`, không có `security.toml`/JWT. Container nằm trong mạng `default`, chung với keycloak, postgrest, xt-desk, apisix+cloudflared, worker. Bất kỳ container nào bị chiếm (hoặc SSRF) cũng đọc, ghi, xóa được object của mọi tenant mà không cần khóa S3. `/dir/assign` cũng cấp fid ghi mà không cần xác thực.
- **Tái hiện:** `test_seaweed_filer_requires_auth`: `wget http://127.0.0.1:8888/buckets/<bucket>/t/<tenant>/sha256/..` trả đúng byte bí mật.
- **Sửa:**
  - Tách mạng `files` (internal) chỉ gồm `xt-api`, `xt-worker`, `seaweedfs`. Bỏ seaweedfs khỏi `default`.
  - Bật `security.toml` (`[jwt.signing]`, `[jwt.filer_signing]`) cho volume/filer, hoặc bind master/volume/filer về 127.0.0.1 và chỉ để S3 nghe ngoài.

### FB-04 — Trung bình (PII): tên tệp trong audit là sha256 không khóa

- **Hiện trạng:** `xt_audit.log_meta` lưu `redacted:sha256:` + `sha256('"<tên>"')`, không có khóa. Tên kiểu `CCCD_<12 số>.pdf` dò ngược được rất nhanh. CCCD có cấu trúc (tỉnh 3 số + thế kỷ/giới 1 số + năm 2 số + 6 số ngẫu nhiên): biết tỉnh và năm thì chỉ còn 10^6 khả năng. Docs coi đây là biện pháp che PII.
- **Tái hiện:** `test_audit_filename_hash_is_keyed`: `sha256(json.dumps(name))` có nguyên văn trong `xt_audit.event.diff`.
- **Sửa:** dùng HMAC-SHA256 với pepper/khóa audit đã có (`XT_AUDIT_KEY_FILE` / pepper bootstrap) cho các cột `redact` trong `log_meta`, hoặc không ghi tên vào audit.

### FB-01 — Trung bình (profile full): tệp kẹt `scanning` vĩnh viễn

- **Hiện trạng:**
  - `CompositionFilesBackend.rescan()` không được gọi ở đâu (grep `rescan` trong `src/` chỉ có định nghĩa). clamd timeout, mất kết nối hay trả `ERROR` (vd `INSTREAM size limit exceeded` khi `StreamMaxLength` < `XT_FILES_MAX_BYTES`) đều thành `ScannerUnavailable`. Tệp giữ `scanning` mãi, không tải về được, không gửi duyệt được.
  - Việc quét chạy đồng bộ bên trong transaction finalize, giữ `FOR UPDATE` và một kết nối DB tới 60 s.
- **Tái hiện:** đọc code (`files_http.py:582`, `service.py:scan`). Chưa chạy ClamAV thật.
- **Sửa:**
  - Thêm job worker định kỳ gọi `rescan` cho từng tenant.
  - finalize chỉ chuyển sang `scanning` rồi commit. Việc quét chạy qua outbox, ngoài transaction.
  - Đặt `StreamMaxLength` ≥ `XT_FILES_MAX_BYTES` trong clamd.conf.

### FB-08 — Thấp–Trung bình: cách ly audience chỉ theo một chiều

- **Hiện trạng:** `/files` dùng `foreign_audiences` của MCP ({xt-workflow, xt-data}) và thêm MCP, nên từ chối đúng. Nhưng:
  - `config.py` (MCP) không có `xt-files` trong `foreign_audiences`;
  - `workflow_http.py:106` cũng không có `xt-files`.

  Token aud `[<mcp>, xt-files]` gọi được `/mcp` (200). Token `[xt-workflow, xt-files]` qua được xác thực ở `/workflow/v1` (403 IAM, không phải 401). Điều này trái D-FILES-1: "token lộ ở bề mặt này không phát lại được ở bề mặt khác". `xt-cli` được cấp cả hai scope optional nên xin được token kép.
- **Tái hiện:** `test_mcp_rejects_token_also_for_files`, `test_workflow_rejects_token_also_for_files`.
- **Sửa:** thêm `XT_FILES_AUDIENCE or "xt-files"` vào tập foreign của MCP (`adapters/identity/config.py`) và của workflow.

### FB-10 — Trung bình (thiết kế IAM, không có test): phạm vi `file.read` quá rộng / thiếu

- **Hiện trạng:**
  - `file.reviewer-read` cho dept_head, accountant, hr… đọc **mọi** tệp trong tenant, không lọc theo phòng. Trong khi đó model `tai_lieu` giới hạn dept_head ở `scope: department`. `FilesPep` đã nạp `resource.department` nhưng policy không dùng.
  - Ngược lại, ở profile minimal (`not_scanned` + `uploader`), người duyệt không tải được tệp đính kèm, nên luồng "duyệt hồ sơ đính kèm" không chạy được.
- **Sửa:**
  - dept_head: thêm điều kiện `resource.department == subject.department`.
  - Cấp quyền đọc theo bản ghi/quy trình mà người đó đang duyệt, thay vì cấp cho cả tenant.
  - Ghi rõ giới hạn của minimal trong docs.

### FB-06 — Thấp (phòng thủ chiều sâu): multipart với CL nhỏ + `Transfer-Encoding: chunked` ghi thân không giới hạn ra đĩa

- **Hiện trạng:**
  - `_length` chỉ tin header Content-Length. h11 0.16 nhận cả CL lẫn TE và đọc theo chunked. `request.form()` của Starlette spool toàn bộ phần `file` ra tệp tạm, sau đó `Spool` mới đếm byte.
  - Đo được: với MAX 256 KiB, server nhận hết **2 MiB** rồi mới trả 413. Không có trần, nên đầy `/tmp`.
  - Toàn bộ thân được đọc **trước** kiểm IAM: tenant không có policy `file.create` vẫn bắt server nhận 25 MiB.
  - Khi đi qua APISIX/nginx (từ chối CL+TE, `max_body_size`) thì lỗi được giảm nhẹ, nhưng xt-api tự nó không an toàn.
- **Tái hiện:** `test_multipart_cl_te_does_not_spool_unbounded`.
- **Sửa:**
  - Từ chối 400 khi có `Transfer-Encoding`.
  - Bọc `request.stream()` bằng bộ đếm có trần trước `form()`.
  - Chạy IAM (`plan_upload`) trước khi đọc thân.

### FB-07 — Thấp: `text/*` chỉ kiểm 8 KiB đầu

- **Hiện trạng:** `sniff()` chỉ nhận `head` (8192 byte). Tệp `text/plain` có NUL/ELF/nhị phân sau 8 KiB vẫn được nhận, trái với docs ("Văn bản phải là UTF-8 hợp lệ, không có NUL"). Khi tải về đã có `nosniff` nên tác động thấp.
- **Tái hiện:** `test_text_validation_covers_whole_file` (201).
- **Sửa:** với họ `text`, chạy incremental UTF-8 decoder và kiểm NUL trong `Spool.write` trên toàn bộ luồng.

### FB-05 — Thấp: token không phải ASCII → 500

- **Hiện trạng:** `hmac.compare_digest(str, str)` ném `TypeError` khi có ký tự không phải ASCII. Request `PUT /files/v1/uploads/<body>.%C3%A9…` trả 500 `internal_error` và ghi log failure (dễ bị lợi dụng làm ồn log/cảnh báo).
- Ghi chú thêm: `urlsafe_b64decode` không strict nên token "dẻo" (chèn `!!!` vào vẫn hợp lệ). Không khai thác được vì link một lần, nhưng nên làm chặt.
- **Tái hiện:** `test_upload_token_non_ascii_is_403_not_500`.
- **Sửa:** kiểm token bằng `^[A-Za-z0-9_-]{1,400}\.[A-Za-z0-9_-]{43}$` trước, rồi so sánh dạng bytes.

### FB-09 — Thấp: field `file` nhận tệp của người khác (cùng tenant) và tệp chưa sẵn sàng

- **Hiện trạng:** FK ghép chỉ bảo đảm tệp tồn tại trong tenant. Employee biết id có thể gắn tệp của đồng nghiệp vào bản ghi của mình. Tệp `awaiting_upload/infected` hay đã xóa mềm cũng gắn được, chỉ bị chặn khi gửi duyệt (`_require_clean_files`). Byte vẫn phải qua `file.read`, nên không lộ nội dung, nhưng toàn vẹn hồ sơ bị ảnh hưởng (gắn chứng từ của người khác vào đề nghị).
- **Tái hiện:** `test_record_cannot_reference_colleagues_file`.
- **Sửa:** khi `create_record`/`update_record` có field `file`, đòi `file.read` (hoặc `created_by` = người tạo) và trạng thái `clean`/`not_scanned`.

### Info

- Link tải lên không kiểm lại người dùng còn hoạt động hay IAM ở thời điểm PUT. Người vừa bị vô hiệu vẫn PUT được trong TTL (tối đa 15 phút).
- Audit không ghi các lần bị 413, link sai, `already_uploaded`.
- Kết nối S3 dùng `http://` nội bộ.
- Docs ghi "Luôn từ chối HTML/SVG", nhưng thực tế chỉ từ chối khi markup đứng ở đầu tệp. Markup ở giữa tệp (hoặc `<!DOCTYPE svg`) được nhận là `text/plain` và tải về vô hại (mục 8).

## 4. Kết luận

**ĐẠT CÓ ĐIỀU KIỆN.**

- **Phần vững:** chữ ký link, cách ly tenant (RLS + tiền tố khóa + FK ghép), chống TOCTOU, policy tải về, header chống XSS, kiểm magic byte. Mọi tấn công trực tiếp vào các phần này đều thất bại.
- **Chặn trước demo công khai:**
  - FB-02 (một người dùng nội bộ làm sập kho 512 MB);
  - FB-03 (byte mọi tenant không được bảo vệ trong mạng compose);
  - FB-04 (PII dò ngược từ audit).
- **Profile full:** cần FB-01 trước khi bật ClamAV.
- **Lỗi thấp:** FB-05..09 sửa sau, có test xfail strict trong `tests/integration/files_audit/` để giữ hồi quy.

## Vòng 2 (16/10/2026, tester Opus 5.5)

Môi trường như vòng 1: 1 Postgres 17 + 1 SeaweedFS 4.48 (tmpfs, `docker rm -f -v`), uvicorn loopback. Peer cùng mạng là container `alpine` tạm (`--rm`, xóa bằng `docker rm -f -v`). Conftest đặt `XT_FILES_RETENTION_SECONDS=0` (job dọn chạy ngay) và `XT_FILES_UNSCANNED_DOWNLOAD=allow` (để kiểm riêng quyết định IAM `file.read`). Không sửa `src/` hay `deploy/`.

```bash
uv run pytest -m integration tests/integration/files_audit_r2 --no-cov -rxX
# 8 passed, 8 xfailed (strict), ~20 s, chạy lại 3 lần đều ổn định
```

| Mã | Hạng mục | KQ | Bằng chứng (`tests/integration/files_audit_r2/test_files_r2.py`) |
|---|---|---|---|
| R2-F1 | Quota tính cả byte đã xóa mềm cho tới khi dọn | **FAIL** | `test_quota_counts_soft_deleted_bytes`: tải 200 KiB → xóa, lặp 6 lần (quota 1 MiB) → cả 6 lần đều 200. `PgFileRepository.usage` lọc `WHERE deleted_at IS NULL`, nên byte còn trên S3 suốt thời gian lưu (mặc định 30 ngày) không được tính. Đối chứng `test_storage_quota_enforced_for_live_files` PASS: với tệp còn sống, lần thứ 6 trả 507 `storage_quota` |
| R2-F2 | Field `file` dạng `xrn:<t>:file:<id>` | **FAIL** | `test_record_file_field_xrn_ref_validated_like_bare_id`: `create_record(tep="xrn:fyc:file:<tệp đồng nghiệp>")` → ok, lưu thành `file:<id>`. Nguyên nhân: `FileToolService.check_attachable` chỉ bóc tiền tố `file:`, còn `xrn:...` thì `UUID()` lỗi rồi `continue`; sau đó `parse_record_ref` của records lại nhận xrn. Đối chứng PASS: `file:<id>` và id trần đều bị từ chối |
| R2-F3 | Purge theo nội dung chạy đồng thời với finalize cùng nội dung | **FAIL** | Hai cách xen kẽ, tái hiện tất định bằng hook trên `store.head` / `store.delete`. (1) finalize HEAD thấy `sha256/<h>` còn nên bỏ qua copy, purge chen vào và xóa. (2) purge đã kiểm `object_in_use` (= không), finalize commit, sau đó purge mới xóa. Cả hai: finalize 200 nhưng tải về **500** (`BlobError` ở `s3.py:stream`), byte mất hẳn. `object_in_use` không thấy hàng chưa commit, và giữa finalize với purge không có khóa nào chung |
| R2-F4 | Master SeaweedFS nhận thao tác admin/assign không xác thực | **FAIL** | `test_compose_seaweed_master_has_guard_whitelist`: `configs.seaweed-security` chỉ có 4 mục `jwt.*`, không có `[guard] white_list`. `test_seaweed_master_rejects_unauthenticated_admin_from_peer` (peer cùng mạng): `GET :9333/dir/assign` → **200 + fid**; `/col/delete?collection=<không tồn tại>` → 400 (đã qua guard, tới bước tìm collection; suy ra collection có thật sẽ bị xóa, không thử trực tiếp để tránh phá dữ liệu). JWT volume vẫn chặn ghi byte (assign không trả `auth`), nhưng xóa collection và grow volume thì mở. PASS: seaweedfs chỉ ở mạng `files` internal, không publish cổng; harness test dùng đúng các mục security.toml của compose |
| R2-F5 | Quyền đọc tệp đi theo quyền đọc bản ghi (decisions.md "Quyền đọc tệp đính kèm") | **FAIL** | Gói test `r2-chung-tu` cài thật (`xt pack install`): model `chung_tu_tt` có field `file`, kế toán đọc mọi phòng. `test_accountant_reads_attachment_of_readable_cross_department_record`: kế toán NV-0005 (D02) `get_record` được đề nghị của NV-0013 (D03), nhưng `GET /files/v1/<id>` → 404. `FilesPep` chỉ xét owner/department, không xét bản ghi đã gắn. PASS: tệp chưa gắn chỉ người tải lên và trưởng phòng cùng phòng đọc được (kế toán, HR, trưởng phòng khác, đồng nghiệp đều 404). Gắn vào bản ghi mà kế toán không đọc được thì tệp vẫn kín |
| R2-F6 | CL+TE: server ngừng đọc trong trần nhỏ, đo phía server | PASS | Đếm byte mà uvicorn/h11 thật sự đọc từ socket (`H11Protocol.data_received`, theo cổng client), client đẩy 32 MiB. Multipart CL=100 + chunked → 400, server đọc ≤ 2 MiB. PUT link CL=10 + chunked → 413, server đọc ≤ MAX + 2 MiB |
| R2-W1 | `files.maintain` trong `xt worker` | **CHƯA NỐI** | `test_worker_registers_files_maintain`: `composition.worker.handlers()` (có S3, không có ClamAV) chỉ có `model.apply, workflow.relay_timers, workflow.tick, workflow.timer_starts`. `FilesMaintenance` chưa được dùng ở đâu trong `src/`. `build_jobs` chỉ dựng files khi có `XT_FILES_CLAMD`. Integrator đang nối |

### Sửa đề xuất

- **R2-F1:** trong `usage`, tính `held` cho các hàng `deleted_at IS NULL OR object_key IS NOT NULL`. Phải đổi cả SQL lẫn index `file_object_pending`. Test sẽ XPASS khi lần thứ 6 trả 507 và sau `maintain` tải lên lại được.
- **R2-F2:** chuẩn hóa ref một chỗ, dùng chung `parse_record_ref` (nhận `file:`, `xrn:<t>:file:`, id trần; tenant khác → not_found) trong `check_attachable`. Ref không parse được thì **từ chối**, không `continue`.
- **R2-F3:** tuần tự hóa theo khóa nội dung. Cả `_promote` lẫn purge lấy `pg_advisory_xact_lock(hash(tenant, sha256))`; purge kiểm lại `object_in_use` sau khi có khóa. Hoặc: finalize luôn `copy` (ghi đè idempotent) thay vì tin HEAD, còn purge chỉ xóa khi giữ khóa.
- **R2-F4:** thêm `[guard]\nwhite_list = "127.0.0.1"` vào `seaweed-security` (S3 gateway cùng tiến trình gọi master qua loopback), và cập nhật `tests/integration/seaweed.py` cho khớp (test mirror sẽ báo nếu lệch).
- **R2-F5:** khi `file.read` bị từ chối, tìm các bản ghi (field kiểu `file`) trỏ tới tệp và cho đọc nếu người gọi `record.read` được ít nhất một bản ghi (IAM + RLS của model). Có thể dùng bảng liên kết file→record do records ghi khi create/update.
- **R2-W1:** làm theo `docs/integration-notes.md` §files: `build_jobs` luôn dựng `files` khi có `XT_FILES_S3_ENDPOINT`, `handlers()` gộp `FilesMaintenance.handlers()`, `bind-queue` gọi `register`.

### Info

- Ở profile minimal (`XT_FILES_UNSCANNED_DOWNLOAD=uploader`, mặc định của compose), chỉ người tải lên tải được byte của tệp `not_scanned`. Vì vậy, kể cả khi đã sửa R2-F5, kế toán vẫn nhận 409 `file_not_scanned` khi tải chứng từ. Cần chốt lại: dùng `allow` cho minimal, hoặc nói rõ chỉ profile full (ClamAV) mới có quyền đọc theo bản ghi.

### Kết luận vòng 2

**CHƯA ĐẠT.** R2-F6 đã sửa đúng: đo phía server, server dừng đọc ≤ 2 MiB.

- **Chặn trước demo:**
  - R2-F3 (mất byte vĩnh viễn khi job dọn chạy);
  - R2-F2 (lách FB-09 bằng xrn);
  - R2-F4 (`/col/delete` không xác thực xóa được cả bucket từ peer trong mạng `files`).
- **R2-F1 và R2-W1** phải sửa cùng nhau. Khi chưa có worker, byte xóa mềm không bao giờ được dọn và cũng không bị tính quota, nên lặp lại đúng FB-02.
- **R2-F5** là quyết định của chủ dự án nhưng chưa được hiện thực.
- Mọi lỗi đều có test `xfail(strict=True, raises=AssertionError)` mang mã R2-Fx. Setup hỏng sẽ ném `RuntimeError`, không bị xfail che.
