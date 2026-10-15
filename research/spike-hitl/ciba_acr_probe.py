# SPDX-License-Identifier: Apache-2.0
"""A6. Thăm dò: CIBA với acr_values=urn:xt:acr:otp -> token có acr gì? (Keycloak không tự xác thực user trong CIBA)."""
import json, os, sys, time, httpx
sys.path.insert(0, os.path.dirname(__file__))
from common import OIDC, CHANNEL, CIBA_CLIENT, CIBA_SECRET, verify_kc
h = httpx.Client(timeout=15)
r = h.post(f"{OIDC}/ext/ciba/auth", data=dict(client_id=CIBA_CLIENT, client_secret=CIBA_SECRET, scope="openid",
           login_hint="alice", binding_message="R-42#probe", acr_values="urn:xt:acr:otp"))
print("backchannel auth:", r.status_code); rid = r.json()["auth_req_id"]
time.sleep(0.5)
msg = json.loads(open("/tmp/xt-spike-hitl/telegram_outbox.jsonl").read().splitlines()[-1])
cb = msg["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
print("webhook:", h.post(f"{CHANNEL}/telegram/webhook", headers={"X-Telegram-Bot-Api-Secret-Token": "tg-webhook-secret-xyz"},
      json={"callback_query": {"from": {"id": msg["chat_id"]}, "data": cb}}).text)
time.sleep(2)
t = h.post(f"{OIDC}/token", data=dict(grant_type="urn:openid:params:grant-type:ciba", client_id=CIBA_CLIENT,
           client_secret=CIBA_SECRET, auth_req_id=rid)).json()
c = verify_kc(t["access_token"]); print("token:", {k: c.get(k) for k in ("azp", "acr", "amr", "auth_time")})
