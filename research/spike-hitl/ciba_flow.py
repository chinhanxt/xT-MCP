# SPDX-License-Identifier: Apache-2.0
"""A. Keycloak CIBA (poll mode) end-to-end + các phép thử agent giả mạo."""
import json, os, sys, time
import httpx, jwt
sys.path.insert(0, os.path.dirname(__file__))
from common import OIDC, CHANNEL, CIBA_CLIENT, CIBA_SECRET, AGENT_CLIENT, AGENT_SECRET, verify_kc, show
from receipt import digest

OUTBOX = "/tmp/xt-spike-hitl/telegram_outbox.jsonl"
TG_SECRET = "tg-webhook-secret-xyz"
h = httpx.Client(timeout=15)

R42 = dict(id="R-42", type="advance", requester="bob", amount=12_000_000, currency="VND",
           purpose="Tạm ứng công tác Đà Nẵng", payee_account="0123-bob")
json.dump(R42, open("/tmp/xt-spike-hitl/r42.json", "w"), ensure_ascii=False)
D = digest(R42)
code = f"R-42#{D[:8]}"
json.dump({code: {"text": "Duyệt tạm ứng 12.000.000đ #R-42 (bob, công tác Đà Nẵng)", "digest": D}},
          open("/tmp/xt-spike-hitl/requests.json", "w"), ensure_ascii=False)

def bc_auth(client, secret, binding, user="alice"):
    return h.post(f"{OIDC}/ext/ciba/auth", data=dict(client_id=client, client_secret=secret,
                  scope="openid", login_hint=user, binding_message=binding))
def poll(client, secret, rid):
    return h.post(f"{OIDC}/token", data=dict(grant_type="urn:openid:params:grant-type:ciba",
                  client_id=client, client_secret=secret, auth_req_id=rid))
def last_tg():
    return json.loads(open(OUTBOX).read().splitlines()[-1])
def tap(msg, idx, from_id=None, secret=TG_SECRET):
    cb = msg["reply_markup"]["inline_keyboard"][0][idx]["callback_data"]
    return h.post(f"{CHANNEL}/telegram/webhook", headers={"X-Telegram-Bot-Api-Secret-Token": secret},
                  json={"callback_query": {"from": {"id": from_id or msg["chat_id"]}, "data": cb}})

def verify_ciba_approval(token, pending):
    """Điều kiện xT chấp nhận một token làm bằng chứng duyệt CIBA."""
    c = verify_kc(token)
    if c.get("azp") != CIBA_CLIENT: raise PermissionError(f"azp={c.get('azp')} != {CIBA_CLIENT}")
    if c.get("preferred_username") != pending["approver"]: raise PermissionError("sub không phải người được hỏi")
    if c["auth_time"] < pending["started"] - 1: raise PermissionError("auth_time trước thời điểm hỏi")
    return c

print("== A0. binding_message tiếng Việt theo đề bài")
r = bc_auth(CIBA_CLIENT, CIBA_SECRET, "Duyệt tạm ứng 12.000.000đ #R-42")
print(r.status_code, r.text)

print(f"\n== A1. backend xin xác thực alice, binding_message={code}")
t0 = int(time.time())
r = bc_auth(CIBA_CLIENT, CIBA_SECRET, code); print(r.status_code, r.text[:400])
rid = r.json()["auth_req_id"]
pending = dict(request_id="R-42", approver="alice", auth_req_id=rid, started=t0)
import base64
print("auth_req_id là JWE, header:", base64.urlsafe_b64decode(rid.split(".")[0] + "==").decode(), "| số phần:", rid.count(".") + 1)
r = poll(CIBA_CLIENT, CIBA_SECRET, rid); print("poll trước khi duyệt:", r.status_code, r.text)
msg = last_tg(); show("tin nhắn Telegram (outbox)", msg)

print("\n== A2. tấn công trong lúc chờ")
r = bc_auth(AGENT_CLIENT, AGENT_SECRET, code); print("agent tự gọi backchannel auth:", r.status_code, r.text)
r = poll(AGENT_CLIENT, AGENT_SECRET, rid); print("agent poll bằng auth_req_id bị lộ:", r.status_code, r.text)
at = h.post(f"{OIDC}/token", data=dict(grant_type="password", client_id=AGENT_CLIENT, client_secret=AGENT_SECRET,
            username="alice", password="alice-pw", scope="openid")).json()
print("agent password-grant cho alice (không OTP):", "access_token" in at, at.get("error"), at.get("error_description"))
import pyotp
from common import TOTP_B32
# Mô phỏng worst case: agent đang giữ token MCP mang sub=alice (cấp từ lần alice đăng nhập trước đó)
at = h.post(f"{OIDC}/token", data=dict(grant_type="password", client_id=AGENT_CLIENT, client_secret=AGENT_SECRET,
            username="alice", password="alice-pw", totp=pyotp.TOTP(TOTP_B32).now(), scope="openid")).json()
