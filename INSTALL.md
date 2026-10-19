<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->
# Cài đặt và dịch xT từ mã nguồn

Hướng dẫn cho Ubuntu 24.04 sạch, chỉ dùng công cụ mã nguồn mở: `git`, `make`, `uv`, Node qua `nvm`, Docker Engine (hoặc Podman). Không cần Docker Desktop, không cần dịch vụ đóng, **không phải sửa tay tệp header hay mã nguồn nào**: mọi cấu hình đặt trong tệp `.env` *trước* khi dịch/chạy.

Ký hiệu: **[đã chạy]** = đã chạy thật ngày 16/10/2026 và có output; **[Docker: có output trong docs/deploy.md]** = bước cần Docker, kết quả thật ghi ở tài liệu đó; **[chưa kiểm chứng]** = chưa chạy.

## 1. Yêu cầu

| Công cụ | Phiên bản (đã dùng) | Giấy phép | Ghi chú |
|---|---|---|---|
| Ubuntu | 24.04 LTS | | Máy phát triển: Linux 6.17 |
| git, make, curl, openssl, python3 | git 2.43, GNU make 4.3, Python 3.12 (hệ thống) | | `python3` chạy script triển khai (chỉ stdlib); `openssl` sinh khóa ký |
| uv | >= 0.11 (đã dùng 0.11.25) | MIT/Apache-2.0 | Tự tải Python >= 3.12 nếu thiếu (đã chạy Python 3.13) |
| Node.js | 24 LTS qua nvm (đã dùng v24.14.1, CI dùng 24) | MIT | Dịch/kiểm thử/chạy bản hợp nhất Desk, Studio và WareTrack 3D |
| Docker Engine + Compose v2 | 29.1.3 | Apache-2.0 | Chỉ cần để chạy stack (`up-*`, `test-integration`). **Podman** (`podman compose`): [chưa kiểm chứng] |

Cài công cụ (đã chạy phần `uv`/`nvm` trên máy phát triển; cài mới từ đầu: [chưa kiểm chứng] vì máy đã có sẵn):

```sh
sudo apt-get update && sudo apt-get install -y git make curl openssl python3 ca-certificates
curl -LsSf https://astral.sh/uv/install.sh | sh                       # uv
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash
. ~/.nvm/nvm.sh && nvm install 24                                      # Node
sudo apt-get install -y docker.io docker-compose-v2                    # Docker Engine (không phải Desktop)
sudo usermod -aG docker "$USER"                                        # đăng nhập lại sau lệnh này
```

Dung lượng: cần khoảng 3 GB đĩa trống cho ảnh Docker của profile `minimal` (ảnh nền ~2,1 GB, xem mục 5) cộng ~1 GB cho `.venv`, `node_modules` và Chromium của Playwright.

## 2. Lấy mã nguồn

```sh
git clone https://github.com/chinhanxt/xT-MCP.git xT && cd xT
# Hoặc: tar xzf xt-<version>.tar.gz && cd xt-<version>
```

Bản phát hành `xt-<version>.tar.gz` kèm `SHA256SUMS` và SBOM CycloneDX: kiểm bằng `sha256sum -c SHA256SUMS`. Tarball tạo từ `git archive`, không chứa `node_modules` hay `.venv`; nguồn tích hợp trong `imports/` giữ thông báo giấy phép riêng.

## 3. Cấu hình trước khi dịch

Mọi cấu hình là biến môi trường trong tệp `.env`; không có tệp header nào để sửa. Có ba nơi:

| Phạm vi | Tệp | Cách tạo | Nội dung chính |
|---|---|---|---|
| Stack Docker (Tầng 1, xt-api, xt-worker, Desk trong Compose) | `deploy/.env` | `make -C deploy env` (hoặc tự động khi `make up-minimal`): sao từ `deploy/.env.example`, quyền 600, **tự sinh mọi bí mật còn trống** và không ghi đè giá trị có sẵn | Hostname (`XT_DOMAIN_*`), cổng (`APISIX_HTTP_PORT`, `KC_HTTP_PORT`), heap Keycloak (`KC_HEAP`), client MCP, worker (`XT_WORKER_*`), LLM (`DESK_LLM_URL`, `DESK_LLM_MODEL`, `DESK_TOOL_PROFILE`) |
| Khóa ứng dụng | `~/.config/xt/app/` (hoặc `$XT_SECRETS_DIR/app`) | Tự sinh bởi `make env`: khóa audit/cursor/PII, khóa ký biên nhận Ed25519. Ngoài repo | Không commit |
| Desk lúc dịch | `desk/.env` | `cp desk/.env.example desk/.env` rồi sửa | `DESK_MCP_URL`, `DESK_LLM_URL`, `DESK_LLM_MODEL`, `DESK_OIDC_ISSUER`, `DESK_OIDC_CLIENT_ID`, `DESK_SANDBOX_URL`, ... Thiếu biến bắt buộc thì build báo lỗi nêu tên biến |

**Profile (cờ chọn thành phần):**

| Profile | Lệnh | Dịch vụ |
|---|---|---|
| `minimal` | `make up-minimal` | postgres, keycloak, apisix, xt-init, xt-api, xt-worker (+ postgrest, xt-desk khai báo trong compose) |
| `full` | `make up-full` | `minimal` + ollama, nats, seaweedfs, clamav, openbao |
| `public` | `make up-public` | thêm `cloudflared` (cần `TUNNEL_TOKEN`); **không** nằm trên đường cài mặc định |

Sửa hostname xong chạy lại `make -C deploy render` (sinh `deploy/apisix/rendered/apisix.yaml`; `up-*` tự gọi).

## 4. Các target `make`

Đã đối chiếu từng target với `Makefile` và `deploy/Makefile`.

