<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->

# Vận hành bản tích hợp: sao lưu, khôi phục, health check, truy vết lỗi

Áp dụng cho bản chạy local theo [unified-local.md](unified-local.md). **Trạng thái:** công cụ sao lưu/khôi phục `deploy/scripts/backup_unified.py` (mục 1) **đang làm**: nằm ở PR #30, chưa vào `staging`, nên lệnh ở mục 1 chưa chạy được trên nhánh này cho tới khi PR đó được duyệt. Đây là hướng dẫn cho bản tích hợp/demo (G7), **không** phải cam kết production: chưa có HA, backup tự động hay giám sát liên tục.

## 1. Sao lưu và khôi phục

Công cụ: `deploy/scripts/backup_unified.py` (stdlib, không cần cài thêm; cần `pg_dump`, `pg_restore`, `psql` trong `PATH` (client có major >= server: stack dùng postgres:17 trong khi Ubuntu 24.04 mặc định client 16; trỏ client 17 bằng `--pg-dump`/`--pg-restore`/`--psql`) nếu dùng thành phần PostgreSQL).

Một bản lưu là tệp `xt-unified-<UTC>.tar` quyền 0600 kèm `<tệp>.sha256`, gồm `manifest.json` (sha256 và kích thước từng tệp) và các thành phần chọn bằng cờ:

| Thành phần | Cờ | Nội dung |
|---|---|---|
| PostgreSQL | `--pg-url-env TÊN_BIẾN` | `pg_dump -Fc` của database. DSN nằm trong biến môi trường đó; script không đưa DSN vào argv, log hay manifest |
| Tệp đối tượng | `--objects-dir DIR` | Cây tệp đối tượng trên host. SeaweedFS chạy trong Docker volume `seaweed` (`deploy/compose.yaml`): xuất volume ra một thư mục host trước, script không đọc volume trực tiếp |
| Studio | `--studio-dir [DIR]` và `--studio-key [FILE]` (bắt buộc đi cùng nhau) | `studio.sqlite` chụp bằng `VACUUM INTO` (không chép `-wal`/`-shm`), `credential.key`, tệp còn lại trong thư mục Studio, và `studio.key` |

Mặc định Studio: `$XT_UNIFIED_STATE_DIR/studio` (hoặc `~/.local/state/xt-unified/studio`) và `$XT_STUDIO_SECRET_FILE` (hoặc `<state>/studio.key`), trùng `scripts/unified-start.mjs`. **Hai khóa phải đi cùng database**: `credential.key` mã hóa credential model/connector (`imports/dify-rea/src/store.ts:16`); Studio từ chối mở database cũ khi thiếu nó (`store.ts:22`). `studio.key` là secret HMAC giữa lõi và Studio.

### Sao lưu

```bash
export XT_PG_URL_FOR_BACKUP='postgresql://…'   # URL role đọc được toàn bộ database; đặt trong shell, không ghi vào lệnh
python3 deploy/scripts/backup_unified.py backup \
  --out "$HOME/xt-backups" \
  --pg-url-env XT_PG_URL_FOR_BACKUP \
  --objects-dir /đường/dẫn/objects \
  --studio-dir --studio-key
python3 deploy/scripts/backup_unified.py verify "$HOME/xt-backups/xt-unified-<UTC>.tar"
```

- Dừng ghi trước khi sao lưu để ba thành phần cùng một thời điểm (dừng `make unified-start`; với stack Docker, dừng `xt-api`/`xt-worker`). Script không tự khóa ghi.
- Bản lưu chứa bí mật: khóa Studio, và dump có `xt_audit.pepper` ([audit-model-engine.md](audit-model-engine.md), mục khóa audit). Lưu ngoài repo, giữ 0600, mã hóa khi đưa ra khỏi máy.
- **Thư mục checkpoint audit (`XT_AUDIT_CHECKPOINT_DIR`) nằm ngoài PostgreSQL** để làm bằng chứng; script không sao lưu nó. Sao lưu riêng cùng khóa công khai `XT_AUDIT_VERIFY_KEY_FILE`.

### Khôi phục

```bash
# 1. Dừng launcher/xt-api/xt-worker. Tạo database trống và các role cụm (xt_app, xt_migrator, ...).
# 2. Khôi phục (đích không rỗng sẽ bị từ chối):
python3 deploy/scripts/backup_unified.py restore "$HOME/xt-backups/xt-unified-<UTC>.tar" \
  --pg-url-env XT_PG_URL_FOR_RESTORE --objects-dir /đường/dẫn/objects-mới \
  --studio-dir --studio-key
# 3. Kiểm toàn vẹn audit sau khi database lên lại:
uv run xt audit verify --against-checkpoint latest
```

Quy tắc của `restore`:

