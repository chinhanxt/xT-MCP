#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Tải JWKS của Keycloak realm, kiểm chặt, ghi atomically cho PostgREST (stdlib).

Fail-closed: bất kỳ lỗi nào (mạng, issuer lệch, khóa riêng, không còn khóa ký hợp lệ) thì thoát mã 1 và KHÔNG đụng
tệp hiện có. Đích mặc định `${XT_SECRETS_DIR:-~/.config/xt}/pgrst/jwks.json`, bị từ chối nếu nằm trong repo.
Xoay khóa: chạy lại rồi `docker compose kill -s USR2 postgrest` (nạp lại cấu hình, không restart).

  python3 scripts/fetch_jwks.py [--base REALM_URL] [--issuer ISS] [--out PATH]

Nguồn tin cậy: chỉ `--base` (mặc định loopback Keycloak). Cả discovery lẫn JWKS đều lấy dưới `--base`; discovery phải
khai đúng `issuer` kỳ vọng VÀ `jwks_uri` có đường dẫn `/realms/<realm>/protocol/openid-connect/certs`. Không có cách chỉ
một URL JWKS tùy ý rồi gán cho issuer. Giá trị `issuer` này phải trùng `xt_meta.platform['pgrst_issuer']` (SQL).
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import NoReturn

DEPLOY = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from envfile import load_env  # noqa: E402

ALLOWED_ALG = {"RS256", "ES256"}
PRIVATE_PARAMS = {"d", "p", "q", "dp", "dq", "qi", "oth", "k"}
MAX_BYTES = 256 * 1024


def fail(msg: str) -> NoReturn:
    sys.exit(f"fetch_jwks: {msg}")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a: object, **k: object) -> None:  # type: ignore[override]
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def check_base(base: str) -> str:
    """http chỉ cho loopback (Keycloak nội bộ); còn lại bắt buộc https. Không userinfo/query/fragment."""
    u = urllib.parse.urlsplit(base)
    loop = u.hostname in ("127.0.0.1", "localhost", "::1")
    if u.scheme not in ("http", "https") or not u.hostname or (u.scheme == "http" and not loop):
        fail(f"base không hợp lệ (http chỉ cho loopback, còn lại https): {base}")
    if u.username or u.password or u.query or u.fragment:
        fail("base không được chứa userinfo/query/fragment")
    return base.rstrip("/")


def get_json(url: str, timeout: float) -> object:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with _OPENER.open(req, timeout=timeout) as r:  # noqa: S310 (scheme đã kiểm ở check_base)
            if r.status != 200:
                fail(f"{url}: HTTP {r.status}")
            raw = r.read(MAX_BYTES + 1)
    except (OSError, urllib.error.URLError) as e:  # gồm HTTPError, kể cả 3xx (không theo redirect)
        fail(f"{url}: {e}")
    if len(raw) > MAX_BYTES:
        fail(f"{url}: phản hồi quá lớn")
    try:
        return json.loads(raw)
    except ValueError as e:
        fail(f"{url}: không phải JSON ({e})")


def _bits(v: str) -> int:
    pad = "=" * (-len(v) % 4)
    return int.from_bytes(base64.urlsafe_b64decode(v + pad), "big").bit_length()


def valid_signing_key(k: object) -> dict | None:
    """Trả khóa công khai chỉ gồm tham số cần thiết, hoặc None nếu không phải khóa ký chấp nhận được."""
    if not isinstance(k, dict):
        return None
    if PRIVATE_PARAMS & k.keys():
        fail(f"JWKS chứa tham số khóa riêng ở kid={k.get('kid')!r}: từ chối toàn bộ")
    kid, kty, alg = k.get("kid"), k.get("kty"), k.get("alg")
    if not isinstance(kid, str) or not kid or k.get("use", "sig") != "sig" or alg not in ALLOWED_ALG:
        return None
    # Không xóa giới hạn của JWK rồi biến khóa chỉ dùng ký/mã hóa thành khóa xác minh.
    operations = k.get("key_ops", ["verify"])
    if not isinstance(operations, list) or "verify" not in operations:
        return None
    try:
        if kty == "RSA" and alg == "RS256":
            if _bits(k["n"]) < 2048 or _bits(k["e"]) < 2:
                return None
            return {"kty": kty, "kid": kid, "use": "sig", "alg": alg, "n": k["n"], "e": k["e"]}
        if kty == "EC" and alg == "ES256" and k.get("crv") == "P-256":
            if _bits(k["x"]) < 200 or _bits(k["y"]) < 200:
                return None
            return {"kty": kty, "kid": kid, "use": "sig", "alg": alg, "crv": "P-256", "x": k["x"], "y": k["y"]}
    except (KeyError, ValueError, TypeError):
        return None
    return None


