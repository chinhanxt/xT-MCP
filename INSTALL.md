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
| Node.js | 24 LTS qua nvm (đã dùng v24.14.1, CI dùng 24) | MIT | Chỉ cần để dịch/kiểm thử Desk |
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
git clone <URL-repo-công-khai> xT && cd xT      # hoặc: tar xzf xt-<version>.tar.gz && cd xt-<version>
```

Bản phát hành `xt-<version>.tar.gz` kèm `SHA256SUMS` và SBOM CycloneDX: kiểm bằng `sha256sum -c SHA256SUMS`. Tarball tạo từ `git archive`, **không** chứa `node_modules`, `.venv` hay mã của dự án khác.

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

Tạo trong `dist/` (cần mạng cho `uvx`/`npx`, và `git` sạch; chỉ gồm nội dung đã commit):

- `xt-<version>-py3-none-any.whl`
- `xt-<version>.tar.gz`: `git archive` với tiền tố `xt-<version>/`. **Loại** `research/de_thi_OLP2026.pdf` và `.txt` (đề thi thuộc VFOSSA, xem `docs/decisions.md`).
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

Báo lỗi cài đặt bằng GitHub Issues (mẫu **Báo lỗi**), kèm phiên bản và output lệnh.
