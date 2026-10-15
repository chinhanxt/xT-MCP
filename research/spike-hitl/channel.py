# SPDX-License-Identifier: Apache-2.0
"""Authentication-channel endpoint cho Keycloak CIBA (HTTP provider) + webhook Telegram giả lập.

Keycloak POST /ciba/auth-channel (Bearer = JWT do Keycloak ký, chỉ channel giữ) -> channel gửi tin Telegram
có inline button mang callback_data ký HMAC (pending_id, chat_id, decision). Telegram POST /telegram/webhook
(header X-Telegram-Bot-Api-Secret-Token) -> channel kiểm secret + chat_id đã liên kết + chữ ký + dùng một lần
-> gọi Keycloak /ext/ciba/auth/callback bằng chính Bearer đó.
Ở spike, "gửi Telegram" = ghi vào outbox JSONL (không có bot token thật)."""
import hashlib, hmac, json, os, secrets, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import httpx

sys.path.insert(0, os.path.dirname(__file__))
from common import OIDC

OUTBOX = os.environ.get("OUTBOX", "/tmp/xt-spike-hitl/telegram_outbox.jsonl")
WEBHOOK_SECRET = os.environ.get("TG_WEBHOOK_SECRET", "tg-webhook-secret-xyz")   # chỉ server xT + Telegram biết
NONCE_KEY = os.environ.get("TG_NONCE_KEY", "nonce-hmac-key").encode()
CHAT_LINK = {"alice": 7001}                    # login_hint -> chat_id đã liên kết (bước link 1 lần)
REQUESTS_DB = "/tmp/xt-spike-hitl/requests.json"  # xT tra nội dung đầy đủ theo binding code
PENDING, USED = {}, set()
LOCK = threading.Lock()

def sign(pid, chat, decision):
    msg = f"{pid}|{chat}|{decision}".encode()
    return hmac.new(NONCE_KEY, msg, hashlib.sha256).hexdigest()[:24]

class H(BaseHTTPRequestHandler):
    def log_message(self, fmt, *a): print("[channel]", fmt % a, flush=True)
    def reply(self, code, obj=None):
        body = json.dumps(obj or {}, ensure_ascii=False).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if self.path == "/ciba/auth-channel":
            auth = self.headers.get("Authorization", "")
            if not auth.startswith("Bearer "):
                return self.reply(401)
            print("[channel] from Keycloak:", json.dumps(data, ensure_ascii=False), flush=True)
            user, code = data.get("login_hint"), data.get("binding_message", "")
            chat = CHAT_LINK.get(user)
            if chat is None:
                return self.reply(400, {"error": "user chưa liên kết Telegram"})
            reqs = json.load(open(REQUESTS_DB)) if os.path.exists(REQUESTS_DB) else {}
            detail = reqs.get(code, {}).get("text", "(không tìm thấy yêu cầu)")
            pid = secrets.token_urlsafe(8)
            with LOCK:
                PENDING[pid] = dict(bearer=auth[7:], user=user, chat=chat, code=code, t=time.time())
            msg = dict(chat_id=chat, text=f"{detail}\nMã đối chiếu: {code}",
                       reply_markup={"inline_keyboard": [[
                           {"text": "Duyệt", "callback_data": f"{pid}:A:{sign(pid, chat, 'A')}"},
                           {"text": "Từ chối", "callback_data": f"{pid}:R:{sign(pid, chat, 'R')}"}]]})
            with open(OUTBOX, "a") as f:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")
            return self.reply(201)

        if self.path == "/telegram/webhook":
            if not hmac.compare_digest(self.headers.get("X-Telegram-Bot-Api-Secret-Token", ""), WEBHOOK_SECRET):
                return self.reply(403, {"error": "bad secret_token"})
            cq = data.get("callback_query", {})
            try:
                pid, dec, sig = cq["data"].split(":")
            except Exception:
                return self.reply(400, {"error": "bad callback_data"})
            with LOCK:
                p = PENDING.get(pid)
                if p is None or pid in USED:
                    return self.reply(409, {"error": "unknown or used"})
                if cq.get("from", {}).get("id") != p["chat"]:
                    return self.reply(403, {"error": "chat_id không khớp người được hỏi"})
                if not hmac.compare_digest(sig, sign(pid, p["chat"], dec)):
                    return self.reply(403, {"error": "bad signature"})
                USED.add(pid)
            status = "SUCCEED" if dec == "A" else "UNAUTHORIZED"
            r = httpx.post(f"{OIDC}/ext/ciba/auth/callback", json={"status": status},
                           headers={"Authorization": f"Bearer {p['bearer']}"}, timeout=10)
            print(f"[channel] KC callback {status} -> {r.status_code} {r.text}", flush=True)
            return self.reply(200, {"kc_callback": r.status_code, "status": status})
        self.reply(404)

if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 19301), H).serve_forever()
