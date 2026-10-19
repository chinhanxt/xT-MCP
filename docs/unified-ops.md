<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->

# Vận hành bản tích hợp: sao lưu, khôi phục, health check, truy vết lỗi

Áp dụng cho bản chạy local theo [unified-local.md](unified-local.md) và bản Docker Compose (`deploy/compose.yaml`, mục 4). Đây là hướng dẫn cho bản tích hợp/demo (G7), **không** phải cam kết production: chưa có HA, backup tự động hay giám sát liên tục.

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
- Bản lưu chứa bí mật: khóa Studio, và dump có `xt_audit.pepper` ([audit-model-engine.md](archive/audit-model-engine.md), mục khóa audit). Lưu ngoài repo, giữ 0600, mã hóa khi đưa ra khỏi máy.
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

- Kiểm `.sha256`, rồi manifest và sha256 từng tệp, giải nén vào thư mục tạm; chỉ khi đạt mới ghi đích. Bản bị sửa hoặc thiếu `studio.sqlite`/`credential.key`/`studio.key` bị từ chối và đích không đổi. Thiếu tệp `.sha256` cũng là lỗi (cả `verify` lẫn `restore`, thoát mã khác 0) trừ khi thêm `--no-sidecar`: manifest nằm cùng tar nên không chống được việc sửa có chủ ý.
- Thứ tự: (1) kiểm bản lưu và kiểm tra trước cả ba đích (PostgreSQL, thư mục đối tượng, thư mục Studio cùng `credential.key`/`studio.key`: loại đích, quyền ghi, đích không rỗng); (2) dựng objects/Studio vào thư mục tạm cạnh đích; (3) đổi chỗ nguyên tử (rename) objects/Studio, **ghi PostgreSQL sau cùng**. Lỗi ở bước 1-2 không đụng đích nào; lỗi khi đổi chỗ thì hoàn tác; nếu `pg_restore` lỗi thì tệp đã đổi chỗ được giữ (khớp bản lưu).
- Đích không rỗng (database có bảng, thư mục có tệp, tệp khóa đã tồn tại) bị từ chối. `--force` đổi tên đích cũ thành `*.pre-restore-<UTC có micro giây>-<6 ký tự uuid>` (database dùng `pg_restore --clean --if-exists`, nên đã ghi đè).
- `pg_dump`/`pg_restore` giữ owner và ACL; role cụm không nằm trong dump nên phải tồn tại trước.
- Quyền sau khôi phục: tệp 0600, thư mục Studio 0700.

Test restore PostgreSQL thật của PR #44 đã vào `staging`; [nhật ký thực thi](archive/unified-execution.md) ghi 1/1 đạt với client PG17, PR #83 cập nhật docstring theo lượt chạy đó. Đây là bằng chứng đã ghi nhận, không phải lượt kiểm mới. Giới hạn: `--clean --if-exists` không xóa đối tượng có trong database đích mà không có trong dump (nên restore vào database trống); khôi phục đồng bộ ba thành phần khi hệ thống đang ghi chưa được kiểm chứng.