def build_jwks(doc: object) -> dict:
    if not isinstance(doc, dict) or not isinstance(doc.get("keys"), list):
        fail("JWKS thiếu mảng `keys`")
    keys: dict[str, dict] = {}
    dup: set[str] = set()
    for raw in doc["keys"]:
        k = valid_signing_key(raw)
        if k is None:
            continue
        if k["kid"] in keys:
            dup.add(k["kid"])
        keys[k["kid"]] = k
    for kid in dup:  # kid trùng: không đoán khóa nào đúng
        del keys[kid]
    if not keys:
        fail("không còn khóa ký RS256/ES256 hợp lệ nào (RSA >= 2048 bit hoặc EC P-256)")
    return {"keys": list(keys.values())}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", help="URL realm; mặc định http://127.0.0.1:$KC_HTTP_PORT/realms/$XT_REALM")
    ap.add_argument("--issuer", help="issuer kỳ vọng; mặc định https://$XT_DOMAIN_AUTH/realms/$XT_REALM")
    ap.add_argument("--out", help="mặc định: ${XT_SECRETS_DIR:-~/.config/xt}/pgrst/jwks.json")
    ap.add_argument("--timeout", type=float, default=5.0)
    a = ap.parse_args()

    env = {**load_env(DEPLOY / ".env"), **os.environ}
    realm = env.get("XT_REALM") or "xt"
    base = check_base(a.base or f"http://127.0.0.1:{env.get('KC_HTTP_PORT') or '18080'}/realms/{realm}")
    if not base.endswith(f"/realms/{realm}"):
        fail(f"base phải kết thúc bằng /realms/{realm}")
    certs = f"{base}/protocol/openid-connect/certs"
    issuer = a.issuer or env.get("XT_PGRST_ISSUER") or (
        f"https://{env['XT_DOMAIN_AUTH']}/realms/{realm}" if env.get("XT_DOMAIN_AUTH") else ""
    )
    if not issuer:
        fail("thiếu issuer kỳ vọng (--issuer hoặc XT_DOMAIN_AUTH)")
    sec = Path(env.get("XT_SECRETS_DIR") or "~/.config/xt").expanduser()
    out = (Path(a.out).expanduser() if a.out else sec / "pgrst" / "jwks.json").resolve()
    repo = DEPLOY.parent.resolve()
    if out == repo or repo in out.parents:
        fail(f"từ chối ghi trong repo: {out}")

    # JWKS chỉ gắn với issuer nếu discovery của chính realm đó khai đúng issuer (KC_HOSTNAME cố định).
    disc = get_json(f"{base}/.well-known/openid-configuration", a.timeout)
    got = disc.get("issuer") if isinstance(disc, dict) else None
    if got != issuer:
        fail(f"issuer lệch: kỳ vọng {issuer!r}, Keycloak trả {got!r}")
    uri = disc.get("jwks_uri")
    if not isinstance(uri, str) or urllib.parse.urlsplit(uri).path != f"/realms/{realm}/protocol/openid-connect/certs":
        fail("discovery khai jwks_uri không thuộc realm này")

    jwks = build_jwks(get_json(certs, a.timeout))
    data = json.dumps(jwks, indent=2, sort_keys=True) + "\n"

    out.parent.mkdir(parents=True, exist_ok=True)
    out.parent.chmod(0o755)  # uid 65534 của container phải đọc được; JWKS là khóa công khai
    fd, tmp = tempfile.mkstemp(prefix=".jwks.", dir=out.parent)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, out)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    print(f"đã ghi {out}: {len(jwks['keys'])} khóa ({', '.join(k['kid'][:12] for k in jwks['keys'])}), issuer {issuer}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
