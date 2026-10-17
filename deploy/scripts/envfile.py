# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Đọc/ghi deploy/.env và sinh bí mật còn trống (stdlib)."""
from __future__ import annotations

import json
import re
import secrets
import subprocess
import os
import shutil
from pathlib import Path

SECRET_KEYS = (
    "POSTGRES_PASSWORD",
    "KC_BOOTSTRAP_ADMIN_PASSWORD",
    "XT_IDP_INTROSPECTION_CLIENT_SECRET",
    "XT_IDP_ADMIN_CLIENT_SECRET",
    "XT_PGRST_PASSWORD",
    "XT_DB_APP_PASSWORD",
    "XT_DB_MIGRATOR_PASSWORD",
    "XT_DB_CONTROL_PASSWORD",
    "OPENBAO_DEV_TOKEN",
    "XT_FILES_S3_ACCESS_KEY",
    "XT_FILES_S3_SECRET_KEY",
    "XT_SEAWEED_JWT_WRITE_KEY",
    "XT_SEAWEED_JWT_READ_KEY",
    "XT_SEAWEED_FILER_JWT_WRITE_KEY",
    "XT_SEAWEED_FILER_JWT_READ_KEY",
)
_LINE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")


def _value(raw: str) -> str:
    return raw.split(" #", 1)[0].strip().strip("'\"") if not raw.startswith(("'", '"')) else raw.strip().strip("'\"")


def load_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            m = _LINE.match(line)
            if m:
                out[m.group(1)] = _value(m.group(2))
    return out


def ensure_env(deploy: Path) -> dict[str, str]:
    """Tạo .env từ .env.example nếu chưa có; điền bí mật trống. Không bao giờ ghi đè giá trị đã có."""
    env_path = deploy / ".env"
    if not env_path.exists():
        shutil.copyfile(deploy / ".env.example", env_path)
        env_path.chmod(0o600)
    lines = env_path.read_text().splitlines()
    changed = False
    for i, line in enumerate(lines):
        m = _LINE.match(line)
        if m and m.group(1) in SECRET_KEYS and not _value(m.group(2)):
            lines[i] = f"{m.group(1)}={secrets.token_urlsafe(24)}"
            changed = True
            print(f"đã sinh {m.group(1)} vào {env_path}")
    present = {m.group(1) for m in map(_LINE.match, lines) if m}
    for key in SECRET_KEYS:
        if key not in present:  # .env cũ thiếu khóa mới thêm vào SECRET_KEYS
            lines.append(f"{key}={secrets.token_urlsafe(24)}")
            changed = True
            print(f"đã sinh {key} vào {env_path}")
    example = {m.group(1): m.group(2) for m in map(_LINE.match, (deploy / ".env.example").read_text().splitlines()) if m}
    for key, raw in example.items():  # .env cũ thiếu biến mới của .env.example: thêm (không ghi đè)
        if key not in present and key not in SECRET_KEYS:
            lines.append(f"{key}={_value(raw)}")
            changed = True
            print(f"đã thêm {key} (mặc định từ .env.example) vào {env_path}")
    if changed:
        env_path.write_text("\n".join(lines) + "\n")
    ensure_app_secrets(load_env(env_path))
    ensure_seaweed_configs(load_env(env_path))
    return load_env(env_path)


def secrets_dir(env: dict[str, str]) -> Path:
    return Path(env.get("XT_SECRETS_DIR") or Path.home() / ".config" / "xt")


def private_secrets_dir(env: dict[str, str]) -> Path:
    root = secrets_dir(env)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.stat().st_mode & 0o777 != 0o700:
        # Never chmod an existing shared directory chosen by mistake (e.g. /tmp).
        raise SystemExit("XT_SECRETS_DIR must be a dedicated private directory with mode 0700")
    return root


def ensure_app_secrets(env: dict[str, str]) -> Path:
    """Khóa tệp của xt-api/xt-worker ở `<XT_SECRETS_DIR>/app` (ngoài repo): audit/cursor/pii (hex 32 byte) và
    khóa ký biên nhận Ed25519 (PKCS#8 PEM, openssl). Không bao giờ ghi đè. Container chạy uid 10001 nên
    thư mục 0755, tệp 0644 (thư mục cha vẫn 0700 nên người dùng khác trên host không đọc được)."""
    d = private_secrets_dir(env) / "app"
    d.mkdir(parents=True, exist_ok=True)
    d.chmod(0o755)
    for name in ("audit.key", "cursor.key", "pii.key"):
        f = d / name
        if not f.exists():
            f.write_text(secrets.token_hex(32) + "\n")
            print(f"đã sinh {f}")
        f.chmod(0o644)
    pem = d / "receipt-ed25519.pem"
    if not pem.exists():
        subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(pem)], check=True)
        print(f"đã sinh {pem}")
    pem.chmod(0o644)
    return d


SEAWEED_SECURITY_TOML = """\
# R2-F4: master/volume/filer chỉ nhận lệnh quản trị (/dir/assign, /col/delete, /vol/grow…)
# từ loopback = gateway S3 trong cùng tiến trình `weed server`; peer trong mạng `files` bị 401.
[guard]
white_list = "127.0.0.1,@"   # @ = gRPC nội tiến trình (S3/filer -> master/volume)
[jwt.signing]
key = "{XT_SEAWEED_JWT_WRITE_KEY}"
expires_after_seconds = 10
[jwt.signing.read]
key = "{XT_SEAWEED_JWT_READ_KEY}"
expires_after_seconds = 10
[jwt.filer_signing]
key = "{XT_SEAWEED_FILER_JWT_WRITE_KEY}"
expires_after_seconds = 10
[jwt.filer_signing.read]
key = "{XT_SEAWEED_FILER_JWT_READ_KEY}"
expires_after_seconds = 10
"""


def ensure_seaweed_configs(env: dict[str, str]) -> Path:
    """Render `s3.json` + `security.toml` của SeaweedFS (kèm bí mật từ .env) ra `<XT_SECRETS_DIR>/seaweed`
    (ngoài repo). compose mount bằng `configs.<name>.file` vì Docker Compose không nạp `configs.content` vào
    service read_only. Ghi lại mỗi lần (theo .env hiện tại). Container chạy uid 1000 nên thư mục 0755, tệp 0644
    (thư mục cha vẫn 0700 nên người dùng khác trên host không đọc được), cùng mẫu với ensure_app_secrets."""
    keys = ("XT_FILES_S3_ACCESS_KEY", "XT_FILES_S3_SECRET_KEY", *(k for k in SECRET_KEYS if k.startswith("XT_SEAWEED_")))
    missing = [k for k in keys if not env.get(k)]
    if missing:
        raise SystemExit(f"thiếu {', '.join(missing)} trong .env (chạy make env)")
    d = private_secrets_dir(env) / "seaweed"
    d.mkdir(parents=True, exist_ok=True)
    d.chmod(0o755)
    s3 = {"identities": [{"name": "xt-api",
                          "credentials": [{"accessKey": env["XT_FILES_S3_ACCESS_KEY"], "secretKey": env["XT_FILES_S3_SECRET_KEY"]}],
                          "actions": ["Admin", "Read", "Write", "List", "Tagging"]}]}
    for name, text in (("s3.json", json.dumps(s3) + "\n"), ("security.toml", SEAWEED_SECURITY_TOML.format(**env))):
        f = d / name
        f.write_text(text)
        f.chmod(0o644)
    return d
