# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""E11: `xt audit checkpoint` + `xt audit verify` trên stack thật trả toàn vẹn; superuser sửa một dòng
`xt_audit.event` của tenant A -> verify báo đúng tenant/seq (mã 1); tenant B vẫn toàn vẹn."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from xte2e import compose
from xte2e.flows import A, B, REQUESTER, advance, key
from xte2e.report import Checks, Evidence

SCENARIO = "E11"
pytestmark = pytest.mark.e2e


def _audit(cmd: list[str], d: Path) -> tuple[int, str]:
    env = {
        "XT_AUDIT_CHECKPOINT_DIR": "/audit/cp",
        "XT_AUDIT_SIGNING_KEY_FILE": "/audit/sign.pem",
        "XT_AUDIT_SIGNING_KEY_ID": "e2e-1",
        "XT_AUDIT_VERIFY_KEY_FILE": "/audit/verify.pem",
    }
    flags = ["run", "--rm", "--no-deps", "-T", "--entrypoint", "xt", "-v", f"{d}:/audit"]
    for k in env:
        flags += ["-e", k]
    r = compose.dc([*flags, "xt-api", "audit", *cmd], env=env, check=False, timeout=300)
    return r.returncode, (r.stdout or "") + (r.stderr or "")[-600:]


def test_audit_tamper(ev: Evidence, world) -> None:  # type: ignore[no-untyped-def]
    c = Checks(ev)
    world.call(
        world.by_key(A, REQUESTER),
        "create_record",
        {"model_id": "advance_request", "values": advance(999_000), "idempotency_key": key("aud")},
    )
    d = Path(tempfile.mkdtemp(prefix="xt-e2e-audit-"))
    try:
        (d / "cp").mkdir()
        k = Ed25519PrivateKey.generate()
        (d / "sign.pem").write_bytes(
            k.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        (d / "verify.pem").write_bytes(
            k.public_key().public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            )
        )
        for p in [d, d / "cp"]:
            p.chmod(0o777)
        for f in ("sign.pem", "verify.pem"):
            (d / f).chmod(0o644)
        rc, out = _audit(["checkpoint", "--label", "e2e"], d)
        ev.step("xt audit checkpoint", {"rc": rc, "out": out.strip().splitlines()[-3:]})
        c.check(rc == 0, f"checkpoint ký Ed25519 ghi ngoài DB (rc={rc})")
        rc, out = _audit(["verify", "--against-checkpoint", "latest"], d)
        ev.step("verify trước khi sửa", {"rc": rc, "out": out.strip().splitlines()[-4:]})
        c.check(rc == 0 and "toàn vẹn" in out, f"verify trước khi sửa: toàn vẹn (rc={rc})")
        ta, tb = world.tenants[A]["id"], world.tenants[B]["id"]
        seq = compose.psql(f"SELECT max(seq) FROM xt_audit.event WHERE tenant_id = '{ta}'")
        c.check(bool(seq), f"tenant A có sự kiện audit (seq={seq})")
        if not seq:
            c.done()
        compose.psql(
            "BEGIN; ALTER TABLE xt_audit.event DISABLE TRIGGER USER;"
            f" UPDATE xt_audit.event SET diff = diff || '{{\"e2e_tamper\": true}}'::jsonb WHERE seq = {seq};"
            " ALTER TABLE xt_audit.event ENABLE TRIGGER USER; COMMIT;"
        )
        ev.step("superuser sửa xt_audit.event", {"tenant": "A", "seq": seq})
        rc, out = _audit(["verify", "--against-checkpoint", "latest"], d)
        lines = out.strip().splitlines()
        ev.step("verify sau khi sửa", {"rc": rc, "out": lines[-4:]})
        line_a = next((ln for ln in lines if ln.startswith(ta)), "")
        line_b = next((ln for ln in lines if ln.startswith(tb)), "")
        c.check(rc == 1, f"verify phát hiện sửa (rc={rc})")
        c.check(
            f"seq={seq}" in line_a or seq in line_a, f"báo đúng tenant A + seq {seq}: {line_a!r}"
        )
        c.check(
            "ok" in line_b.lower() or "intact" in line_b.lower() or "toàn vẹn" in line_b,
            f"tenant B vẫn toàn vẹn: {line_b!r}",
        )
        # Hoàn nguyên để stack còn dùng được cho lần chạy kế tiếp (make e2e luôn dựng volume mới).
        compose.psql(
            "BEGIN; ALTER TABLE xt_audit.event DISABLE TRIGGER USER;"
            f" UPDATE xt_audit.event SET diff = diff - 'e2e_tamper' WHERE seq = {seq};"
            " ALTER TABLE xt_audit.event ENABLE TRIGGER USER; COMMIT;"
        )
    finally:
        shutil.rmtree(d, ignore_errors=True)
    c.done()
