<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->
# REST `/files/v1`: dữ liệu phi cấu trúc (Tầng 1)

Dịch vụ thứ tư của Tầng 1 theo đề thi ("Quản trị dữ liệu phi cấu trúc"). Byte của tệp nằm trên **SeaweedFS** (S3, Apache-2.0). Metadata nằm trong Postgres (`xt_meta.file_object`), có RLS theo tenant và audit hash-chain. Quét virus bằng **ClamAV** ở profile `full`. Mọi lời gọi đi qua APISIX (host `xt-api`) tới `xt-api`. SeaweedFS không có cổng hay route nào ra ngoài.

Mã nguồn:

| Lớp | Tệp |
|---|---|
| Cổng + vòng đời + nhận dạng kiểu tệp | `src/xt/application/files/{ports,service,sniff}.py` |
| S3 (AWS SigV4 trên httpx) + clamd | `src/xt/adapters/blob/{s3,clamd}.py` |
| Metadata Postgres | `src/xt/adapters/db/files.py`, migration `0009_files.sql` |
| REST (Starlette) + OpenAPI 3.1 | `src/xt/adapters/http/files_api.py` |
| Nối vào `xt serve` + tool `upload_file` | `src/xt/entrypoints/files_http.py` |
| Quyền (gói dữ liệu) | `packages/tep-dinh-kem/` (policy `file.*` + model `tai_lieu`) |

## 1. Luồng

```text
agent/curl --Bearer(aud xt-files)--> POST /files/v1 {name, content_type}
           <-- 201 {file{id, status: awaiting_upload}, put_url, upload_url, expires_at}
người/curl --(không Bearer)--------> PUT put_url  (byte nguyên văn)
           xt-api: đếm (chặn quá hạn mức), sha256, kiểm magic byte, ghi quarantine/ trên S3
           <-- 200 {file{status: uploaded, sha256}}
agent/curl --Bearer--> POST /files/v1/{id}/finalize {sha256}
           sha256 khớp -> scanning (full: ClamAV) | not_scanned (minimal)
           -> copy sang khóa theo nội dung t/<tenant>/sha256/<hh>/<hex>, xóa quarantine
agent/curl --Bearer--> GET /files/v1/{id}/content   (luồng, ETag = sha256)
```

- **Multipart một bước:** `POST /files/v1` với `multipart/form-data` (phần `file`, tùy chọn `sha256`). Lệnh này tạo ý định, nhận byte và finalize ngay. Nếu không gửi `sha256`, server dùng sha256 tự tính.
- **PUT có `Content-Digest: sha-256=:<base64>:`** (RFC 9530): finalize ngay trong cùng request. Trang tải lên cho người dùng cách này.
- **Byte không bao giờ đi qua LLM.** Tool MCP `upload_file(source=upload_link)` chỉ trả link. Người dùng mở `upload_url` (trang HTML, CSP chặt) hoặc client dùng `put_url`.

## 2. Endpoint

| Method + đường dẫn | Xác thực | Kết quả |
|---|---|---|
| `GET /files/v1?limit=` | Bearer | Tệp tôi đã tải lên, mới nhất trước |
| `POST /files/v1` (JSON) | Bearer | 201: ý định + `put_url`. Gọi đúng tool `upload_file`, nên có IAM, idempotency, audit giống MCP |
| `POST /files/v1` (multipart) | Bearer | 201: tệp đã finalize |
| `PUT /files/v1/uploads/{token}` | chữ ký trong URL | 200: `uploaded` (hoặc đã finalize nếu có `Content-Digest`) |
| `GET /files/v1/uploads/{token}` | chữ ký trong URL | Trang HTML để người chọn tệp |
| `POST /files/v1/{id}/finalize` | Bearer (người tải lên) | 200: `scanning` / `clean` / `not_scanned` |
| `GET /files/v1/{id}` | Bearer (`file.read`) | Metadata |
| `GET /files/v1/{id}/content` | Bearer (`file.read`) + policy quét | Byte của tệp. Kèm `Content-Disposition`, `ETag`, `Repr-Digest`, `nosniff`, CSP sandbox |
| `DELETE /files/v1/{id}` | Bearer (`file.delete`) | Xóa mềm (`deleted_at`) |
| `GET /files/v1/openapi.json` | công khai | OpenAPI 3.1, sinh từ code |

