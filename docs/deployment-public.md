<!-- SPDX-License-Identifier: Apache-2.0 -->
# Triển khai công khai qua Cloudflare Tunnel (profile `public`)

Instance thử nghiệm và demo của xT được công khai qua Cloudflare Tunnel từ máy phát triển. Profile này **không nằm trên đường build/cài đặt** (PoF I.4). Khi tự host ở môi trường sản xuất, dùng reverse proxy TLS tự quản thay cho tunnel (xem `architecture-v2.md` D10).

## Hostname

| Hostname | Vai trò | Đích nội bộ |
|---|---|---|
| `xt.freepro.online` | xT Desk: chat + `/desk/confirm/{id}` | APISIX `127.0.0.1:9080` |
| `xt-mcp.freepro.online` | MCP endpoint `/mcp`, URL dán vào connector ChatGPT/Claude | APISIX `127.0.0.1:9080` |
| `xt-api.freepro.online` | Dịch vụ headless Tầng 1: `/data`, `/workflow`, `/files` | APISIX `127.0.0.1:9080` |
| `xt-auth.freepro.online` | Keycloak (SSO, OAuth 2.1, `KC_HOSTNAME`) | APISIX `127.0.0.1:9080` |

Mọi hostname đều đi vào **một cổng gateway duy nhất**. APISIX định tuyến theo header `Host`. Hostname nào không khai báo thì tunnel trả `404`.

Tất cả hostname đều chỉ có một cấp (`xt-*.freepro.online`), vì Universal SSL miễn phí của Cloudflare chỉ phủ `*.freepro.online`. Dạng hai cấp như `mcp.xt.freepro.online` cần Advanced Certificate (trả phí).

## Thành phần đã tạo (15/10/2026)

- **Tunnel:** `xt`, ID `26e169a9-3754-476d-bf7b-a4367d0fc00a`, cấu hình quản lý từ xa (`config_src: cloudflare`).
- **DNS:** 4 bản ghi CNAME `xt`, `xt-mcp`, `xt-api`, `xt-auth` trỏ tới `26e169a9-3754-476d-bf7b-a4367d0fc00a.cfargotunnel.com`, có proxied.
- **Kiểm chứng:** connector `healthy` với 4 kết nối QUIC (`sin06`, `sin13`, `sin22`, `sin08`). Gọi `https://<hostname>/` qua Internet, cả 4 hostname trả `200` và nhận đúng `Host`.

## Bí mật (không bao giờ commit)

| Tệp (quyền 600, ngoài repo) | Nội dung |
|---|---|
| `~/.config/xt/cf_token` | Cloudflare API token, cần quyền Account → Cloudflare Tunnel: Edit và Zone → DNS: Edit |
| `~/.config/xt/tunnel_token` | Token chạy connector |
| `~/.config/xt/tunnel_id` | ID tunnel |

## Chạy connector

```bash
cloudflared tunnel --no-autoupdate run --token "$(cat ~/.config/xt/tunnel_token)"
```

Profile `public` trong docker compose sẽ chạy connector này dưới dạng service và đọc token từ biến môi trường/secret.

## Lưu ý chủ quyền dữ liệu

Khi bật tunnel, TLS kết thúc tại Cloudflare. Chỉ dùng cho thử nghiệm và demo. Khi demo rút mạng, dùng `/etc/hosts` trỏ cùng hostname về `127.0.0.1` để `iss` của Keycloak không đổi (`architecture-v2.md`, kiểm ở v0.3).
