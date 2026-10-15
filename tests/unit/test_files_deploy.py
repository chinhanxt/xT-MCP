# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Kiểm offline (không chạy container) cấu hình triển khai SeaweedFS (FB-03, docs/audit-files.md):
mạng nội bộ riêng, không publish cổng, JWT volume/filer, không route gateway nào tới SeaweedFS.

Xác minh trên stack thật: `docker compose exec xt-api python -c "import urllib.request as u;
u.urlopen('http://seaweedfs:8888/')"` phải ra 401; từ container khác (vd postgrest/keycloak)
tên `seaweedfs` không phân giải được (không chung mạng)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
SECRETS = (
    "XT_FILES_S3_ACCESS_KEY",
    "XT_FILES_S3_SECRET_KEY",
    "XT_SEAWEED_JWT_WRITE_KEY",
    "XT_SEAWEED_JWT_READ_KEY",
    "XT_SEAWEED_FILER_JWT_WRITE_KEY",
    "XT_SEAWEED_FILER_JWT_READ_KEY",
)


@pytest.fixture(scope="module")
def compose(tmp_path_factory: pytest.TempPathFactory) -> dict:  # type: ignore[type-arg]
    if shutil.which("docker") is None:
        pytest.skip("không có docker CLI (docker compose config)")
    env = tmp_path_factory.mktemp("env") / ".env"
    lines = []
    for line in (DEPLOY / ".env.example").read_text(encoding="utf-8").splitlines():
        key = line.split("=", 1)[0]
        if (
            "=" in line
            and not line.startswith("#")
            and (key in SECRETS or key.endswith(("PASSWORD", "SECRET")))
        ):
            line = f"{key}=test-{key.lower()}-0123456789"
        lines.append(line)
    env.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out = subprocess.run(
        ["docker", "compose", "--env-file", str(env), "-f", str(DEPLOY / "compose.yaml"),
         "--profile", "minimal", "--profile", "full", "config", "--format", "json"],
        capture_output=True, text=True, check=False, timeout=60,
    )  # fmt: skip
    if out.returncode != 0 and "unknown flag" in out.stderr:
        pytest.skip("docker compose quá cũ")
    assert out.returncode == 0, out.stderr[-500:]
    return dict(json.loads(out.stdout))


def test_seaweed_only_on_internal_files_network(compose: dict) -> None:  # type: ignore[type-arg]
    svc = compose["services"]
    assert compose["networks"]["files"].get("internal") is True
    assert set(svc["seaweedfs"]["networks"]) == {"files"}
    assert not svc["seaweedfs"].get("ports")
    on_files = {n for n, s in svc.items() if "files" in (s.get("networks") or {})}
    assert on_files <= {"seaweedfs", "xt-api", "xt-worker", "clamav"}, on_files
    for name in ("keycloak", "apisix", "postgrest", "xt-desk", "postgres"):
        assert "files" not in (svc[name].get("networks") or {}), name


def test_seaweed_hardened_and_jwt_enabled(compose: dict) -> None:  # type: ignore[type-arg]
    sw = compose["services"]["seaweedfs"]
    assert sw["user"] == "1000:1000" and sw["read_only"] is True
    assert sw["cap_drop"] == ["ALL"]
    assert "@sha256:" in sw["image"]
    targets = {c["target"] for c in sw["configs"]}
    assert {"/etc/seaweedfs/security.toml", "/etc/xt-s3/s3.json"} <= targets
    sec = compose["configs"]["seaweed-security"]["content"]
    for section in ("jwt.signing", "jwt.signing.read", "jwt.filer_signing",
                    "jwt.filer_signing.read"):  # fmt: skip
        assert f"[{section}]" in sec
    assert 'key = ""' not in sec
    s3 = json.loads(compose["configs"]["seaweed-s3"]["content"])
    assert s3["identities"][0]["credentials"][0]["secretKey"]


def test_no_gateway_route_to_seaweed() -> None:
    tmpl = (DEPLOY / "apisix" / "apisix.yaml.tmpl").read_text(encoding="utf-8")
    assert "seaweedfs" not in tmpl and ":8333" not in tmpl and ":8888" not in tmpl


def test_env_generator_knows_seaweed_secrets() -> None:
    gen = (DEPLOY / "scripts" / "envfile.py").read_text(encoding="utf-8")
    example = (DEPLOY / ".env.example").read_text(encoding="utf-8")
    for key in SECRETS:
        assert f'"{key}"' in gen and f"{key}=" in example, key