if "access_token" not in at:   # Keycloak chặn tái dùng TOTP trong cùng cửa sổ 30s -> lấy mã kế tiếp
    time.sleep(31 - time.time() % 30)
    at = h.post(f"{OIDC}/token", data=dict(grant_type="password", client_id=AGENT_CLIENT, client_secret=AGENT_SECRET,
                username="alice", password="alice-pw", totp=pyotp.TOTP(TOTP_B32).now(), scope="openid")).json()
print("(mô phỏng) token MCP của agent mang sub=alice:", "access_token" in at)
for gt in ("password", "client_credentials"):
    r = h.post(f"{OIDC}/token", data=dict(grant_type=gt, client_id=CIBA_CLIENT, client_secret=CIBA_SECRET,
               username="alice", password="alice-pw"))
    print(f"client CIBA cấp token qua grant {gt}:", r.status_code, r.text)
agent_tok = at.get("access_token") or h.post(f"{OIDC}/token", data=dict(grant_type="client_credentials",
            client_id=AGENT_CLIENT, client_secret=AGENT_SECRET)).json()["access_token"]
r = h.post(f"{OIDC}/ext/ciba/auth/callback", json={"status": "SUCCEED"},
           headers={"Authorization": f"Bearer {agent_tok}"}); print("agent gọi KC callback bằng token của nó:", r.status_code, r.text)
r = h.post(f"{OIDC}/ext/ciba/auth/callback", json={"status": "SUCCEED"},
           headers={"Authorization": f"Bearer {rid}"}); print("agent gọi KC callback bằng auth_req_id:", r.status_code, r.text)
r = tap(msg, 0, secret="guess"); print("webhook giả, sai secret_token:", r.status_code, r.text)
r = tap(msg, 0, from_id=9999); print("webhook đúng secret, sai chat_id:", r.status_code, r.text)
r = poll(CIBA_CLIENT, CIBA_SECRET, rid); print("poll sau các tấn công:", r.status_code, r.text)

print("\n== A3. alice bấm Duyệt trên điện thoại")
time.sleep(1)
r = tap(msg, 0); print("webhook:", r.status_code, r.text)
r = tap(msg, 0); print("bấm lại (replay callback_data):", r.status_code, r.text)
for _ in range(10):
    r = poll(CIBA_CLIENT, CIBA_SECRET, rid)
    if r.status_code == 200 or r.json().get("error") not in ("authorization_pending", "slow_down"): break
    time.sleep(2)
print("poll:", r.status_code)
tok = r.json()
claims = verify_ciba_approval(tok["access_token"], pending)
show("access_token claims (đã kiểm chữ ký)", {k: claims.get(k) for k in
     ("iss", "sub", "preferred_username", "azp", "typ", "auth_time", "iat", "acr", "amr", "sid", "scope", "jti")})
idc = verify_kc(tok["id_token"], audience=CIBA_CLIENT)
show("id_token claims", {k: idc.get(k) for k in ("sub", "preferred_username", "auth_time", "acr", "amr", "azp")})
print(f"auth_time - lúc hỏi = {claims['auth_time'] - t0}s")
json.dump(claims, open("/tmp/xt-spike-hitl/evidence_ciba.json", "w"))
r = poll(CIBA_CLIENT, CIBA_SECRET, rid); print("poll lần 2 (auth_req_id dùng lại):", r.status_code, r.text)

print("\n== A4. token của agent nộp làm bằng chứng")
for label, t in (("agent alice password-grant", at.get("access_token")),
                 ("agent service-account", h.post(f"{OIDC}/token", data=dict(grant_type="client_credentials",
                      client_id=AGENT_CLIENT, client_secret=AGENT_SECRET)).json()["access_token"])):
    if not t: continue
    c = jwt.decode(t, options={"verify_signature": False})
    try: verify_ciba_approval(t, pending); print(f"[OK ] {label}")
    except Exception as e: print(f"[REJ] {label}: {e}  (azp={c.get('azp')} sub={c.get('preferred_username')} acr={c.get('acr')} amr={c.get('amr')})")

print("\n== A5. đường từ chối")
r = bc_auth(CIBA_CLIENT, CIBA_SECRET, code); rid2 = r.json()["auth_req_id"]
time.sleep(0.5); r = tap(last_tg(), 1); print("webhook Từ chối:", r.status_code, r.text)
time.sleep(2); r = poll(CIBA_CLIENT, CIBA_SECRET, rid2); print("poll:", r.status_code, r.text)
