<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->

# Kit kiểm thử ChatGPT live và hậu kiểm chỉ đọc

Kit gồm [prompts](prompts.md), `plan.example.json`, verifier `scripts.live.verify` và [mẫu bằng chứng](evidence-template.md). Việc tạo kit **không chạy browser, đăng nhập, gọi ChatGPT/MCP công khai, Docker hoặc deploy**; chưa có kết quả live mới. Chạy các mục browser/live dưới đây **sau**, trong lượt được phép, từ thư mục gốc repo. Không cài thêm công cụ hoặc đổi public stack để chạy kit.

## 1. Chuẩn bị lượt kiểm và kế hoạch riêng tư

Chọn tenant demo được phép ghi, run-id mới và actor/fixture theo [prompts.md](prompts.md). Bình Minh là `binhminh`; NV-0013 (requester), NV-0010 (trưởng bộ phận), NV-0001 (giám đốc) là baseline 17/10, **không chứng minh** người nào có `warehouse_manager` hôm nay. Chọn run-id dài ít nhất 12 ký tự, ví dụ ngày UTC kèm hậu tố ngẫu nhiên; mọi mã/key mới bắt đầu bằng `RUN_ID-`. Xác minh tenant, `me`/`acting_for`, roles, app và schema bằng `inspect_workspace`/`describe_model` trước ghi; quản lý kho phải khác người tạo.

Lưu cookie, profile, log, plan, transcript và bằng chứng ngoài repo, thư mục 0700/tệp 0600. Không đọc `.env`, không đưa bí mật vào shell history/argv/chat. Tạo bản sao riêng tư của plan; nhập đường dẫn thật ở prompt shell, không sửa bản mẫu thành dữ liệu live trong repo:

```bash
umask 077
read -r -p 'Đường dẫn plan riêng tư ngoài repo: ' XT_LIVE_PLAN
export XT_LIVE_PLAN
cp scripts/live/plan.example.json "$XT_LIVE_PLAN"
chmod 600 "$XT_LIVE_PLAN"
```

Điền toàn bộ marker/số liệu tổng hợp bằng binding thật, không tái dùng UUID từ fixture offline. `records` cần `label`, `model`, `match`, `count`, `fields` (gồm revision mong đợi khi cần), số audit chính xác và `in_window`; mã phiếu theo prompt là `RUN_ID-OUT`, `-AI`, `-SELF`, `-BAD`. Bản ghi `BAD`/probe bị tắt phải có count=0. `self_request` trỏ label phiếu SELF do chính B (approver của phiếu OUT) tự tạo, cuối lượt còn `in_review`; AI vẫn `draft`.

`operations` chỉ liệt kê thao tác thành công cần biên nhận; key/actor/tool phải khớp lời gọi thật. `actor_key` là khóa service thực tế, không suy từ tên hay person UUID; quản trị được phép có thể đọc `xt_meta.tool_operation` theo tenant/tool/idempotency key để lấy binding, không in DSN/credential/receipt thô. `warehouse` dùng UUID người tạo/duyệt, subject người xác nhận thật, từng dòng có delta **âm** và baseline `before`/`level_before` lấy từ MCP trước ghi. `foreign` cần tenant/ref có thật và revision baseline do verifier quản trị xác minh; không dùng UUID ngẫu nhiên. `disabled_pack` là pack thật bị tắt; `pii` dùng trace mới, tool `get_record`/`find_people` (hoặc `search_records`) và tên trường thực tế như `person.email`.

Ghi `SINCE` trước lời ghi đầu tiên, `UNTIL` sau lời replay/ca âm cuối, timestamp ISO 8601 có múi giờ. Đọc tồn baseline qua MCP theo đúng item/location/kho; verifier đối chiếu audit UPDATE trước/sau của từng balance/level với baseline này; không tự lấy snapshot sau thao tác làm baseline. Không thay kỳ vọng để khớp kết quả sai. Tệp chưa đủ binding phải giữ trạng thái thiếu điều kiện.

## 2. Browser riêng :90 — chỉ thực hiện sau

Chỉ dùng helper `/home/chinhan/.cache/xt-shared-browser/start.py` và profile `chrome-profile` của nó. Helper có thể chọn :90–:129, nên phải kiểm :90 trống trước khi khởi động và xác nhận lại :90 sau. Nếu :90 đã dùng, chỉ tái sử dụng khi state/PID/profile cùng UID được kiểm; không kill, đổi cổng hoặc chiếm display của phiên khác. Không dùng :0/:1. Đoạn sau chỉ tạo browser khi chưa có phiên hợp lệ:

```bash
python3 - <<'PY'
import json, os, subprocess, sys
from pathlib import Path
b = Path('/home/chinhan/.cache/xt-shared-browser')
def owned(pid):
    p = Path('/proc') / str(pid)
    assert type(pid) is int and pid > 1 and p.stat().st_uid == os.getuid()
    return (p / 'cmdline').read_bytes().split(b'\0')
def verify(s):
    assert s['display'] == ':90'
    assert (b / 'chrome-profile').stat().st_uid == os.getuid()
    xcmd, vcmd = owned(s['xvfb_pid']), owned(s['vnc_pid'])
    assert b':90' in xcmd and any(x.endswith(b'/Xvfb') for x in xcmd)
    assert b':90' in vcmd and any(x.endswith(b'/x11vnc') for x in vcmd)
    assert any(b'websockify' in x for x in owned(s['websockify_pid']))
    chrome = owned(s['chrome_pid'])
    assert f'--user-data-dir={b}/chrome-profile'.encode() in chrome
    assert b'DISPLAY=:90' in (Path('/proc') / str(s['chrome_pid']) / 'environ').read_bytes().split(b'\0')
    for key in ('vnc_port', 'web_port', 'cdp_port'):
        assert type(s[key]) is int and 1024 <= s[key] <= 65535
    assert f"--remote-debugging-port={s['cdp_port']}".encode() in chrome
try:
    state = b / 'state.json'
    occupied = Path('/tmp/.X90-lock').exists() or Path('/tmp/.X11-unix/X90').exists()
    if occupied:
        assert state.stat().st_uid == os.getuid()
        s = json.loads(state.read_text()); verify(s)
    else:
        if state.exists():
            old = json.loads(state.read_text())
            assert not any(Path('/proc', str(old.get(k, -1))).exists() for k in ('xvfb_pid', 'vnc_pid', 'websockify_pid', 'chrome_pid'))
        subprocess.run([sys.executable, str(b / 'start.py')], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        s = json.loads(state.read_text()); verify(s)
    print(f"Đã kiểm :90; noVNC http://127.0.0.1:{s['web_port']}/vnc.html?host=127.0.0.1&port={s['web_port']}&autoconnect=1 ; VNC localhost:{s['vnc_port']}; CDP localhost:{s['cdp_port']}")
except Exception:
    raise SystemExit('Dừng: chưa xác minh được phiên riêng :90; không attach hoặc kill tiến trình.')
PY
```

Nếu hậu kiểm thất bại do race hoặc helper chọn display khác, dừng; chỉ xử lý tiến trình helper vừa tạo sau khi xác minh ownership, không dùng `pkill` chung. noVNC/VNC/CDP chỉ dùng loopback, lấy cổng từ state mới, không mở tunnel/public. `vnc.passwd` do helper tạo là tệp mật khẩu VNC nhị phân; giữ riêng như credential; không đọc `vnc-password.txt` cũ và không in password. Đặt lại tương tác để người vận hành dùng noVNC:

```bash
b=/home/chinhan/.cache/xt-shared-browser
LD_LIBRARY_PATH="$b/usr/lib/x86_64-linux-gnu" "$b/usr/bin/x11vnc" -storepasswd "$b/vnc.passwd"
chmod 600 "$b/vnc.passwd"
```

Chỉ một đối số tệp sau `-storepasswd` sẽ hỏi mật khẩu tương tác, không truyền bí mật trên argv. Nếu phiên VNC cần nạp lại password, dừng thao tác và xử lý đúng tiến trình đã xác minh của lượt này; không khởi động helper thứ hai để lách :90 đang bận.

## 3. Nạp cookie tùy chọn — chỉ thực hiện sau

Người vận hành cung cấp tệp export được phép dùng. **Không chạy** `load-cookies.cjs` cũ vì nó có đường dẫn cứng. Đoạn dưới chỉ đọc đường dẫn từ biến môi trường, chỉ nhận domain ChatGPT, gắn vào CDP của profile/:90 đã xác minh và không tự điều hướng:

```bash
read -r -s -p 'Đường dẫn tệp cookie riêng tư: ' XT_LIVE_COOKIE_FILE
printf '\n'
export XT_LIVE_COOKIE_FILE
NODE_PATH=/home/chinhan/xT/desk/node_modules node <<'JS'
const fs = require('fs');
(async () => {
  const { chromium } = require('playwright');
  const b = '/home/chinhan/.cache/xt-shared-browser';
  const s = JSON.parse(fs.readFileSync(`${b}/state.json`, 'utf8'));
  const uid = process.getuid();
  if (s.display !== ':90' || fs.statSync(`${b}/state.json`).uid !== uid || fs.statSync(`${b}/chrome-profile`).uid !== uid) throw Error();
  const cmd = pid => {
    if (!Number.isInteger(pid) || pid < 2 || fs.statSync(`/proc/${pid}`).uid !== uid) throw Error();
    return fs.readFileSync(`/proc/${pid}/cmdline`, 'utf8').split('\0');
  };
  const xcmd = cmd(s.xvfb_pid);
  if (!xcmd.includes(':90') || !xcmd.some(x => x.endsWith('/Xvfb'))) throw Error();
  const chrome = cmd(s.chrome_pid);
  if (!chrome.includes(`--user-data-dir=${b}/chrome-profile`) || !chrome.includes(`--remote-debugging-port=${s.cdp_port}`)) throw Error();
  if (!fs.readFileSync(`/proc/${s.chrome_pid}/environ`, 'utf8').split('\0').includes('DISPLAY=:90')) throw Error();
  if (!Number.isInteger(s.cdp_port) || s.cdp_port < 1024 || s.cdp_port > 65535) throw Error();
  const path = process.env.XT_LIVE_COOKIE_FILE;
  if (!path || fs.statSync(path).uid !== uid || (fs.statSync(path).mode & 0o077)) throw Error();
  const exported = JSON.parse(fs.readFileSync(path, 'utf8'));
  const source = Array.isArray(exported) ? exported : exported.cookies;
  if (!Array.isArray(source)) throw Error();
  const cookies = source.filter(c => typeof c.domain === 'string' && /^(\.)?chatgpt\.com$/.test(c.domain.toLowerCase())).map(c => {
    if (typeof c.name !== 'string' || typeof c.value !== 'string') throw Error();
    const out = { name: c.name, value: c.value, domain: c.domain.toLowerCase(), path: c.path || '/', secure: c.secure === true, httpOnly: c.httpOnly === true };
    const expiry = c.expirationDate ?? c.expires;
    if (typeof expiry === 'number' && expiry > 0) out.expires = expiry;
    const sameSite = { strict: 'Strict', lax: 'Lax', none: 'None', no_restriction: 'None' }[String(c.sameSite || '').toLowerCase()];
    if (sameSite) out.sameSite = sameSite;
    return out;
  });
  if (!cookies.length) throw Error();
  const browser = await chromium.connectOverCDP(`http://127.0.0.1:${s.cdp_port}`);
  const context = browser.contexts()[0];
  if (!context) throw Error();
  await context.addCookies(cookies);
  console.log('Đã nạp cookie ChatGPT vào profile riêng; chưa điều hướng.');
  process.exit(0);
})().catch(() => { console.error('Không nạp được cookie; không in dữ liệu hoặc lỗi thô.'); process.exit(1); });
JS
unset XT_LIVE_COOKIE_FILE
```

Nếu module Playwright không sẵn ở `NODE_PATH`, dừng và ghi thiếu điều kiện; không cài công cụ trong lượt kit. Cookie không thay thế kiểm danh tính. Người vận hành tự mở ChatGPT trong noVNC, không copy cookie vào chat/log.

## 4. Connector và chạy câu chuyện thật — chỉ thực hiện sau

Trong ChatGPT, mở Settings → Apps; bật developer mode tại nơi host cung cấp, thêm hoặc refresh connector public MCP `https://xt-mcp.freepro.online/mcp`. Tên/vị trí nút có thể đổi theo host; lưu bằng chứng thao tác thật, không giả có nút. Hoàn tất OAuth đúng doanh nghiệp `binhminh`, bắt đầu requester; kiểm `inspect_workspace` sau mỗi reconnect/đổi actor. Khi đổi actor, dừng mọi ghi, dùng Disconnect/reconnect của connector và chọn tài khoản OAuth cần thử; nếu SSO giữ người cũ, dùng đăng xuất/chọn tài khoản khác ở màn hình xác thực. Kiểm lại `inspect_workspace` trước ghi; nhãn connector hay tên hiển thị không đủ. Không đổi hostname, OAuth policy hoặc quyền nền để làm xanh ca kiểm.

Chạy từng phần trong [prompts.md](prompts.md), giữ args/key nguyên khi replay. Quyết định duyệt chỉ hoàn tất sau người đúng quyền xác nhận ở Desk bằng password/TOTP theo yêu cầu thật; không gửi bí mật vào chat. Chỉ `confirmation_required` chưa phải đã duyệt. Phân biệt kênh H/P/D/I/WareTrack, ChatGPT và Studio; WareTrack hiện chỉ đọc nên tạo qua MCP không chứng minh tạo trong WareTrack. Host chặn trước MCP không chứng minh server từ chối. Giữ riêng trace, biên nhận, source ref/revision, ảnh/DOM và kết quả hồi phục app sau ca tắt.

## 5. Hậu kiểm DB độc lập chỉ đọc