| Target | Làm gì | Trạng thái |
|---|---|---|
| `make setup` | `uv sync`: tạo `.venv`, cài xt và công cụ dev theo `uv.lock` | [đã chạy] |
| `make unified-setup` | `npm ci` theo lockfile của Studio, WareTrack 3D và Desk | Lệnh đối chiếu với `scripts/unified.mjs`; xem hướng dẫn bản hợp nhất bên dưới |
| `make unified-build` | Build ba frontend, gộp Studio/WareTrack vào `desk/dist/` | Bằng chứng và giới hạn: [nhật ký thực thi](docs/archive/unified-execution.md) |
| `make unified-test` | Test Studio, WareTrack 3D và Desk theo `npm test` của từng thành phần; không chạy backend/Playwright | Lệnh đối chiếu với `scripts/unified.mjs` và ba `package.json` |
| `make unified-start` | Build lại theo runtime env rồi chạy lõi, Studio và portal local; không tạo hạ tầng/seed | Cần `XT_UNIFIED_ENV_FILE`; xem [unified-local.md](docs/unified-local.md) |
| `make lint` | `ruff check`, `ruff format --check`, `mypy` (strict) | [đã chạy]: ruff xanh; mypy xanh khi mã nguồn nhất quán (lần chạy 16/10 báo 1 lỗi kiểu trong `src/` đang được sửa dở, không thuộc phần tài liệu/phát hành) |
| `make boundaries` | `lint-imports`: ranh giới module/lớp | [đã chạy] 4 contract kept, 0 broken |
| `make reuse` | `uv run reuse lint`: giấy phép từng tệp | [đã chạy] tuân thủ REUSE 3.3 (cả trong tarball phát hành: 948/948 tệp) |
| `make test` | `pytest` (unit, không cần Docker) | [đã chạy trên clone sạch HEAD dcd2a9f] 1588 passed, 395 deselected (tích hợp + chậm), 5 xfailed, ~55 s |
| `make check` | lint + boundaries + reuse + test (+ `desk-test` nếu có `desk/node_modules`) + `scripts/check_spdx.sh .` | [đã chạy] |
| `make verify-clean` | Như giám khảo: `git clone` HEAD ra `/tmp/xt-clean-*`, chạy `make setup`, `make check`, `make release`, kiểm tarball (không đề thi/`.env`/khóa/`e2e/reports`, có LICENSE/NOTICE/THIRD_PARTY_NOTICES, REUSE trong tarball) rồi xóa clone (`XT_KEEP_CLONE=1` để giữ lại). Cảnh báo nếu `src/`, `desk/src/`, `tests/`... còn tệp chưa commit | [đã chạy 16/10 trên HEAD dcd2a9f] |
| `make test-integration` | MỘT lần pytest cho mọi suite tích hợp (Keycloak 26.6.3, postgres:17, PostgREST, SeaweedFS thật trong Docker); mỗi suite container tên/cổng riêng, dọn bằng `docker rm -f -v`. Không tự pull ảnh: kéo trước (mục "Ảnh cho test tích hợp") | [đã chạy trên clone sạch HEAD dcd2a9f] 376 passed, 15 xfailed, 5 phút 40 s, RAM cần ≥ 4 GB trống |
| `make desk-setup` | `npm ci` + cài Chromium headless cho Playwright (qua nvm) | [đã chạy trên clone sạch]. Ubuntu mới còn thiếu thư viện hệ thống của Chromium: chạy thêm `cd desk && npx playwright install-deps chromium` (cần sudo) |
| `make desk-build` | `tsc --noEmit` + `vite build` → `desk/dist/`; nạp `desk/.env` (hoặc `desk/.env.example` nếu chưa có) | [đã chạy] build thành công |
| `make desk-test` | `vitest` + Playwright e2e (LLM giả, OIDC giả, MCP giả) | [đã chạy trên clone sạch] 144 vitest + 19 Playwright e2e passed. E2E dùng cổng cố định 195xx: không chạy hai phiên `desk-test` cùng lúc (lỗi `... is already used`) |
| `make desk-e2e-local` | e2e với Ollama thật | cần Ollama chạy sẵn, [chưa kiểm chứng trong tài liệu này] |
| `make release` | `scripts/release.sh`: wheel, `xt-<version>.tar.gz`, SBOM Python + Desk, `SHA256SUMS` trong `dist/` | [đã chạy] mục 6 |
| `make up-minimal` / `up-full` / `up-public` | sinh `deploy/.env`, render APISIX, `docker compose --profile ... up -d --build` | [Docker: có output trong docs/deploy.md] cho `minimal` (postgres, keycloak, apisix, xt-init, xt-api, xt-worker); `full`/`public` [chưa kiểm chứng] |
| `make provision-realm` | tạo/cập nhật realm Keycloak `xt` (idempotent) | [Docker: có output trong docs/deploy.md] |
| `make down` | dừng stack; `make -C deploy clean` xóa cả volume | [Docker: dùng trong các phiên kiểm] |
| `make -C deploy test-offline` | 18 test của script provision/JWKS với Keycloak giả, không Docker | [đã chạy] OK |
| `make check-worker`, `check-data`, `test-worker-integration`, `test-data-integration` | kiểm tập con khi nhiều người cùng sửa | dành cho phát triển |

### Trình tự dịch và kiểm tra (không cần Docker)

```sh
make setup
uv run xt --version        # xt 0.1.0.dev0
uv run xt doctor           # python: ok; postgres: unknown nếu chưa đặt XT_DATABASE_URL
make check                 # chạy đủ bước ở bảng trên
make desk-setup            # một lần
cp desk/.env.example desk/.env   # chỉnh nếu cần, rồi:
make desk-build
make desk-test
```

### Bản hợp nhất Desk, Studio và WareTrack 3D

Sau `make setup`, dùng chuỗi lệnh chung thay cho các bước Desk riêng:

```sh
make unified-setup
make unified-build
make unified-test
XT_UNIFIED_ENV_FILE="$HOME/.config/xt/unified.env" make unified-start
```

Ba bước setup/build/test không cần Docker. Bước start cần PostgreSQL/Keycloak đã provision và cấu hình runtime quyền 0600, có kết nối host hợp lệ; launcher không tạo tenant, không cấp quyền và không seed. Cổng mặc định: lõi `127.0.0.1:8010`, Studio `127.0.0.1:4327`, portal `http://localhost:5173` (Studio tại `/studio/`, WareTrack tại `/waretrack/`). Ctrl+C dừng các tiến trình launcher đã tạo; cổng bị chiếm làm launcher báo lỗi.

