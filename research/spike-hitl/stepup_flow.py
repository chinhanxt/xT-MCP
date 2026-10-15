# SPDX-License-Identifier: Apache-2.0
"""B. Step-up xác thực tươi: client public RIÊNG xt-confirm, PKCE S256, prompt=login + max_age=0 + acr otp.

"Trình duyệt" = httpx.Client có cookie jar (headless, không đụng display). Người thật = biết mật khẩu + TOTP
trên điện thoại (pyotp với secret đã biết). Agent điều khiển trình duyệt = có cookie phiên + autofill mật khẩu,
KHÔNG có TOTP."""
import base64, hashlib, html, json, os, re, secrets, sys, time
from urllib.parse import urlencode, urlparse, parse_qs
import httpx, jwt, pyotp
sys.path.insert(0, os.path.dirname(__file__))
from common import OIDC, CONFIRM_CB, DESK_CB, TOTP_B32, verify_kc, show

totp = pyotp.TOTP(TOTP_B32)
browser = httpx.Client(timeout=15, follow_redirects=False)   # trình duyệt của user (agent có thể lái)
# Keycloak đặt cookie Secure; trình duyệt thật coi http://localhost là secure context -> cho phép tương tự
import http.cookiejar
http.cookiejar.DefaultCookiePolicy.return_ok_secure = lambda self, cookie, request: True
backend = httpx.Client(timeout=15)                           # backend xT

def form(page, fid):
    m = re.search(rf'<form[^>]*id="{fid}"[^>]*action="([^"]+)"', page) or \
        re.search(rf'<form[^>]*action="([^"]+)"[^>]*id="{fid}"', page)
    return html.unescape(m.group(1)) if m else None

def follow(r):
    """Đi theo redirect trong Keycloak; dừng khi về redirect_uri (trả code) hoặc tới trang HTML."""
    while r.status_code in (302, 303):
        loc = r.headers["location"]
        if loc.startswith("http://localhost:19302/"):
            return ("redirect", loc)
        r = browser.get(loc)
    return ("page", r.text)

def pkce():
    v = secrets.token_urlsafe(48)
    return v, base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).decode().rstrip("=")

def authorize_url(client, redirect, challenge, state, nonce, **extra):
    q = dict(response_type="code", client_id=client, redirect_uri=redirect, scope="openid",
             state=state, nonce=nonce, code_challenge=challenge, code_challenge_method="S256", **extra)
    return f"{OIDC}/auth?{urlencode(q)}"

def submit_login(page, pw="alice-pw"):
    a = form(page, "kc-form-login")
    return follow(browser.post(a, data=dict(username="alice", password=pw, credentialId="")))

def submit_otp(page, code):
    a = form(page, "kc-otp-login-form")
    return follow(browser.post(a, data=dict(otp=code)))

def where(res):
    kind, v = res
    if kind == "redirect":
        return "REDIRECT có code" if "code=" in v else f"REDIRECT {v[:120]}"
    if 'id="kc-otp-login-form"' in v: return "TRANG OTP"
    if 'id="kc-form-login"' in v: return "TRANG MẬT KHẨU"
    m = re.search(r'kc-feedback-text">([^<]+)', v) or re.search(r'id="input-error[^"]*"[^>]*>\s*([^<]+)', v)
    return "TRANG KHÁC: " + (m.group(1).strip() if m else v[:200])

def exchange(client, redirect, code, verifier):
    d = dict(grant_type="authorization_code", client_id=client, redirect_uri=redirect, code=code)
    if verifier: d["code_verifier"] = verifier
    return backend.post(f"{OIDC}/token", data=d)

FRESH = 120
def verify_confirm(id_token, conf):
    """Điều kiện server xT chấp nhận làm bằng chứng step-up cho confirmation `conf`."""
    c = verify_kc(id_token, audience="xt-confirm")
    errs = []
    if c.get("azp") != "xt-confirm": errs.append(f"azp={c.get('azp')}")
    if c.get("nonce") != conf["nonce"]: errs.append("nonce không gắn với confirmation này")
    if c.get("auth_time", 0) < conf["created_at"]: errs.append(f"auth_time trước lúc tạo confirmation ({conf['created_at'] - c.get('auth_time', 0)}s)")
    if time.time() - c.get("auth_time", 0) > FRESH: errs.append("auth_time quá 120s")
    if c.get("acr") != "urn:xt:acr:otp": errs.append(f"acr={c.get('acr')}")
    if "otp" not in (c.get("amr") or []): errs.append(f"amr={c.get('amr')}")
    if c.get("preferred_username") != conf["approver"]: errs.append("sai người duyệt")
    if errs: raise PermissionError("; ".join(errs))
    return c

def pick(c): return {k: c.get(k) for k in ("preferred_username", "azp", "auth_time", "iat", "acr", "amr", "nonce", "sid")}

def human_otp(page):
    """Người thật đọc mã trên điện thoại; Keycloak chặn tái dùng mã cùng cửa sổ 30s -> chờ mã kế tiếp."""
    r = submit_otp(page, totp.now())
    if r[0] == "page" and 'kc-otp-login-form' in r[1]:
        print("   mã OTP hiện tại đã dùng trong cửa sổ này -> Keycloak từ chối tái dùng; chờ mã kế tiếp")
        time.sleep(31 - time.time() % 30)
        r = submit_otp(r[1], totp.now())
    return r