Cần DSN đã cấu hình cho quản trị/BYPASSRLS có quyền đọc cả tenant chính và tenant đối chứng; collector từ chối role RLS hẹp vì dễ cho kết quả thiếu hàng giả thành “không tồn tại”. Collector dùng READ ONLY, REPEATABLE READ, timeout và rollback; không gọi setter tạo context, không hỗ trợ `--sql` hoặc ghi tùy ý. Chạy hậu kiểm khi app probe **còn tắt** vì plan mong đợi `disabled`; lưu kết quả rồi quản trị bật lại qua đề xuất/duyệt và lưu bằng chứng phục hồi riêng. DSN chỉ lấy qua môi trường, có thể là `service=...` từ libpq service đã cấu hình, không đặt credential literal trong lệnh:

```bash
read -r -s -p 'DSN verifier hoặc libpq service đã cấu hình: ' XT_LIVE_VERIFY_DSN
printf '\n'
export XT_LIVE_VERIFY_DSN
read -r -p 'SINCE ISO 8601 có múi giờ đã ghi: ' SINCE
read -r -p 'UNTIL ISO 8601 có múi giờ đã ghi: ' UNTIL
python -m scripts.live.verify --plan "$XT_LIVE_PLAN" --tenant binhminh --since "$SINCE" --until "$UNTIL"
unset XT_LIVE_VERIFY_DSN
```

Dùng Python của môi trường dự án đã có (ví dụ kích hoạt `.venv` trước); không cài hoặc đọc `.env`. Lệnh chỉ in bảng check đã làm sạch, không dump snapshot/receipt/PII. Exit 0 = các kiểm trạng thái trong plan đạt; 1 = có trạng thái/tác dụng không khớp; 2 = đầu vào, binding, thời gian hoặc quyền quan sát thiếu/không hợp lệ. Exit 0 **không** chứng nhận toàn bộ live: bản ghi vắng không chứng minh lỗi từ chối đã tới server; metadata egress masked không chứng minh mọi nội dung đã che; replay, nguồn và UI cần transcript riêng.

`xt_audit.verify` ở verifier kiểm tính nhất quán chuỗi toàn tenant, không chống tự tính lại/xóa đuôi nếu chưa có neo ngoài DB. Luồng neo hiện có được ghi tại `src/xt/entrypoints/audit_cli.py`: `xt audit checkpoint` và `xt audit verify --against-checkpoint latest`; chỉ chạy theo quy trình/quyền cấu hình checkpoint riêng, không tự cấp khóa hoặc coi đây là chức năng của kit.

## 6. Kiểm kit offline, không phải live

Các lệnh sau chỉ kiểm mã/fixture, không kết nối DB/browser. Dùng môi trường Python dự án đã có:

```bash
python -m pytest -q -o addopts='' tests/unit/live
python -m ruff check scripts/live tests/unit/live
python -m mypy --explicit-package-bases scripts/live tests/unit/live
```

Fixture `tests/unit/live/fixtures/demo.json` là wrapper gồm `plan` và `snapshot`, không truyền wrapper trực tiếp cho verifier. Tách vào thư mục tạm ngoài repo rồi chạy chế độ offline:

```bash
umask 077
XT_LIVE_OFFLINE_DIR=$(mktemp -d /tmp/xt-live-offline.XXXXXX)
export XT_LIVE_OFFLINE_DIR
python - <<'PY'
import json, os
from pathlib import Path
d = Path(os.environ['XT_LIVE_OFFLINE_DIR'])
fixture = json.loads(Path('tests/unit/live/fixtures/demo.json').read_text())
for key in ('plan', 'snapshot'):
    target = d / f'{key}.json'
    target.write_text(json.dumps(fixture[key])); target.chmod(0o600)
PY
python -m scripts.live.verify --plan "$XT_LIVE_OFFLINE_DIR/plan.json" --snapshot "$XT_LIVE_OFFLINE_DIR/snapshot.json" --tenant synthetic-demo --since 2026-10-18T10:00:00Z --until 2026-10-18T11:00:00Z
rm "$XT_LIVE_OFFLINE_DIR/plan.json" "$XT_LIVE_OFFLINE_DIR/snapshot.json"
rmdir "$XT_LIVE_OFFLINE_DIR"
unset XT_LIVE_OFFLINE_DIR
```

Phải có banner `OFFLINE / FIXTURE — not LIVE server evidence`. Dù bảng toàn PASS, giữ nhãn offline. Sao chép mẫu bằng chứng ra ngoài repo:

```bash
read -r -p 'Đường dẫn bằng chứng riêng tư ngoài repo: ' XT_LIVE_EVIDENCE
cp scripts/live/evidence-template.md "$XT_LIVE_EVIDENCE"
chmod 600 "$XT_LIVE_EVIDENCE"
```

Điền source manifest, run-id, thời gian, lớp bằng chứng, kết quả, giới hạn và đường dẫn/hash tệp thật. Không commit cookie, profile, DSN, raw PII hoặc artifact live; không kết luận §9/U01–U15 xanh khi còn thiếu nguồn/UI/fixture hoặc chưa chạy.