`Idempotency-Key` của `POST /files/v1` là **tùy chọn**. Nếu gửi, request lặp lại với cùng khóa trả về đúng tệp cũ (200). Nếu không gửi, mỗi lần gọi tạo một ý định mới; ý định không dùng sẽ tự hết hạn.

### Trạng thái

| REST | Tool `upload_file` (enum TCV1 §5.13) | Nghĩa |
|---|---|---|
| `awaiting_upload` | `awaiting_upload` | Đã có ý định, chưa có byte |
| `uploaded` | `awaiting_upload` | Byte đã ở `quarantine/`, chờ finalize |
| `scanning` | `scanning` | Chờ ClamAV ("pending_scan"). Quét chạy ngoài transaction; clamd lỗi thì tăng `scan.attempts`, job `files.maintain` quét lại |
| `scan_failed` | `scan_failed` | clamd lỗi `XT_FILES_SCAN_MAX_ATTEMPTS` lần liên tiếp: bị chặn (409 `file_scan_failed`), không đính kèm được; quản trị quét lại bằng `FileManager.retry_scan` |
| `clean` | `clean` | ClamAV báo sạch |
| `not_scanned` | `not_scanned` | Profile không quét ("unscanned"). Ai được tải về do `XT_FILES_UNSCANNED_DOWNLOAD` quyết định |
| `infected` | `infected` | ClamAV phát hiện mã độc; byte đã xóa, chỉ giữ tên chữ ký |
| `expired` | `expired` | Quá hạn tải lên mà chưa finalize |

### Lỗi (envelope chung `ok:false`, như `/workflow/v1`)

| HTTP | `error.code` / `details.reason` | Khi nào |
|---|---|---|
| 401 | `unauthenticated` | Thiếu token, sai `aud` (token MCP/workflow/data không dùng được ở đây), phiên người |
| 403 | `access_denied` (`insufficient_scope`, `iam_denied`) | Thiếu scope `xt-files`; IAM từ chối (vd tenant không có policy `file.create`); đọc được nhưng không có quyền xóa |
| 403 | `upload_url_invalid` / `upload_url_expired` | Link bị sửa / hết hạn (`XT_FILES_UPLOAD_TTL_SECONDS`) |
| 404 | `not_found` | Không có, **hoặc** thuộc tenant khác, **hoặc** cùng tenant nhưng không được đọc. Ba trường hợp trả cùng một câu, không phân biệt được |
| 409 | `precondition_failed` (`already_uploaded`, `not_uploaded`, `file_not_scanned`, `file_scanning`, `file_infected`, `file_not_ready`, `upload_expired`) | Sai bước trong vòng đời / policy quét |
| 411 | `length_required` | PUT hoặc multipart không có `Content-Length` |
| 413 | `resource_constraint` (`limit_bytes`) | Vượt `XT_FILES_MAX_BYTES`. Server chặn ngay khi đếm vượt, không ghi tiếp. Multipart: trần tổng thân đếm trước khi Starlette spool ra đĩa; có `Transfer-Encoding` thì 411 |
| 429 | `resource_constraint` `pending_uploads_quota` | Quá số ý định đang mở (`XT_FILES_MAX_PENDING_PER_USER` / `_PER_TENANT`) |
| 507 | `resource_constraint` `storage_quota` | Hết quota byte của người (`XT_FILES_USER_QUOTA_BYTES`) hoặc tenant (`XT_FILES_TENANT_QUOTA_BYTES`), kiểm khi tạo ý định, khi nhận byte và khi finalize |
| 415 | `content_type_not_allowed` / `content_type_mismatch` | Kiểu không có trong danh sách cho phép, hoặc byte đầu tệp không khớp kiểu khai báo |
| 400 | `file_not_attachable` / `file_not_ready` | `create_record`/`update_record`: field `file` trỏ tới tệp người ghi không đọc được (hoặc không có), hoặc chưa `clean`/`not_scanned` |
| 422 | `checksum_mismatch` | sha256 không khớp. Server bỏ byte, tệp quay về `awaiting_upload`, có thể PUT lại khi link còn hạn |

