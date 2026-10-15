# Đo footprint thật của profile `minimal` (xT)

Đo ngày 2026-10-15, máy 15.6 GB RAM, Docker Engine 29.1.3 (Linux, không Docker Desktop), compose dựng tại `/tmp/xt-footprint` (đã xóa). RAM lấy từ `docker stats --no-stream` (cgroup, gồm page cache của container). Máy đang chạy các container khác nên chỉ tính container `xtfp-*`.

## Kết luận

- **Đạt mục tiêu < 4 GB với biên rất lớn.** Mặc định (Keycloak `start-dev`, không chỉnh heap): ~1.15 GB idle, ~1.4 GB sau tải. Đã chỉnh Keycloak (`start --optimized`, Xmx 256m): **~0.75 GB** cả 7 container.
- Ước tính "Keycloak ~1 GB" của RT Q8 là đúng cho cấu hình không giới hạn heap (JVM tự lấy 25-70% RAM host); chỉnh heap kéo xuống ~450-520 MB.
- Số trên dùng **placeholder** cho xt-mcp/xt-worker (python:3.12-slim rỗng, 4-15 MB). Ứng dụng thật (Starlette + SpiffWorkflow + WeasyPrint + boto3) ước tính **150-300 MB mỗi tiến trình [ước tính, chưa đo]**; cộng vào vẫn < 2 GB.
- Chưa đo: xt-migrate, xt-bot, import realm thật với client/DCR, dữ liệu Postgres lớn, tải đồng thời cao.

## RAM từng dịch vụ (MiB)

| Dịch vụ | Idle (cấu hình mặc định, start-dev) | Sau login + request + upload | Cấu hình đã chỉnh (optimized, Xmx256m) |
|---|---|---|---|
| Keycloak 26.6.3 | 746 | 809 | 464 idle / 519 sau tải |
| APISIX 3.19.0 (standalone) | 228 | 232 | 88 |
| SeaweedFS (`weed server -s3`) | 86 | 233 (sau PUT 20 MB) | 67 |
| PostgreSQL 17-alpine | 50 | 52 | 45 |
| PostgREST (latest) | 25 | 50 | 17 |
| python placeholder xt-mcp | 15 | 15 | 14 |
| python placeholder xt-worker | 4 | 4 | 4 |
| **Tổng** | **~1 154** | **~1 395** | **~753** |

Tải: 3 lần login admin-cli, 40 vòng (admin API + OIDC discovery + request qua APISIX), tạo realm `xt` + 20 user, PUT bucket + object 20 MB qua S3. APISIX idle 228 MB ở lần đo đầu là ngay sau khởi động; ở lần đo sau ổn định ~88 MB.

## Keycloak: heap tối thiểu

Mỗi cấu hình: recreate container, chờ `/realms/master` trả 200, đợi 15 s, rồi 150 vòng (token + list user qua admin API, mỗi 10 vòng tạo user). Mọi cấu hình dưới đây 0/150 lỗi, không OOM, không restart. JVM thêm `-XX:MaxMetaspaceSize=192m`.

| Chế độ | Heap | Ready (s) | RAM idle (MiB) | RAM sau tải (MiB) |
|---|---|---|---|---|
| start-dev | MaxRAMPercentage=70 (~10 GB trần) | 31 | 552 | 627 |
| start-dev | Xmx512m | 30 | 501 | 542 |
| start-dev | Xmx384m | 25 | 463 | 509 |
| start-dev | Xmx256m | 35 | 424 | 483 |
| start-dev | Xmx192m | 54 | 436 | 496 |
| start-dev | Xmx128m | **OOM khi khởi động** | - | - |
| start --optimized | MaxRAMPercentage=70 | 17 | 636 | 685 |
| start --optimized | Xmx512m | 10 | 514 | 567 |
| start --optimized | Xmx384m | 12 | 437 | 480 |
| start --optimized | Xmx256m | 10-15 | 453-464 | 497-519 |
| start --optimized | Xmx192m | 9 | 429 | 471 |
| start --optimized | Xmx128m | 11 | 427 | 487 |
| start --optimized | Xmx96m | 15 | 444 | 469 |
| start --optimized | Xmx64m | 11 | 403 | 442 |

- **Tối thiểu để khởi động: dev ≥ 192m; optimized chạy được tới 64m** với tải nhẹ ở trên. Đây là ngưỡng sống sót, không phải ngưỡng khuyến nghị: tải thật (nhiều session, import realm, Organizations) chưa thử.
- **Khuyến nghị: `start --optimized` + `JAVA_OPTS_KC_HEAP="-Xms128m -Xmx384m -XX:MaxMetaspaceSize=192m"`** (hoặc 256m). RSS dừng ~450-520 MB vì non-heap (metaspace, code cache, Quarkus, thread) chiếm ~300 MB, giảm heap dưới 256m không tiết kiệm đáng kể.
- `--optimized` cần bước `kc.sh build` (Dockerfile 3 dòng, ảnh thêm 175 MB: 649 MB so với 474 MB). Lợi ích: khởi động 10-17 s thay vì 25-55 s và RAM thấp hơn khi có heap cố định. `start-dev` không dùng cho bản `public`.
- Healthcheck Keycloak 26 nằm ở **cổng quản trị 9000** (`/health/ready`), không phải 8080. Compose cần `KC_HEALTH_ENABLED=true` và kiểm tra cổng 9000 (ảnh Keycloak không có curl, dùng `bash` `/dev/tcp` hoặc dò `/realms/master` từ ngoài).
- Phiên bản đo là 26.6.3 (có sẵn trên máy), kiến trúc ghi 26.8.1; chênh lệch dự kiến không đáng kể nhưng **chưa đo 26.8.1**.