- Kiểm `.sha256`, rồi manifest và sha256 từng tệp, giải nén vào thư mục tạm; chỉ khi đạt mới ghi đích. Bản bị sửa hoặc thiếu `studio.sqlite`/`credential.key`/`studio.key` bị từ chối và đích không đổi.
- Đích không rỗng (database có bảng, thư mục có tệp, tệp khóa đã tồn tại) bị từ chối. `--force` đổi tên đích cũ thành `*.pre-restore-<UTC>` (database dùng `pg_restore --clean --if-exists`, nên đã ghi đè).
- `pg_dump`/`pg_restore` giữ owner và ACL; role cụm không nằm trong dump nên phải tồn tại trước.
- Quyền sau khôi phục: tệp 0600, thư mục Studio 0700.

Chưa kiểm: restore vào PostgreSQL thật và `xt audit verify` sau restore (test tích hợp cho việc này nằm ở PR #44, chưa vào `staging`, chưa chạy); khôi phục đồng bộ ba thành phần khi hệ thống đang ghi.

## 2. Health check

| Dịch vụ | Kiểm | Kỳ vọng | Nguồn |
|---|---|---|---|
| Lõi `xt-api` | `GET /healthz` | 200 `{"status":"ok"}`; **chỉ chứng minh tiến trình sống**, không kiểm database/Keycloak | `src/xt/adapters/mcp/http_app.py:251`; healthcheck compose `deploy/compose.yaml:159` |
| Lõi, quyền | `POST /mcp` không token | 401 kèm PRM | `http_app.py:229-257` |
| Studio (Node) | `GET http://127.0.0.1:4327/` | 200 khi đã build; 503 `Chạy npm run build trước.` khi chưa | `imports/dify-rea/src/server.ts:77-92` |
| Studio, quyền | `GET /api/session` không assertion | 401 (`Thiếu xác thực xT.`) | `imports/dify-rea/src/xt-auth.ts:62` |
| Qua lõi | `GET /studio-api/session` không token | 401 `unauthenticated` | `src/xt/entrypoints/studio_http.py:101-102,209-232` |
| Portal | `GET http://localhost:5173/`, `/studio/`, `/waretrack/` | 200 sau `unified-build` | `docs/unified-local.md` |
| SeaweedFS | healthcheck compose | `cluster/healthz` + cổng S3 8333 | `deploy/compose.yaml:450` |
| Cổng bị chiếm | launcher dừng thay vì giành cổng | báo lỗi khi 8010/4327/5173 đã có dịch vụ | `docs/unified-local.md` |

`node scripts/unified-smoke.mjs --out build/unified-smoke.json` (script có trên `staging`, chưa nối vào Makefile) tự động hóa các dòng liveness và "401 không token" ở trên và ghi bằng chứng JSON; nó không chứng minh đăng nhập, tenant hay DPoP.

## 3. Truy vết lỗi theo mã yêu cầu (correlation ID)

Mã hiện có là `trace_id`:

- Mọi phản hồi tool/REST (MCP, `/workflow/v1`, `/files/v1`) chứa `meta.trace_id` ở envelope (`src/xt/application/tools/dispatcher.py:736`).
- Khi tool lỗi, log của `xt-api` ghi dòng `... trace_id=<mã> tool=<tên> exc=<kiểu lỗi> at <tệp:dòng:hàm>` (`dispatcher.py:349,352`; định dạng trong `src/xt/application/tools/safe_log.py`). Log cố ý **không** chứa message lỗi, DSN hay dữ liệu cá nhân.
- Bảng nghiệp vụ có cột `trace_id` (ví dụ `xt_meta.file_event`, `xt_meta.pii_egress`: `adapters/db/files.py:130`, `adapters/db/notify.py:173`).

Cách truy:

```bash
# Lấy trace_id từ envelope lỗi hiển thị cho người dùng/ChatGPT, rồi:
docker compose -f deploy/compose.yaml logs xt-api xt-worker | grep 'trace_id=<mã>'
```

Giới hạn đã biết (việc còn lại của G7, **chưa có**):

- Không có header `X-Request-Id` đi xuyên APISIX → lõi → Studio; trình duyệt không nhận mã nếu lỗi xảy ra trước khi có envelope.
- `/studio-api/*`: proxy tạo `trace_id` mới cho mỗi yêu cầu (`studio_http.py:239`) nhưng không trả về client, nên lỗi Studio không dò ngược được từ phía người dùng.
- Log yêu cầu của Studio Node chỉ có `METHOD path status ms` (`server.ts:212`), không có mã yêu cầu.
- Truy ngược actor → phiếu → giao dịch → báo cáo (lớp 7) dựa vào audit và `xt audit verify`; báo cáo D phụ thuộc `aggregate_records`, hiện còn `unavailable` ở lõi (`src/xt/entrypoints/composition/records.py:364`; D-UNI-AGG-1 chưa hiện thực).
