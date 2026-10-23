<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->
# Tài khoản demo `binhminh`: một tài khoản mỗi cấp

Quyết định: [D-DEMO-LEVELS-1](decisions.md). Tenant `binhminh` chỉ còn 7 tài khoản đăng nhập. Danh bạ (60 người trong `seed/binh-minh/org.json`) giữ nguyên; người không có tài khoản vẫn nằm trong sơ đồ tổ chức.

Tài liệu này không chứa mật khẩu hay mã TOTP. Bí mật nằm ở `~/.config/xt/` của người vận hành.

## 1. Bảy tài khoản

Username Keycloak là phần trước `@` của email trong `org.json`.

| Cấp | Key | Người | Username | Vai trò xT | Dùng để demo |
|---|---|---|---|---|---|
| Giám đốc | NV-0001 | Nguyễn Hữu Trí | `tri.nguyenhuu` | `director` | Duyệt cuối đề xuất > 5 triệu. Là người duyệt thay thế ở bước IT của tiếp nhận nhân sự và ở bước duyệt JD agent. Nhận bước `ktt` khi kế toán đã làm `kttt`. Duyệt giảm giá > 20%, bảng công > 60 giờ, bảo trì > 20 triệu. Đọc audit |
| Kế toán | NV-0004 | Phạm Kim Oanh | `oanh.phamkim` | `dept_head` (KT), `chief_accountant`, **`payment_accountant`** (thêm), `facility_manager` (đã cấp) | Kiểm chứng từ (`kttt`), kế toán trưởng (`ktt`, `chief_acct_overdue`), bước Kế toán của tiếp nhận/nghỉ việc, duyệt phiếu bảo trì |
| Trưởng phòng | NV-0010 | Hoàng Quốc Bảo | `bao.hoangquoc` | `dept_head` (KD), `sales_manager`, `project_manager` (đã cấp) | Duyệt bước 1 đề xuất và phiếu xin duyệt của Dũng/Trâm, duyệt giảm giá, duyệt bảng công, quản lý trực tiếp ở tiếp nhận, nghiệm thu giao việc |
| Nhân sự | NV-0026 | Nguyễn Yến Nhi | `nhi.nguyenyen` | `hr` | Mở hồ sơ tiếp nhận hoặc nghỉ việc, làm bước HR, đóng hồ sơ |
| IT | NV-0031 | Đặng Minh Khang | `khang.dangminh` | `it_admin` | Tạo `access_grant` (chọn vai trò). Sau đó giám đốc duyệt bước IT, Khang không tự duyệt. Thu hồi quyền khi nghỉ việc. Lập JD agent rồi giám đốc duyệt |
| Quản lý kho | NV-0014 | Dương Bảo Trâm | `tram.duongbao` | `warehouse_manager`, `sales` (đã cấp) | Danh mục kho, duyệt phiếu nhập/xuất do Dũng lập |
| Nhân viên | NV-0013 | Phan Tiến Dũng | `dung.phantien` | `employee`, `sales`, `technician` (đã cấp) | Lập đề xuất tạm ứng, phiếu xin duyệt, phiếu kho (cả nhập kho), đề nghị giảm giá, bảng công, phiếu bảo trì |

"Đã cấp" nghĩa là vai trò đi qua `access_grant` ở lần tiếp nhận ngày 18/10, lưu trong `xt_meta.person_role_grant` với `approved_by` = NV-0032. Tắt NV-0032 không thu hồi các vai trò này.

Tài khoản bị tắt (`enabled=false`, không xóa):
- `binhminh`: `quyen.dothuc` (NV-0005, `payment_accountant`), `vinh.buiquang` (NV-0032, `it_admin` thứ hai), `tung.tranba` (NV-0020).
- `hoamai`: `phuc.maixuan`, `tung.nguyenthanh`, `linh.vuphuong`.
- `e2e.hai-to-chuc`.

Trong xT, NV-0005, NV-0032 và NV-0020 cũng được chuyển `active=false`. Người nghỉ không đăng nhập được, không bao giờ được giao bước mới, và bước họ đang giữ được giao lại.

