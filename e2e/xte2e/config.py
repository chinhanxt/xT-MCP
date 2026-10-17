# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Cấu hình harness: đọc `deploy/.env` (do `make env` sinh, không commit) + override môi trường.

Bí mật của người dùng test (mật khẩu, TOTP) nằm ở `${XT_SECRETS_DIR:-~/.config/xt}/e2e/demo-seed.json` (ngoài repo,
quyền 600), không bao giờ ghi vào repo hay báo cáo.
"""

from __future__ import annotations

import json
import os
import secrets
import string
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
E2E = ROOT / "e2e"

# Biến môi trường harness luôn ép cho stack e2e (không sửa deploy/.env của người dùng):
# - XT_MCP_LOCAL_CLIENTS rỗng: client public (xt-desk-agent) chỉ tier masked (decisions.md "Tier PII").
# - XT_DATA_CLIENTS: client headless duy nhất được xin scope xt-data: `xt-cli` (deploy/keycloak/provision.py,
#   public + PKCE, redirect loopback RFC 8252 mọi cổng); xt-workflow cũng chỉ cấp cho xt-cli (mặc định).
HEADLESS_CLIENT = "xt-cli"
HEADLESS_REDIRECT = "http://127.0.0.1:8765/callback"
STACK_OVERRIDES = {
    "XT_MCP_LOCAL_CLIENTS": "",
    "XT_DATA_CLIENTS": HEADLESS_CLIENT,
    # E12: bộ chọn tenant của Desk (nướng vào ảnh xt-desk) gợi ý đúng hai tenant của demo-seed.
    "DESK_OIDC_ORGANIZATIONS": "binhminh,hoamai",
}


def load_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        k, raw = line.split("=", 1)
        k = k.strip()
        if not k.replace("_", "").isalnum():
            continue
        if raw.startswith(("'", '"')):
            v = raw.strip().strip("'\"")
        else:
            v = raw.split(" #", 1)[0].strip()
        out[k] = v
    return out


@dataclass(frozen=True)
class Settings:
    env: dict[str, str]
    project: str
    gateway: tuple[str, int]
    hosts: dict[str, str]
    realm: str
    kc_admin: str
    secrets_dir: Path
    state_dir: Path
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def issuer(self) -> str:
        return f"https://{self.hosts['auth']}/realms/{self.realm}"

    @property
    def mcp_url(self) -> str:
        return f"https://{self.hosts['mcp']}/mcp"

    @property
    def desk(self) -> str:
        return f"https://{self.hosts['desk']}"

    @property
    def api(self) -> str:
        return f"https://{self.hosts['api']}"

    def script_env(self) -> dict[str, str]:
        """Môi trường cho script con (desk_real.mjs, security_smoke.sh, ...): cổng host
        APISIX/Keycloak cùng nguồn với harness (`deploy/.env` hoặc shell), mặc định 9080/18080."""
        ports = {k: self.env[k] for k in ("APISIX_HTTP_PORT", "KC_HTTP_PORT") if self.env.get(k)}
        return {**os.environ, **ports}

    def compose_env(self) -> dict[str, str]:
        """Môi trường cho `docker compose`: biến shell được ưu tiên hơn --env-file."""
        return {**os.environ, **STACK_OVERRIDES}


@cache
def settings() -> Settings:
    env = {
        **load_env_file(DEPLOY / ".env"),
        **{k: v for k, v in os.environ.items() if k.startswith(("XT_", "KC_", "APISIX_"))},
    }
    env.update({k: v for k, v in STACK_OVERRIDES.items() if k not in os.environ})
    sdir = Path(env.get("XT_SECRETS_DIR") or Path.home() / ".config" / "xt")
    return Settings(
        env=env,
        project=os.environ.get("XT_E2E_PROJECT", "xt-e2e"),
        gateway=("127.0.0.1", int(env.get("APISIX_HTTP_PORT") or 9080)),
        hosts={
            "desk": env.get("XT_DOMAIN_DESK", "xt.freepro.online"),
            "mcp": env.get("XT_DOMAIN_MCP", "xt-mcp.freepro.online"),
            "api": env.get("XT_DOMAIN_API", "xt-api.freepro.online"),
            "auth": env.get("XT_DOMAIN_AUTH", "xt-auth.freepro.online"),
        },
        realm=env.get("XT_REALM") or "xt",
        kc_admin=f"http://127.0.0.1:{env.get('KC_HTTP_PORT') or 18080}",
        secrets_dir=sdir,
        state_dir=sdir / "e2e",
    )


# ------------------------------------------------------------ bí mật người dùng test ----
def _password() -> str:
    # Chính sách realm: >= 12 ký tự, có hoa/thường/số, khác username/email.
    alphabet = string.ascii_letters + string.digits
    while True:
        p = "".join(secrets.choice(alphabet) for _ in range(20))
        if (
            any(c.isupper() for c in p)
            and any(c.islower() for c in p)
            and any(c.isdigit() for c in p)
        ):
            return p


def users_file() -> Path:
    return settings().state_dir / "demo-seed.json"


def load_users() -> dict[str, Any]:
    f = users_file()
    if not f.exists():
        return {}
    data: dict[str, Any] = json.loads(f.read_text(encoding="utf-8"))
    return data


def save_users(data: dict[str, Any]) -> None:
    d = settings().state_dir
    d.mkdir(parents=True, exist_ok=True)
    d.chmod(0o700)
    f = users_file()
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(f)


def ensure_secret(entry: dict[str, Any], *, totp: bool) -> dict[str, Any]:
    """Giữ nguyên bí mật đã có (idempotent), chỉ sinh phần còn thiếu."""
    entry.setdefault("password", _password())
    if totp:
        # Keycloak lưu chuỗi thô; khóa HMAC = bytes UTF-8 của chuỗi (tests/integration/keycloak.py).
        entry.setdefault("totp", secrets.token_hex(10))
    return entry