## APISIX standalone (không etcd)

**Chạy được.** `config.yaml` với `deployment.role: data_plane`, `role_data_plane.config_provider: yaml`; route khai báo trong `conf/apisix.yaml` (kết thúc bằng dòng `#END`). Đã kiểm chứng: `GET :19090/rest/` qua `proxy-rewrite` tới PostgREST trả 200 (OpenAPI), route tới Keycloak cấu hình cùng file. Không có etcd, không có Admin API; APISIX tự reload khi file đổi (khớp thiết kế GitOps `xt gitops sync` ghi file YAML). Cảnh báo `load_stream` trong log là vô hại. Lưu ý: không có Admin API nên mọi thay đổi route phải qua file.

## Thời gian khởi động nguội (ảnh đã có sẵn, `compose up -d`)

| Dịch vụ | Thời gian tới sẵn sàng |
|---|---|
| PostgreSQL | ~12 s (gồm initdb lần đầu) |
| PostgREST | ~12 s (log "schema cache loaded" sau khi Postgres healthy) |
| APISIX | ~12 s (trả HTTP trên cổng 9080) |
| SeaweedFS (S3 :8333) | ~12 s |
| Keycloak start-dev (lần đầu, migrate DB) | ~34 s (log "started in 33.5s") |
| Keycloak start-dev (lần sau) | 25-54 s |
| Keycloak --optimized | 9-17 s |

Tổng toàn stack sẵn sàng: ~45 s lần đầu (Keycloak là điểm nghẽn). Thời gian pull không đo (phụ thuộc mạng).

## Dung lượng đĩa

| Ảnh | Kích thước |
|---|---|
| postgres:17-alpine | 297 MB |
| keycloak 26.6.3 (chuẩn) | 474 MB |
| keycloak tùy biến `--optimized` (thay cho ảnh chuẩn) | 649 MB |
| apache/apisix:3.19.0-debian | 440 MB |
| chrislusf/seaweedfs (latest) | 529 MB |
| postgrest/postgrest | 20.5 MB |
| python:3.12-slim | 119 MB |
| **Tổng ảnh (dùng bản optimized)** | **~2.06 GB** |

Chưa gồm lớp pip của xt-api/worker (ước tính +200-400 MB) và dữ liệu (dữ liệu đo: vài chục MB). Máy đo chỉ còn ~9 GB trống nên không pull thêm ảnh lớn.

## Công cụ mở

- Mọi ảnh đều pull/chạy bằng **Docker Engine + Compose plugin** (apt, Linux), không cần Docker Desktop. Đã dựng thật bằng `docker build` + `docker compose`.
- `podman`: **không cài trên máy này nên chưa kiểm chứng** `podman compose`. Tuyên bố hỗ trợ Podman trong kiến trúc vẫn là chưa kiểm chứng; cần test trong CI. Lưu ý lệnh `docker compose` dùng `depends_on: condition: service_healthy` và `--no-deps`/`--force-recreate`, `podman-compose` có thể khác.
- Ảnh đều là OSS (Postgres, Keycloak, APISIX, SeaweedFS, PostgREST: Apache-2.0/MIT/PostgreSQL). Registry dùng là Docker Hub và quay.io (dịch vụ, không phải công cụ).

## Profile `minimal` khuyến nghị

- Postgres 17-alpine (`shared_buffers=128MB`, `max_connections=50`), Keycloak `start --optimized` với heap `-Xms128m -Xmx384m -XX:MaxMetaspaceSize=192m`, APISIX standalone, PostgREST, SeaweedFS (`weed server -s3 -master.volumeSizeLimitMB=64 -volume.max=8`), xt-api, xt-worker, xt-bot.
- Đặt `mem_limit` để chặn thất thoát: Keycloak 768m, APISIX 256m, SeaweedFS 512m, Postgres 512m, PostgREST 128m, xt-api/worker 512m mỗi cái. Tổng trần ~3.2 GB, nằm dưới 4 GB.
- Dự kiến thực tế: 0.75 GB (đo) + ~0.5 GB xt-* thật (ước tính) = **~1.3 GB**.

## Nếu vượt 4 GB, cắt theo thứ tự

1. Hạ heap Keycloak xuống 256m và giảm `max_connections`/`shared_buffers` Postgres (tiết kiệm ~100 MB).
2. Gộp xt-worker vào xt-api (một tiến trình, tiết kiệm 150-300 MB).
3. Tắt xt-bot (đã quy định tắt khi thiếu token).
4. Thay SeaweedFS bằng thư mục volume cục bộ ở chế độ chỉ-hash (mất presigned upload; chỉ dùng cuối cùng).
5. Bỏ APISIX, để xt-api tự xác thực JWT (mất cổng chung; chỉ khi bắt buộc, vì đề yêu cầu API Gateway).