## 2. Ràng buộc khi diễn demo

- **Tiếp nhận nhân sự (v8):** Khang tạo `access_grant`, giám đốc hoàn thành bước IT. Nếu Khang tự hoàn thành, hệ thống từ chối với lý do `self_approval`. Hồ sơ tiếp nhận đã mở trước khi nâng lên v8 vẫn chạy định nghĩa v7, không có người duyệt thay thế. Cần mở hồ sơ mới.
- **JD agent (v2):** người phụ trách agent (`nguoi_phu_trach`) không được là giám đốc. Nếu là giám đốc thì không ai duyệt được, vì người duyệt phải khác người lập JD và khác người phụ trách.
- **Đề xuất:**
  - Thanh toán > 50 triệu đi theo chuỗi Bảo → Oanh (`kttt`) → giám đốc (`ktt`, do Oanh đã làm `kttt`) → giám đốc (bước `director`).
  - Tạm ứng (Desk chỉ gắn `advance_request`) đi theo chuỗi Bảo → Oanh (các bước kế toán trưởng) → giám đốc.
  - Người đề nghị không bao giờ tự duyệt.
- **Kho:** người lập phiếu không được duyệt phiếu của mình. Trâm là quản lý kho duy nhất, nên phiếu do Trâm lập sẽ không có người duyệt. Phiếu, kể cả phiếu nhập kho, do Dũng lập.
- **Người đề nghị nên ở phòng KD (Dũng, Trâm):** đề xuất và phiếu xin duyệt do Nhi hoặc Khang lập sẽ về trưởng phòng NS (NV-0025) hoặc IT (NV-0030) trong danh bạ. Hai người này không có đăng nhập, nên phiếu chỉ tới giám đốc khi SLA 24 giờ leo thang.

## 3. Runbook trên stack demo (`xt-e2e-20261016`)

Mọi lệnh dưới đây chạy trên máy chủ demo theo đúng thứ tự. Không chạy lệnh nào trước khi PR đã được gộp vào `staging`. Trước bước build, kiểm `free -g` (available ≥ 4 GB) và `df -h /`.

```bash
XTC=/home/chinhan/.cache/xt-ops/xtc.sh   # chạy `xt` trong container xt-api (mount /packages)
WHO="--proposed-by person:NV-0031 --approved-by person:NV-0001"
```

### 3.1. Cập nhật mã, build và migrate

Bản mới thêm trường `alternates` vào spec quy trình và thêm migration 0023. Gói mới chỉ cài được sau khi ảnh `xt` mới đã chạy và DB đã migrate. Ảnh cũ từ chối trường lạ (`extra=forbid`).

```bash
cd /home/chinhan/.cache/xt-deploy
git status --short                                           # phải rỗng; có thay đổi thì dừng, lưu lại trước
git fetch origin && git checkout --detach origin/staging      # /packages của xtc.sh đọc thư mục này
cd deploy
docker tag xt/xt:dev xt/xt:pre-demo-levels                  # để hoàn tác
COMPOSE_PROJECT_NAME=xt-e2e-20261016 docker compose --env-file .env build xt-api
$XTC -- db migrate          # xtc.sh dùng `compose run` nên đã chạy ảnh mới; mong đợi "đã áp: 0023_person_change"
COMPOSE_PROJECT_NAME=xt-e2e-20261016 docker compose --env-file .env up -d --no-deps --no-build xt-api xt-worker
```

Migrate trước rồi mới khởi động lại xt-api/xt-worker, để dịch vụ mới không chạy trên DB chưa có 0023.

### 3.2. Nâng gói

Mỗi gói làm hai bước: xem kế hoạch, chép mã `sha256:…`, rồi chạy lại với `--confirm`.

```bash
$XTC -- pack install --tenant binhminh /packages/nhan-su-vong-doi $WHO
#   kiểm: 0.4.2 -> 0.4.3, "employee_onboarding v8", 0 RỦI RO / 0 PHÁ HỦY / 0 BỊ CHẶN
$XTC -- pack install --tenant binhminh /packages/nhan-su-vong-doi $WHO --confirm sha256:<mã>

$XTC -- pack install --tenant binhminh /packages/nhan-su-ai $WHO
#   kiểm: 0.1.0 -> 0.1.1 (hoặc cài mới), "duyet_jd_agent v2"
$XTC -- pack install --tenant binhminh /packages/nhan-su-ai $WHO --confirm sha256:<mã>
```