Biến môi trường, redirect OIDC và giới hạn kiểm thử: [docs/unified-local.md](docs/unified-local.md). Sao lưu PostgreSQL/tệp/Studio kèm khóa, health check và truy vết lỗi: [docs/unified-ops.md](docs/unified-ops.md). [Sơ đồ kiến trúc hợp nhất](docs/diagrams/xt-unified-architecture.html); [kế hoạch G7](docs/archive/2026-10-17-unified-xt-studio-waretrack.md) và [tiến độ/bằng chứng](docs/archive/unified-execution.md).

### Ứng dụng Tầng 3: cài, tắt, bật lại

CLI quản trị dùng `XT_DB_MIGRATOR_URL` (role `xt_migrator`) và cấu hình khóa audit theo [Model Engine](docs/model-engine.md). Ví dụ tenant `acme` đã tồn tại, gói và các phụ thuộc đã được kiểm tra:

```sh
uv run xt pack install --tenant acme packages/kho-hang --proposed-by nguoi-de-xuat --approved-by nguoi-duyet
uv run xt pack disable --tenant acme kho-hang --proposed-by nguoi-de-xuat --approved-by nguoi-duyet
uv run xt pack enable --tenant acme kho-hang --proposed-by nguoi-de-xuat --approved-by nguoi-duyet
```

Mỗi dòng là bước **xem kế hoạch**, chưa áp; với từng thao tác, người duyệt kiểm kế hoạch/digest rồi chạy lại đúng lệnh đó, thêm `--confirm 'sha256:…'` bằng digest vừa in. Chỉ chuyển sang thao tác kế tiếp sau khi thao tác trước đã áp; enable chỉ dùng khi gói đang tắt. Người duyệt phải khác người đề xuất. CLI kiểm lại digest trước khi áp và ghi nhận hai người; đây là đường quản trị dùng credential migrator, không thay thế xác thực người duyệt qua Desk.

Disable giữ dữ liệu, ẩn model và chặn ghi theo tenant; từ chối nếu còn gói đang bật phụ thuộc. Gói cài từ trước khi có metadata vòng đời cần cài lại trước khi tắt/bật. Không có lệnh gỡ/xóa dữ liệu trong luồng này.

### Sau nâng cấp: PostgREST và checkpoint audit

Sao lưu theo [unified-ops.md](docs/unified-ops.md) trước khi nâng cấp. Với database đã bootstrap, hoàn tất bước quản trị **trước khi mở lại `/data`**:

```sh
# Nạp XT_DATA_SUPERUSER_URL từ secret vào môi trường, không truyền DSN qua argv.
# Cấu hình XT_PGRST_PASSWORD, XT_DATA_CLIENTS và XT_PGRST_ISSUER
# (hoặc XT_DOMAIN_AUTH/XT_REALM) theo docs/headless-data.md.
make -C deploy pgrst-db
```