## 3. An toàn

- **Danh tính:** audience riêng `xt-files` + scope optional `xt-files` (Keycloak, `provision_data.py`). Chỉ client trong `XT_FILES_ALLOWED_CLIENTS` (mặc định `xt-cli`) được gọi. Token còn mang aud MCP/workflow thì bị từ chối. Phiên người (client confirm) bị 401.
- **IAM** (`packages/tep-dinh-kem`):
  - `file.create` (L1): employee và agent.
  - `file.read`: người tải lên; `director` mọi tệp; `dept_head`, `chief_accountant`, `accountant`, `hr` chỉ tệp của người cùng phòng (`resource.department = subject.department`).
  - `file.delete` (xóa mềm, L1): người tải lên.
  - Agent bị kiểm hai lớp: quyền `role:agent` và quyền của người được đại diện, cùng trần tự chủ.
- **Mạng (FB-03):** `seaweedfs` (và `clamav`) chỉ nối mạng compose `files` (`internal: true`), chung với `xt-api` và `xt-worker`; không publish cổng, không route APISIX. `security.toml` bật JWT cho volume và filer: truy cập thẳng `:8080`/`:8888` trả 401, chỉ S3 (SigV4) đọc/ghi được byte. Kiểm offline: `tests/unit/test_files_deploy.py`. Kiểm trên stack thật: `docker compose exec seaweedfs wget -S -O- http://127.0.0.1:8888/buckets/xt-files/` phải ra 401; `docker compose exec postgrest getent hosts seaweedfs` không phân giải được.
- **Cô lập tenant ba lớp:**
  - RLS `tenant_id = xt.tenant()` trên `file_object` và `file_event`.
  - Khóa object có tiền tố `t/<tenant_id>/`.
  - Field `file` của model có FK ghép `(tenant_id, id)` tới `xt_meta.file_object`. Bản ghi không trỏ được tới tệp của tenant khác, kể cả khi đoán đúng id.
- **Kiểu tệp:**
  - Danh sách cho phép: PDF, PNG, JPEG, GIF, WebP, OOXML/ODF/ZIP, `text/plain|csv|markdown`, JSON.
  - Server so magic byte với kiểu khai báo.
  - Luôn từ chối: PE, ELF, Mach-O, shebang, OLE2 (Office cũ có macro), RAR, 7z, gzip, HTML/SVG/XML.
  - Văn bản phải là UTF-8 hợp lệ, không có NUL.
- **Toàn vẹn:**
  - xt-api tự tính sha256 khi nhận byte.
  - S3 PUT ký payload sha256 và kèm `x-amz-checksum-sha256`. SeaweedFS trả `BadDigest` nếu lệch (đã kiểm trên 4.48 thật).
  - Khi finalize, server so sha256 của client với sha256 tự tính, rồi đối chiếu `HEAD` của object (kích thước + sha256 lưu trong metadata).