### 3.3. Vai trò và người nghỉ trong xT (`xt person set`)

Phải thêm vai trò cho Oanh trước, rồi mới tắt Quyên. Làm ngược lại thì các bước `payment_accountant` đang mở sẽ chỉ còn người không có đăng nhập.

```bash
# Oanh giữ thêm payment_accountant
$XTC -- person set --tenant binhminh --key NV-0004 --add-roles payment_accountant $WHO
#   kiểm "Trước"/"Sau", rồi:
$XTC -- person set --tenant binhminh --key NV-0004 --add-roles payment_accountant $WHO --confirm sha256:<mã>

# Tắt Quyên, Vinh, Tùng (giữ bản ghi, không xóa)
for k in NV-0005 NV-0032 NV-0020; do
  $XTC -- person set --tenant binhminh --key $k --inactive $WHO
done
#   chép từng mã rồi:
$XTC -- person set --tenant binhminh --key NV-0005 --inactive $WHO --confirm sha256:<mã NV-0005>
$XTC -- person set --tenant binhminh --key NV-0032 --inactive $WHO --confirm sha256:<mã NV-0032>
$XTC -- person set --tenant binhminh --key NV-0020 --inactive $WHO --confirm sha256:<mã NV-0020>
```

Mỗi lần áp ghi một dòng `xt_meta.person_change` vào hash-chain audit của tenant. Lệnh từ chối key lạ, kế hoạch rỗng, mã sai và trường hợp người đề xuất trùng người duyệt.

Không bắt buộc: tắt luôn 3 người của `hoamai` trong xT bằng `--tenant hoamai --key NV-0001|NV-0004|NV-0007 --inactive`. Tắt Keycloak (mục 3.4) đã đủ chặn đăng nhập.

Kiểm tra (chỉ đọc):

```bash
docker exec xt-e2e-20261016-postgres-1 psql -U xt -d xt -c "SELECT p.key, p.active, p.roles FROM xt_meta.person p JOIN xt_meta.tenant_profile t ON t.tenant_id = p.tenant_id WHERE t.alias = 'binhminh' AND p.key IN ('NV-0004','NV-0005','NV-0032','NV-0020')"
docker exec xt-e2e-20261016-postgres-1 psql -U xt -d xt -c "SELECT key, before, after, approved_by, at FROM xt_meta.person_change ORDER BY at"
```

Thay `-U xt -d xt` nếu `.env` đặt `POSTGRES_USER`/`POSTGRES_DB` khác.

**Cảnh báo:** `xt org import` không có `--if-empty`, hoặc chạy lại demo-seed ở chế độ force, sẽ nạp lại `roles`/`active` từ `org.json` và ghi đè các thay đổi trên. Khi đó phải chạy lại mục 3.3.

Không chạy lại `python -m xte2e.demo_roles e2e/demo-roles.binhminh.json --apply` với kế hoạch cũ. Kế hoạch đó dùng NV-0032 làm người duyệt IT và NV-0005 làm kế toán. Bước 1 của script tạo lại user Keycloak cho người vận hành chưa có đăng nhập, rồi chạy `xt org import --links`, nên đi ngược roster này. Nếu cần cấp thêm vai trò gói, mở hồ sơ tiếp nhận trên Desk: Nhi mở hồ sơ, Khang tạo `access_grant`, giám đốc hoàn thành bước IT, Oanh làm bước kế toán.

### 3.4. Keycloak: tắt và bật lại tài khoản

Đăng nhập kcadm một lần. Mật khẩu lấy từ env của chính container, không gõ ra màn hình:

```bash
KC=xt-e2e-20261016-keycloak-1
REALM=$(grep '^XT_REALM=' /home/chinhan/.cache/xt-deploy/deploy/.env | cut -d= -f2); REALM=${REALM:-xt}
docker exec $KC sh -c '/opt/keycloak/bin/kcadm.sh config credentials --config /tmp/kc.cfg \
  --server http://localhost:8080 --realm master \
  --user "$KC_BOOTSTRAP_ADMIN_USERNAME" --password "$KC_BOOTSTRAP_ADMIN_PASSWORD"'
kc() { docker exec $KC /opt/keycloak/bin/kcadm.sh "$@" --config /tmp/kc.cfg; }
uid() { kc get users -r "$REALM" -q username="$1" -q exact=true --fields id --format csv --noquotes; }
```

Xem trước. Mỗi username phải ra đúng một id; nếu rỗng thì dừng lại và tra bằng `kc get users -r "$REALM" -q search=<tên>`.

```bash
for u in quyen.dothuc vinh.buiquang tung.tranba phuc.maixuan tung.nguyenthanh linh.vuphuong e2e.hai-to-chuc; do
  echo "$u -> $(uid $u)"
done
```

Tắt tài khoản và đăng xuất mọi phiên đang mở:

```bash
for u in quyen.dothuc vinh.buiquang tung.tranba phuc.maixuan tung.nguyenthanh linh.vuphuong e2e.hai-to-chuc; do
  id=$(uid $u); [ -n "$id" ] || { echo "không thấy $u"; continue; }
  kc update users/$id -r "$REALM" -s enabled=false
  kc create users/$id/logout -r "$REALM" -s realm="$REALM" -s user="$id"
  kc get users/$id -r "$REALM" --fields username,enabled
done
```

Lệnh đăng xuất theo đúng ví dụ "logout a user" của tài liệu Keycloak Admin CLI (`create users/ID/logout` kèm `-s realm=… -s user=…`; không có `-s` thì `create` không có thân yêu cầu). Cú pháp `create`/`get` đã đối chiếu `kcadm.sh --help` của ảnh `xt/keycloak:26.6.3-optimized`; chưa chạy trên stack demo.

Bật lại một tài khoản, ví dụ Quyên. Làm cả hai phía, Keycloak và xT:

```bash
kc update users/$(uid quyen.dothuc) -r "$REALM" -s enabled=true
$XTC -- person set --tenant binhminh --key NV-0005 --active $WHO
$XTC -- person set --tenant binhminh --key NV-0005 --active $WHO --confirm sha256:<mã>
```

Không cần thêm realm role `payment_accountant` cho Oanh trong Keycloak. Quyền trong xT đọc từ `person.roles` và vai trò cấp qua access_grant, không đọc realm role của token. Oanh đã có TOTP vì là trưởng phòng.

Dọn sau khi xong: `docker exec $KC rm -f /tmp/kc.cfg`.

### 3.5. Kiểm nhanh sau runbook

1. Đăng nhập Desk lần lượt 7 tài khoản ở mục 1, mỗi tài khoản phải vào được. Các tài khoản đã tắt phải bị Keycloak từ chối.
2. Chạy luồng tiếp nhận:
   - Nhi mở hồ sơ tiếp nhận mới và làm bước HR.
   - Khang tạo `access_grant`.
   - Hộp việc của giám đốc có bước "IT: email, tài khoản, thiết bị". Giám đốc xác nhận (mật khẩu + TOTP).
   - Vai trò được áp cho người mới.
3. Chạy luồng đề xuất: Dũng gửi tạm ứng 8 triệu, Bảo duyệt rồi giám đốc duyệt. Đề nghị chuyển trạng thái `approved`.

### 3.6. Hoàn tác

- Bật lại tài khoản theo mục 3.4. Đặt lại vai trò bằng `person set --remove-roles payment_accountant` cho NV-0004 và `--active` cho người đã tắt.
- Ảnh cũ: `docker tag xt/xt:pre-demo-levels xt/xt:dev`, sau đó `up -d --no-deps --no-build xt-api xt-worker`.
- Migration 0023 chỉ thêm bảng, nên ảnh cũ vẫn chạy được trên DB đã migrate. Gói đã cài giữ ở phiên bản mới. Instance v8/v2 cần ảnh mới để nạp lại định nghĩa.
