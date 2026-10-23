# demo-ops: công tắc OTP + mật khẩu/mã TOTP tài khoản demo (chỉ môi trường demo)

Dịch vụ nhỏ `deploy/demo-ops/` (Python stdlib, một tệp server + một trang HTML) cho phép, trên trang web
`https://<XT_DOMAIN_DESK>/demo/`:

- bật/tắt OTP bằng một công tắc;
- xem từng tài khoản demo: mật khẩu (có nút chép) và mã TOTP hiện tại (tự đổi mỗi 30 s, có nút chép).

Không thuộc profile `minimal`/`full`/`public`: chỉ chạy khi bật profile `demo`. Production không đổi hành vi.

## Cách hoạt động

| Thành phần | Việc |
|---|---|
| `demo-ops` | Gọi Keycloak Admin REST (token realm `master`, URL nội bộ `http://keycloak:8080`) |
| Keycloak (nguồn sự thật) | flow `Browser - Conditional 2FA` của browser flow: `CONDITIONAL` (bật) / `DISABLED` (tắt); client `xt-desk-confirm`: `minimum.acr.value` = `urn:xt:acr:otp` / `urn:xt:acr:pwd` |
| Tệp chế độ `{"otp": true\|false}` | demo-ops ghi (nguyên tử) vào volume `demo-ops-state`; xt-api mount read-only tại `/run/demo-ops/mode.json` |
| xt-api | Nếu đặt `XT_CONFIRM_MODE_FILE`, mỗi request đọc tệp (cache theo mtime): `true` -> `(urn:xt:acr:otp, {otp})`, `false` -> `(urn:xt:acr:pwd, {pwd})`. Tệp thiếu/hỏng -> dùng `XT_CONFIRM_ACR`/`XT_CONFIRM_REQUIRED_AMR` như cũ. Không đặt biến -> hành vi cũ hoàn toàn |

`GET /demo/api/state` đọc giá trị thật từ Keycloak và báo `mismatch` nếu flow và `minimum.acr.value` lệch nhau,
hoặc tệp chế độ lệch Keycloak. Lúc khởi động demo-ops tự đưa tệp chế độ khớp Keycloak (khi Keycloak nhất quán).
Trang tự thấy lệch và hiện cảnh báo; bấm công tắc để đồng bộ lại.

Bảo vệ: HTTP Basic (mật khẩu đọc từ tệp, so sánh hằng thời gian, tên đăng nhập tùy ý), `POST` yêu cầu
`Origin`/`Referer` cùng host, mọi phản hồi `Cache-Control: no-store`, CSP nonce. Trang hiển thị mật khẩu tài
khoản demo nên đặt mật khẩu Basic dài, ngẫu nhiên.

## Bật

Chạy trong thư mục deploy của máy (`/home/chinhan/.cache/xt-deploy/deploy`, project `xt-e2e-20261016`).

1. Bí mật (thư mục cha `~/.config/xt` đã 0700; tệp lá 0644 để uid 10001 trong container đọc được, giống `app/`):

   ```bash
   D=~/.config/xt/demo-ops
   mkdir -p "$D" && chmod 755 "$D"
   openssl rand -base64 24 | tr -d '\n' > "$D/password"                       # mật khẩu Basic của trang /demo/
   grep '^KC_BOOTSTRAP_ADMIN_USERNAME=' .env | cut -d= -f2- | tr -d '\n' > "$D/kc-admin-user"
   grep '^KC_BOOTSTRAP_ADMIN_PASSWORD=' .env | cut -d= -f2- | tr -d '\n' > "$D/kc-admin-password"
   install -m 0644 ~/.cache/xt-live-mcp/secrets/e2e/demo-seed.json "$D/demo-seed.json"   # chép lại khi seed lại
   chmod 644 "$D"/password "$D"/kc-admin-user "$D"/kc-admin-password
   cat "$D/password"; echo      # mật khẩu để đăng nhập trang
   ```

   Nếu seed để chỗ khác: đặt `DEMO_SEED_FILE=/đường/dẫn/demo-seed.json` (0644) trong `deploy/.env`.
   Thư mục khác `~/.config/xt/demo-ops`: đặt `DEMO_OPS_SECRETS_DIR`.