Nếu khôi phục đi kèm nâng cấp hoặc checkpoint còn digest v1, làm theo [quy trình nâng cấp và neo lại checkpoint](../INSTALL.md#sau-nâng-cấp-postgrest-và-checkpoint-audit). Không coi mã thoát 2 là đã kiểm toàn vẹn; giữ checkpoint cũ và xác minh thay đổi trước khi tạo neo v2.

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

Có hai mã:

- **`X-Request-Id`** (PR #28, đã vào `staging`): proxy `/studio-api/*` tạo `request_id` cho mỗi yêu cầu (`studio_http.py:107`), trả về client ở header `x-request-id` trên phản hồi thành công, 401 và 502 (`studio_http.py:110`), ký vào assertion gửi Studio (`request_id` trong `xt-auth.ts`), và Studio ghi vào log yêu cầu dạng `METHOD path status ms request_id=<mã>` (`server.ts:215`). Cũng là `trace_id` của envelope lỗi do proxy tạo (`studio_http.py:254`). Truy: lấy `x-request-id` từ phản hồi lỗi, rồi `grep 'request_id=<mã>'` trong log Studio và `trace_id=<mã>` trong log `xt-api`.
- **`trace_id`**:

- Mọi phản hồi tool/REST (MCP, `/workflow/v1`, `/files/v1`) chứa `meta.trace_id` ở envelope (`src/xt/application/tools/dispatcher.py:736`).
- Khi tool lỗi, log của `xt-api` ghi dòng `... trace_id=<mã> tool=<tên> exc=<kiểu lỗi> at <tệp:dòng:hàm>` (`dispatcher.py:349,352`; định dạng trong `src/xt/application/tools/safe_log.py`). Log cố ý **không** chứa message lỗi, DSN hay dữ liệu cá nhân.
- Bảng nghiệp vụ có cột `trace_id` (ví dụ `xt_meta.file_event`, `xt_meta.pii_egress`: `adapters/db/files.py:130`, `adapters/db/notify.py:173`).

Cách truy:

```bash
# Lấy trace_id từ envelope lỗi hiển thị cho người dùng/ChatGPT, rồi:
docker compose -f deploy/compose.yaml logs xt-api xt-worker | grep 'trace_id=<mã>'
```

Khả năng truy nguồn và giới hạn:

- `X-Request-Id` chỉ có trên đường `/studio-api/*`; các route MCP, `/workflow/v1`, `/files/v1` vẫn chỉ có `meta.trace_id` trong envelope, và trình duyệt không nhận mã nếu lỗi xảy ra trước khi có envelope.
- Báo cáo D dùng `aggregate_records` đã hiện thực, có `as_of` và `lineage_ref` dạng `explain:<trace_id>`. `get_history` mở tham chiếu này, kiểm lại quyền và trả truy vấn nguồn trong thời hạn 24 giờ (PR #72). Đọc lại truy vấn dùng dữ liệu hiện tại, không phải ảnh chụp lịch sử; kiểm toàn vẹn audit bằng `xt audit verify` không thay thế đối soát số liệu báo cáo.

## 4. Bản tích hợp trên Docker Compose (demo công khai)

`deploy/compose.yaml` chạy cùng bản tích hợp với launcher local, theo cách nối dây sau:

| Thành phần | Compose | Ghi chú |
|---|---|---|
| Portal | `xt-desk` (ảnh `xt/desk:dev`) | Build từ gốc repo bằng `scripts/unified.mjs build`, gồm Desk, `/studio/` và `/waretrack/`. nginx phục vụ hai đường dẫn này với CSP riêng (`desk/nginx.conf`). Biến `DESK_*` được nướng vào ảnh lúc build, vì vậy đổi `DESK_OIDC_ORGANIZATIONS` thì phải build lại |
| Studio | `studio` (ảnh `xt/studio:dev`, `deploy/docker/studio.Dockerfile`) | Node 24, `STUDIO_XT_MODE=1`, nghe `0.0.0.0:4327` trong mạng compose. Không publish cổng, không có route APISIX trực tiếp. Dữ liệu SQLite và `credential.key` nằm ở volume `<project>_studio` (`/data/studio`) |
| Khóa HMAC | `${XT_SECRETS_DIR}/app/studio.key` | `make env` sinh nếu chưa có và không ghi đè. xt-api đọc ở `/run/secrets/xt/studio.key`. Studio chỉ được mount đúng tệp này, không thấy khóa audit/PII |
| Proxy | `xt-api`: `XT_STUDIO_URL=http://studio:4327`, `XT_STUDIO_SECRET_FILE` | Route APISIX `desk-studio` chuyển `/studio-api/*` trên host Desk tới xt-api. xt-api kiểm token Desk (DPoP, origin, tenant) rồi ký assertion gửi Studio. Studio gọi lại `http://xt-api:8000/studio-internal/tools`; đường này không có route công khai |
| Liveness qua gateway | route `desk-healthz`: `GET /healthz` trên host Desk | Trả `{"status":"ok"}` của xt-api, dùng cho `unified-smoke.mjs` |

Mọi dịch vụ chạy lâu (`postgres`, `keycloak`, `apisix`, `postgrest`, `seaweedfs`, `xt-api`, `xt-worker`, `xt-desk`, `studio`) dùng `restart: unless-stopped`. `xt-init` chạy một lần (`restart: "no"`).

### Route riêng của máy

Một dịch vụ khác dùng chung APISIX/tunnel được khai trong `deploy/apisix/extra-routes.json`. Tệp này không commit (đã có trong `deploy/.gitignore`); mẫu ở `extra-routes.example.json`, có thể đổi đường dẫn bằng `XT_APISIX_EXTRA_ROUTES`. `make render` (`scripts/render_apisix.py`) chèn các route này trước `#END` và từ chối render khi:

- route không khai `hosts` tường minh, hoặc dùng `host`;
- route dùng hostname trùng một trong các `XT_DOMAIN_*`, nên không thể chiếm `xt-mcp` hay host Desk;
- `id` trùng với route có sẵn.

### Smoke qua gateway

```bash
node scripts/unified-smoke.mjs --core https://<XT_DOMAIN_DESK> --portal https://<XT_DOMAIN_DESK> \
  --issuer https://<XT_DOMAIN_AUTH>/realms/xt --out build/unified-smoke-public.json
```

Trên host Desk, `/healthz`, `/mcp` và `/studio-api/*` đều được định tuyến tới xt-api, nên cả 7 kiểm đều đi qua gateway. Muốn chạy local trước khi bật tunnel, dùng container node trên mạng project, `--add-host <hostname>:<IP APISIX>` và `http://`.

### Sao lưu Studio của stack compose

```bash
P=<project>; OUT=$HOME/xt-backups/<UTC>; mkdir -m 700 -p "$OUT/studio-volume"
docker compose -p "$P" --env-file .env stop studio            # dừng ghi
docker run --rm -v "${P}_studio:/v:ro" -v "$OUT/studio-volume:/out" alpine sh -c 'cp -a /v/. /out/'
docker compose -p "$P" --env-file .env start studio
python3 deploy/scripts/backup_unified.py backup --out "$OUT" --pg-url-env XT_PG_URL_FOR_BACKUP \
  --objects-dir <bản xuất seaweed> --studio-dir "$OUT/studio-volume/studio" --studio-key "$XT_SECRETS_DIR/app/studio.key"
```

Khi khôi phục, chép thư mục Studio đã restore vào volume (`docker run … cp -a`), giữ owner uid 1000, và đặt `studio.key` trở lại `${XT_SECRETS_DIR}/app/`.

### LLM của Desk đang tắt

Nếu `XT_DESK_LLM_URL` rỗng và `DESK_LLM_URL` trỏ tới dịch vụ không chạy (ví dụ `ollama` thuộc profile `full`), `/llm/chat/completions` trả 404 (route chưa đăng ký) hoặc 502 `llm_unavailable`. Khi đó khung chat Desk hiện ghi chú "Chưa cấu hình LLM: …" thay cho lỗi HTTP thô (`desk/src/llm.ts`, `LlmUnavailableError`). 401, 413 và 429 vẫn hiện là lỗi thật. Các không gian nghiệp vụ, Studio và WareTrack không phụ thuộc LLM của Desk.

### Gán vai trò gói cho người demo (luồng sản phẩm)

Vai trò gói (`warehouse_manager`, `sales`, `sales_manager`, `project_manager`, `facility_manager`, `technician`) được cấp qua `access_grant` và bước IT của `employee_onboarding` (gói `nhan-su-vong-doi` >= 0.4.2). Không ghi thẳng vào bảng. Công cụ vận hành:

```bash
cd e2e   # của worktree triển khai; deploy/.env trỏ đúng stack
XT_E2E_PROJECT=<project> PYTHONPATH=. uv run --project .. python -m xte2e.demo_roles demo-roles.binhminh.json           # chỉ in kế hoạch
XT_E2E_PROJECT=<project> PYTHONPATH=. uv run --project .. python -m xte2e.demo_roles demo-roles.binhminh.json --apply   # chạy
```

SoD được kiểm trước khi ghi và được kiểm lại ở op `sync_access_grants`: người duyệt IT phải khác HR (người đề nghị), khác người tạo grant và khác người được cấp. Mỗi bước human-only được xác nhận trên `/desk/confirm` bằng mật khẩu + TOTP. Nếu một lần xác nhận bị từ chối hoặc hết hạn, chạy lại với `--attempt N` để bước đó có idempotency_key mới.

Theo D-UNI-AGENT-1, `role:agent` không được ghi danh mục (kho, vị trí, hàng hóa, khách hàng CRM). Desk và connector đều gọi MCP bằng client agent. Muốn một người quản lý danh mục qua Desk, người vận hành phải thêm vai trò người đó vào agent grant một cách tường minh bằng `xt org import --links`, ví dụ `roles: ["agent","employee","warehouse_manager"]`, rồi ghi lại trong nhật ký triển khai.