# ---------------------------------------------------------------- B0. phiên Desk thường ngày
print("== B0. alice đăng nhập Desk (phiên SSO trong trình duyệt)")
v, ch = pkce()
r = follow(browser.get(authorize_url("xt-desk", DESK_CB, ch, "s0", "n0")))
r = submit_login(r[1]); print("sau mật khẩu:", where(r))
if r[0] == "page": r = human_otp(r[1]); print("sau OTP:", where(r))
code = parse_qs(urlparse(r[1]).query)["code"][0]
desk = exchange("xt-desk", DESK_CB, code, v).json()
desk_c = verify_kc(desk["id_token"], audience="xt-desk"); show("Desk id_token", pick(desk_c))
time.sleep(3)

# ---------------------------------------------------------------- confirmation do xT tạo
conf = dict(id="C-42", request_id="R-42", approver="alice", created_at=int(time.time()),
            nonce=hashlib.sha256(b"C-42|" + secrets.token_bytes(16)).hexdigest()[:32])
verifier, challenge = pkce()     # verifier chỉ nằm ở server xT, gắn với C-42
state = secrets.token_urlsafe(12)
URL = authorize_url("xt-confirm", CONFIRM_CB, challenge, state, conf["nonce"],
                    prompt="login", max_age="0", acr_values="urn:xt:acr:otp")
print("\nconfirm authorize URL:", URL)

print("\n== B1. agent lái trình duyệt (có cookie phiên + autofill mật khẩu), không có OTP")
r = follow(browser.get(URL)); print("mở URL:", where(r))
r = submit_login(r[1]); print("submit mật khẩu (autofill):", where(r))
r = submit_otp(r[1], "000000"); print("đoán OTP 000000:", where(r))
r = submit_otp(r[1], "123456") if r[0] == "page" and "kc-otp-login-form" in r[1] else r; print("đoán OTP 123456:", where(r))

print("\n== B2. agent xoá prompt/max_age/acr_values khỏi URL, dựa vào cookie SSO")
v2, ch2 = pkce()
r = follow(browser.get(authorize_url("xt-confirm", CONFIRM_CB, ch2, "s2", conf["nonce"])))
print("mở URL đã bỏ tham số:", where(r))

print("\n== B3. agent lấy token bằng phiên Desk (prompt=none, client xt-desk) rồi nộp")
v3, ch3 = pkce()
r = follow(browser.get(authorize_url("xt-desk", DESK_CB, ch3, "s3", conf["nonce"], prompt="none")))
print("prompt=none trên xt-desk:", where(r))
t3 = exchange("xt-desk", DESK_CB, parse_qs(urlparse(r[1]).query)["code"][0], v3).json()
try: verify_confirm(t3["id_token"], conf); print("[OK ] ???")
except PermissionError as e: print("[REJ] verify_confirm(desk token):", e)
except jwt.InvalidAudienceError as e: print("[REJ] verify_confirm(desk token): aud sai:", e)
try:
    c = verify_kc(t3["id_token"], audience="xt-desk")
    print("   (nếu bỏ qua kiểm aud) claims:", json.dumps(pick(c)))
except Exception as e: print(e)

def human_confirm(url):
    r = follow(browser.get(url)); print("mở URL:", where(r))
    r = submit_login(r[1]); print("mật khẩu:", where(r))
    r = human_otp(r[1]); print("OTP:", where(r))
    return parse_qs(urlparse(r[1]).query)

print("\n== B4. người thật: mật khẩu + OTP trên điện thoại")
cb = human_confirm(URL)
assert cb["state"][0] == state
x = exchange("xt-confirm", CONFIRM_CB, cb["code"][0], None)
print("agent (thấy URL redirect) đổi code KHÔNG có verifier:", x.status_code, x.text)
x = exchange("xt-confirm", CONFIRM_CB, cb["code"][0], secrets.token_urlsafe(48))
print("agent đổi code với verifier đoán:", x.status_code, x.text)
print("-> code đã bị đốt; người thật duyệt lại (xT tạo PKCE mới, cùng C-42)")
verifier, challenge = pkce(); state = secrets.token_urlsafe(12)
URL = authorize_url("xt-confirm", CONFIRM_CB, challenge, state, conf["nonce"],
                    prompt="login", max_age="0", acr_values="urn:xt:acr:otp")
cb = human_confirm(URL)
tok = exchange("xt-confirm", CONFIRM_CB, cb["code"][0], verifier).json()
c = verify_confirm(tok["id_token"], conf)
show("[OK ] xt-confirm id_token (đã kiểm)", pick(c))
print(f"auth_time - created_at = {c['auth_time'] - conf['created_at']}s; now - auth_time = {int(time.time()) - c['auth_time']}s")
at = verify_kc(tok["access_token"]); show("access_token acr/amr", {k: at.get(k) for k in ("azp", "acr", "amr", "auth_time")})
json.dump(c, open("/tmp/xt-spike-hitl/evidence_stepup.json", "w"))

print("\n== B5. ngay sau đó, phiên SSO đã có LoA2: agent thử duyệt C-43 không cần OTP")
conf2 = dict(conf, id="C-43", created_at=int(time.time()), nonce=secrets.token_hex(16))
v5, ch5 = pkce()
r = follow(browser.get(authorize_url("xt-confirm", CONFIRM_CB, ch5, "s5", conf2["nonce"])))
print("URL bỏ prompt/max_age/acr_values:", where(r))
r = follow(browser.get(authorize_url("xt-confirm", CONFIRM_CB, ch5, "s5", conf2["nonce"], prompt="none")))
print("prompt=none:", where(r) if r[0] == "page" else r[1].split("?", 1)[1][:160])
r = follow(browser.get(authorize_url("xt-confirm", CONFIRM_CB, ch5, "s5", conf2["nonce"], acr_values="urn:xt:acr:pwd")))
print("hạ acr_values=pwd:", where(r))
try: verify_confirm(tok["id_token"], conf2)
except PermissionError as e: print("[REJ] tái dùng id_token C-42 cho C-43:", e)