Target gọi `deploy/scripts/configure_data.py`: cài audit, migration nền và cập nhật gate PostgREST bằng superuser, rồi cấu hình authenticator/issuer/allowlist client. `uv run xt db migrate` riêng chưa đủ: view mới trả 0 dòng khi gate cũ chưa có `disabled_models` (fail-closed). Không cấp credential superuser cho service PostgREST. Xem [docs/headless-data.md](docs/headless-data.md#cài-đặt).

Sau nâng cấp hợp lệ, đối chiếu thay đổi catalog với migration đã duyệt trước khi tạo neo mới. Digest audit v2 chuẩn hóa catalog theo tên/ACL để ổn định sau restore; checkpoint v1 không so được digest hàm/catalog mới. `verify` vẫn kiểm chữ ký/chuỗi; nếu không có lỗi chuỗi, checkpoint cũ trả mã **2** và yêu cầu neo lại, không phải kết quả đạt. Mã 2 cũng dùng cho lỗi cấu hình/checkpoint (kể cả chữ ký), nên phải đọc thông báo và xử lý lỗi, không tự động neo lại chỉ vì mã thoát này. Lỗi chuỗi/catalog trả mã **1** phải được điều tra trước khi neo.

```sh
uv run xt audit verify --against-checkpoint latest
# Sau khi xác minh thay đổi và tính toàn vẹn:
uv run xt audit checkpoint --label after-upgrade-digest-v2
uv run xt audit verify --against-checkpoint latest
```

Cần `XT_DB_APP_URL`, `XT_AUDIT_CHECKPOINT_DIR` ngoài PostgreSQL; bước ký cần `XT_AUDIT_SIGNING_KEY_FILE` và `XT_AUDIT_SIGNING_KEY_ID`, bước verify cần `XT_AUDIT_VERIFY_KEY_FILE`. Giữ checkpoint cũ làm bằng chứng; sao lưu thư mục checkpoint và khóa công khai riêng vì bản lưu thống nhất không gồm chúng. Chi tiết [Model Engine](docs/model-engine.md#7a-mô-hình-đe-dọa-của-xt_migrator-quyết-định-16102026-phòng-ngừa--phát-hiện).

### Ảnh cho test tích hợp

Fixture không bao giờ tự kéo ảnh (đĩa có thể đầy). Kéo một lần (~1,5 GB, kiểm `df -h /` trước):

```sh
docker pull postgres:17-alpine
docker pull quay.io/keycloak/keycloak:26.6.3
docker pull postgrest/postgrest@sha256:d155c6718ed9a9f990d159a2ab7c0a3f16944dbb6d0a0344557421042acfe0df
docker pull chrislusf/seaweedfs@sha256:4e61d15fd35994cb1e43e1e553dff106794841fd9a99ade2fc8c8bfce4d7872d
make test-integration      # free -g: available >= 4 GB; không chạy song song với stack nặng khác
```

### Trình tự chạy stack (cần Docker Engine)

```sh
df -h /                    # cần >= 3 GB trống; free -g: available >= 4 GB
make up-minimal            # lần đầu kéo ảnh và dựng ảnh xt từ nguồn; Keycloak sẵn sàng sau ~25 s
make provision-realm
curl -s -H 'Host: xt-auth.freepro.online' 127.0.0.1:9080/realms/xt/.well-known/openid-configuration | head -c 300
make down
```

Gọi `127.0.0.1:9080` với tiêu đề `Host` là cách kiểm không cần DNS; đổi hostname ở `deploy/.env` (`XT_DOMAIN_*`) rồi `make -C deploy render`.

## 5. Dấu chân tài nguyên của profile `minimal`

Số đo thật ngày 15/10/2026, Docker Engine 29.1.3, máy 15,6 GB RAM (`research/footprint.md`):

| Chỉ tiêu | Giá trị |
|---|---|
| RAM cả stack hạ tầng (7 container, Keycloak `start --optimized`, heap tối đa 256 MB) | ~0,75 GB (idle ~1,15 GB với Keycloak `start-dev` mặc định, ~1,4 GB sau tải) |
| Ước tính cộng 2 tiến trình xt thật | ~1,3 GB tổng [ước tính, chưa đo] |
| Tổng ảnh Docker (bản Keycloak tùy biến) | ~2,06 GB, chưa gồm lớp pip của xt-api/xt-worker (ước tính +200-400 MB) |
| Trần bộ nhớ đặt trong compose | Keycloak 768m, APISIX 256m, Postgres 512m, xt-api và xt-worker 512m mỗi cái |
| Thời gian Keycloak sẵn sàng | 10-17 s (`--optimized`), 25-55 s (`start-dev`) |

Đạt mục tiêu < 4 GB RAM với biên lớn. Profile `full` thêm Ollama (qwen3.5:4b cần VRAM ~3 GB) và các dịch vụ khác: chưa đo.

## 6. Tạo bản phát hành

```sh
make release
```

Không cần Docker. Script xóa và tạo lại `dist/`; cần mạng cho `uvx`/`npx`. Commit trước khi phát hành: tarball lấy `HEAD` đã commit, gồm cả quy tắc loại tệp trong `.gitattributes` của ref đó (thay đổi chưa commit không vào tarball). Tạo trong `dist/`:

- `xt-<version>-py3-none-any.whl`
- `xt-<version>.tar.gz`: `git archive` với tiền tố `xt-<version>/`, có `LICENSE`, `README.md`, `CHANGELOG.md`, `NOTICE`. `.gitattributes` tại ref đã commit loại `.claude/worktrees`, `node_modules`, `dist`, `.venv` (kể cả thư mục lồng), `research/de_thi_OLP2026.*` (đề thi thuộc VFOSSA, xem `docs/decisions.md`) và `imports/waretrack-3d/docs/ref`.
- `xt-<version>-python.cdx.json`: SBOM CycloneDX 1.6 của môi trường runtime Python (`cyclonedx-bom`, Apache-2.0), dựng sạch từ `uv.lock`, không nhóm dev.
- `xt-<version>-desk.cdx.json`: SBOM CycloneDX 1.6 của phụ thuộc runtime Desk (`@cyclonedx/cyclonedx-npm`, Apache-2.0), đọc từ `package-lock.json`.
- `SHA256SUMS`: băm của mọi tệp trên; script tự kiểm lại bằng `sha256sum -c`.

Output thật (16/10/2026, version `0.1.0.dev0`):

```text
xt-0.1.0.dev0-desk.cdx.json: OK
xt-0.1.0.dev0-py3-none-any.whl: OK
xt-0.1.0.dev0-python.cdx.json: OK
xt-0.1.0.dev0.tar.gz: OK
```

SBOM Python có 41 thành phần, SBOM Desk có 99. Phiên bản lấy từ `pyproject.toml`; đổi số phiên bản ở đó, không ở nơi khác. Workflow `.github/workflows/release.yml` chạy đúng `scripts/release.sh` khi đẩy tag `v*` và đính kèm các tệp vào GitHub Release.

## 7. Xử lý sự cố

| Triệu chứng | Nguyên nhân và cách xử lý |
|---|---|
| `make desk-build`: `thiếu biến cấu hình bắt buộc: DESK_...` | Chưa có `desk/.env` và `desk/.env.example` không nạp được. Chạy `cp desk/.env.example desk/.env`. Giá trị có dấu cách phải đặt trong ngoặc kép, ví dụ `DESK_OIDC_SCOPE="openid mcp-xt"` |
| `make: nvm.sh: No such file` hoặc `npm: command not found` | Cài nvm và `nvm install 24`; target desk tự nạp `~/.nvm/nvm.sh` |
| `permission denied ... /var/run/docker.sock` | `sudo usermod -aG docker $USER`, đăng nhập lại |
| `no space left on device` khi kéo ảnh | Kiểm `df -h /`; dọn `docker image prune -a`; ảnh nền cần ~2,1 GB |
| `required variable ... is missing` từ compose | `deploy/.env` thiếu biến; chạy `make -C deploy env` (không ghi đè giá trị có sẵn) |
| Keycloak chưa sẵn sàng, `provision-realm` lỗi kết nối | Chờ ~25 s rồi chạy lại (idempotent); xem `docker compose -f deploy/compose.yaml logs keycloak` |
| `xt-init` thoát lỗi, `xt-api` không khởi động | Thiếu khóa ở `~/.config/xt/app`: chạy lại `make -C deploy env`; xem log `xt-init` |
| `uv sync` báo lỗi mạng | Cần truy cập PyPI; đặt `UV_INDEX_URL` nếu dùng mirror |
| `make release`: `cannot find ... cyclonedx` | Cần mạng cho `uvx --from cyclonedx-bom` và `npx @cyclonedx/cyclonedx-npm` |
| Tarball thiếu thay đổi mới | `git archive` chỉ lấy nội dung đã commit; commit trước khi `make release` |
| Clone sạch lỗi `ModuleNotFoundError` / `Cannot find module './…'` mà máy phát triển chạy được | Mã đã commit tham chiếu tệp **chưa commit**. Chạy `make verify-clean` (in danh sách tệp chưa commit trong `src/`, `desk/src/`, `tests/`…) và commit đủ trước khi phát hành |
| `make test-integration`: `thiếu image ...` / `image is missing` | Kéo ảnh ở mục "Ảnh cho test tích hợp" |
| Podman | Thử `make -C deploy up-minimal DC="podman compose --env-file .env"`; [chưa kiểm chứng] |

Báo lỗi cài đặt qua [GitHub Issues — chinhanxt/xT-MCP](https://github.com/chinhanxt/xT-MCP/issues) (mẫu **Báo lỗi**), kèm phiên bản và output lệnh đã loại bí mật.