2. `deploy/.env`: thêm một dòng cho xt-api (đây là cách duy nhất bật tính năng phía xt-api):

   ```
   XT_CONFIRM_MODE_FILE=/run/demo-ops/mode.json
   ```

   `XT_CONFIRM_ACR`/`XT_CONFIRM_REQUIRED_AMR` giữ nguyên làm giá trị dự phòng khi tệp chưa có.

3. Route APISIX. `deploy/apisix/extra-routes.json` (không commit; mẫu trong `extra-routes.example.json`, route
   `demo-ops`): thêm vào `routes` (đổi host thành `XT_DOMAIN_DESK`, ở đây `xt.freepro.online`):

   ```json
   {
     "id": "demo-ops",
     "hosts": ["xt.freepro.online"],
     "uris": ["/demo", "/demo/*"],
     "priority": 20,
     "plugins": {
       "real-ip": {"source": "http_cf_connecting_ip", "trusted_addresses": ["127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]},
       "client-control": {"max_body_size": 4096},
       "limit-req": {"rate": 5, "burst": 10, "key_type": "var", "key": "remote_addr", "rejected_code": 429}
     },
     "upstream": {"type": "roundrobin", "nodes": {"demo-ops:8090": 1}}
   }
   ```

   `render_apisix.py` thường từ chối route dùng hostname `XT_DOMAIN_*`; ngoại lệ duy nhất là route trên host
   Desk có `uris` chỉ gồm `/demo` và `/demo/*`. Sau đó `python3 scripts/render_apisix.py` (ghi tại chỗ, giữ inode);
   nếu APISIX chưa nạp: `docker compose -p xt-e2e-20261016 --env-file .env restart apisix`.

4. Dựng dịch vụ (thêm `--profile demo` vào cách chạy `up` quen thuộc; xt-api được tạo lại vì có biến + volume mới):

   ```bash
   docker compose -p xt-e2e-20261016 --env-file .env --profile minimal --profile demo up -d --build xt-api demo-ops
   ```

5. Kiểm: mở `https://xt.freepro.online/demo/` (tên đăng nhập tùy ý, mật khẩu ở bước 1). Công tắc phải khớp trạng
   thái hiện tại (hiện đang TẮT), không có cảnh báo "Không khớp".

## Bật lại OTP

Bấm công tắc trên trang (Keycloak `CONDITIONAL` + `urn:xt:acr:otp` + tệp chế độ `{"otp": true}`; có hiệu lực ngay,
không cần khởi động lại). Muốn cố định ở mức production (không phụ thuộc tệp): bỏ `XT_CONFIRM_MODE_FILE`, đặt lại
`XT_CONFIRM_ACR=urn:xt:acr:otp`, `XT_CONFIRM_REQUIRED_AMR=otp` (hoặc xóa hai dòng để lấy mặc định) rồi tạo lại xt-api.

## Rollback

1. Bật lại OTP bằng công tắc (hoặc tay: flow `Browser - Conditional 2FA` -> `CONDITIONAL`, client
   `xt-desk-confirm` `minimum.acr.value` -> `urn:xt:acr:otp`).
2. Xóa dòng `XT_CONFIRM_MODE_FILE` khỏi `deploy/.env`; gỡ route `demo-ops` khỏi `extra-routes.json`, render lại.
3. `docker compose -p xt-e2e-20261016 --env-file .env --profile minimal up -d xt-api` rồi
   `docker compose -p xt-e2e-20261016 --env-file .env --profile demo rm -sf demo-ops`.
4. Tùy chọn: `docker volume rm xt-e2e-20261016_demo-ops-state`; xóa `~/.config/xt/demo-ops`.

`make provision-realm` / import lại realm có thể đặt lại flow và `minimum.acr.value` (provision dùng
`XT_CONFIRM_ACR`); sau đó xem trạng thái trên trang và bấm công tắc nếu cần.

## Liên kết "Demo"

Chưa có liên kết trong Desk (không sửa `desk/`); dùng trực tiếp `https://<XT_DOMAIN_DESK>/demo/`.

## Kiểm thử

`uv run pytest tests/unit/demo_ops tests/unit/identity/test_confirm_mode.py` (TOTP đối chiếu thuật toán của
`xt-demo-login.sh`, Basic auth, kiểm origin, Keycloak giả, tệp chế độ);
`python3 -I -m unittest discover -s deploy/tests/offline -t deploy/tests/offline` (route APISIX).