- **Khóa theo nội dung:** hai tệp cùng nội dung trong một tenant dùng chung một object.
- **Dọn rác + quota (FB-02):** job worker `files.maintain` (mỗi tenant, mặc định 5 phút) chuyển ý định quá hạn sang `expired` và xóa byte `quarantine/`; tệp xóa mềm quá `XT_FILES_RETENTION_SECONDS` (mặc định 30 ngày) được xóa object khi không còn hàng nào cùng nội dung (sự kiện `purged`); quét lại tệp `scanning`. Kho minimal: 32 volume × 64 MB ≈ 2 GiB.
- **PII:**
  - Tên tệp là văn bản tự do (vd "CCCD_<số>.pdf"). Với client cloud (tier masked), số CCCD, điện thoại, email trong tên bị thay bằng `[PII]` ở mọi đầu ra REST/MCP.
  - Audit lưu `redacted:hmac:<kid>:<hex>` của tên: HMAC-SHA256 bằng khóa audit của tiến trình (`XT_AUDIT_KEY_FILE`), như cột sensitive của model engine; khóa không bao giờ vào DB (FB-04).
  - Lỗi đi qua `ErrorScrubber` như `/workflow/v1`.
- **Audit:**
  - Mỗi thay đổi `file_object` (INSERT/UPDATE) và mỗi sự kiện `file_event` (created, uploaded, rejected, finalized, scanned, downloaded, deleted, expired) đều nối vào hash-chain của tenant.
  - Kiểm bằng `xt_audit.verify(tenant)`.

## 4. Cấu hình (`xt-api`)

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `XT_FILES_S3_ENDPOINT` | (trống = tắt dịch vụ) | `http://seaweedfs:8333` |
| `XT_FILES_S3_ACCESS_KEY`, `XT_FILES_S3_SECRET_KEY` | `make env` tự sinh | Danh tính S3 duy nhất. Compose đưa vào `configs.seaweed-s3` |
| `XT_FILES_S3_BUCKET` / `XT_FILES_S3_REGION` | `xt-files` / `us-east-1` | Bucket tự tạo khi khởi động |
| `XT_FILES_PUBLIC_URL` | — | Gốc của `put_url`, vd `https://xt-api.freepro.online` |
| `XT_FILES_URL_KEY_FILE` | dẫn xuất từ `XT_CURSOR_KEY_FILE` | Khóa HMAC ký URL tải lên (≥ 32 byte) |
| `XT_FILES_MAX_BYTES` | 26214400 | Hạn mức một tệp. Route APISIX `api-files` cho thêm 1 MiB |
| `XT_FILES_UPLOAD_TTL_SECONDS` | 900 | Hạn `put_url` / `upload_url` |
| `XT_FILES_UNSCANNED_DOWNLOAD` | `uploader` | `deny` \| `uploader` \| `allow` cho tệp `not_scanned` |
| `XT_FILES_CLAMD` | trống | `clamav:3310` ở profile `full` (bật quét) |
| `XT_FILES_SCAN_MAX_ATTEMPTS` | 5 | Lần clamd lỗi trước khi `scan_failed` |
| `XT_FILES_MAX_PENDING_PER_USER` / `_PER_TENANT` | 20 / 500 | Ý định đang mở |
| `XT_FILES_USER_QUOTA_BYTES` / `XT_FILES_TENANT_QUOTA_BYTES` | 256 MiB / 1 GiB | Quota byte |
| `XT_FILES_RETENTION_SECONDS` | 2592000 | Giữ byte của tệp xóa mềm |
| `XT_FILES_ALLOWED_TYPES` | toàn bộ danh sách | Tập con để thu hẹp |
| `XT_FILES_AUDIENCE` / `XT_FILES_REQUIRED_SCOPES` / `XT_FILES_ALLOWED_CLIENTS` | `xt-files` / `xt-files` / `XT_MCP_ALLOWED_CLIENTS` | Như workflow |
| `XT_MCP_CAPABILITIES` | phải gồm `upload_file` | Bật tool gated `upload_file` |

