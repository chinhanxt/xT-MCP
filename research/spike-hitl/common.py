# SPDX-License-Identifier: Apache-2.0
"""Hằng số dùng chung cho spike HITL (Keycloak 26.6.3 cổng 19300)."""
import base64, json, os, httpx, jwt

KC = os.environ.get("KC", "http://localhost:19300")
REALM = "xt-hitl"
ISS = f"{KC}/realms/{REALM}"
OIDC = f"{ISS}/protocol/openid-connect"
CHANNEL = "http://127.0.0.1:19301"            # authentication-channel endpoint (channel.py)
CONFIRM_CB = "http://localhost:19302/confirm/cb"  # redirect_uri của trang confirm do xT sở hữu
DESK_CB = "http://localhost:19302/desk/cb"
CIBA_CLIENT, CIBA_SECRET = "xt-backend-ciba", "ciba-backend-secret"
AGENT_CLIENT, AGENT_SECRET = "xt-agent", "agent-secret"
# Secret TOTP đã biết (Keycloak lưu chuỗi thô; app authenticator dùng base32 của bytes chuỗi này)
TOTP_RAW = "xtSpikeTotpSecret20B"
TOTP_B32 = base64.b32encode(TOTP_RAW.encode()).decode().rstrip("=")

_jwks = None
def verify_kc(token: str, audience=None) -> dict:
    """Kiểm chữ ký RS256 bằng JWKS của realm + iss + exp."""
    global _jwks
    if _jwks is None:
        _jwks = jwt.PyJWKClient(f"{OIDC}/certs", cache_keys=False)
    key = _jwks.get_signing_key_from_jwt(token).key
    return jwt.decode(token, key, algorithms=["RS256"], issuer=ISS,
                      audience=audience, options={"verify_aud": audience is not None})

def show(title, obj):
    print(f"--- {title}")
    print(json.dumps(obj, ensure_ascii=False, indent=1) if not isinstance(obj, str) else obj)