Compose `minimal`:
- Một container `seaweedfs` (`weed server -s3`, volume 64 MB, tối đa 32 volume, `mem_limit 512m`, user `seaweed` uid 1000, `read_only`, `cap_drop ALL`, mạng `files` nội bộ, `configs` `s3.json` + `security.toml` sinh từ `.env`).
- Ảnh pin digest 4.48. Healthcheck gồm master `/cluster/healthz` và cổng S3.
- Profile `full` thêm `clamav`. Đặt `XT_FILES_CLAMD=clamav:3310` trong `deploy/.env`.

## 5. Ví dụ curl

```bash
# Token files (một lần): PKCE loopback, client xt-cli, scope xt-files
bash docs/headless-demo.sh --get-token files ~/.config/xt/files_token
API=https://xt-api.freepro.online; H="Authorization: Bearer $(cat ~/.config/xt/files_token)"

# 1) Ý định -> 2) PUT -> 3) finalize -> 4) tải về
R=$(curl -s -H "$H" -H 'Content-Type: application/json' -d '{"name":"hoadon.pdf"}' $API/files/v1)
ID=$(echo "$R" | python3 -c 'import json,sys;print(json.load(sys.stdin)["data"]["file"]["id"])')
PUT=$(echo "$R" | python3 -c 'import json,sys;print(json.load(sys.stdin)["data"]["put_url"])')
curl -s -X PUT --data-binary @hoadon.pdf -H 'Content-Type: application/pdf' "$PUT"
curl -s -H "$H" -H 'Content-Type: application/json' \
  -d "{\"sha256\":\"$(sha256sum hoadon.pdf | cut -d' ' -f1)\"}" $API/files/v1/$ID/finalize
curl -s -H "$H" -o tai-ve.pdf $API/files/v1/$ID/content

# Một bước (multipart)
curl -s -H "$H" -F 'file=@bien-ban.txt;type=text/plain' $API/files/v1
```

Bước 5 của `docs/headless-demo.sh` chạy đúng chuỗi này (kể cả multipart). Test tích hợp `tests/integration/composition/test_files_api.py::test_headless_demo_files_step` chạy script này thật trên SeaweedFS + Postgres.

## 6. MCP và field `file`

- `upload_file` (TCV1 §5.13):
  - `source=text|base64`: byte nhỏ (≤ 1 MiB) có sẵn trong lời gọi, lưu và finalize ngay.
  - `source=upload_link`: trả `upload_url` (trang cho người) và `put_url` (client), hạn ngắn.
  - `data.file.ref` = `file:<uuid>`.
- Gán ref đó vào field kiểu `file` của bản ghi, vd `create_record(model_id="tai_lieu", values={"tieu_de": "...", "tep": "file:<uuid>"})`. DB chặn id bịa hoặc id của tenant khác bằng FK ghép. Thông báo lỗi tiếng Việt lấy từ `constraint_map`.

## 7. Kiểm chứng

- Unit (port giả):
  - `tests/unit/files/` (vòng đời, nhận dạng kiểu, chữ ký URL, cấu hình, SigV4).
  - `tests/unit/http/test_files_api.py` (định tuyến, mã HTTP, Content-Digest, trang, OpenAPI).
- Tích hợp (`-m integration`, Postgres 17 + SeaweedFS 4.48 thật, container tên riêng, cổng ngẫu nhiên, tmpfs, `docker rm -f -v`):
  - roundtrip ý định → PUT → finalize → tải về;
  - multipart và Idempotency-Key;
  - tenant khác / không được đọc / id bịa cùng ra 404;
  - checksum lệch, quá hạn mức, kiểu cấm (magic byte);
  - link hết hạn hoặc bị sửa;
  - IAM từ chối;
  - policy tệp chưa quét;
  - xóa mềm + audit hash-chain (tên chỉ lưu sha256) + `xt_audit.verify`;
  - MCP `upload_file` + field `file` (FK);
  - bước 5 của headless-demo bằng curl thật.

```bash
uv run pytest -m integration tests/integration/composition/test_files_api.py --no-cov
```
