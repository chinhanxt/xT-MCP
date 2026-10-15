# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Audit hash-chain: kiểm chứng chuỗi theo tenant, đọc event, kiểm HMAC của field sensitive.

`verify` trong DB chỉ PHÁT HIỆN sửa đổi ở GIỮA chuỗi. Xóa đuôi chuỗi hoặc tính lại toàn bộ hash
(DBA/superuser) bị phát hiện nhờ checkpoint neo NGOÀI Postgres: đầu chuỗi mọi tenant được ký
Ed25519 định kỳ, lưu vào thư mục/kho đối tượng cấu hình riêng (`write_checkpoint`), rồi
`verify_against_checkpoint` đối chiếu (`xt audit verify --against-checkpoint`).
"""

from __future__ import annotations

import base64
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from xt.adapters.db.connection import Database
from xt.adapters.db.records import translate
from xt.domain.model.audit import audit_hmac, verify_hmac
from xt.domain.model.context import ModelActor


@dataclass(frozen=True)
class ChainStatus:
    events: int
    first_bad_seq: int | None

    @property
    def intact(self) -> bool:
        return self.first_bad_seq is None


class AuditReader:
    def __init__(self, db: Database) -> None:
        self.db = db

    def verify(self, actor: ModelActor) -> ChainStatus:
        try:
            with self.db.tenant_tx(actor) as cur:
                row = cur.execute(
                    "SELECT n, first_bad_seq FROM xt_audit.verify(xt.tenant())"
                ).fetchone()
        except psycopg.Error as e:
            raise translate(e) from e
        assert row is not None
        bad = row["first_bad_seq"]
        return ChainStatus(int(row["n"]), int(bad) if bad is not None else None)

    def events(
        self, actor: ModelActor, record_id: Any | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        try:
            with self.db.tenant_tx(actor) as cur:
                rows = cur.execute(
                    "SELECT seq, actor_id, effective_actor, client_azp, trace_id, at, model,"
                    " record_id,"
                    " op, diff FROM xt_audit.event WHERE (%s::uuid IS NULL OR record_id = %s)"
                    " ORDER BY seq LIMIT %s",
                    (record_id, record_id, limit),
                ).fetchall()
        except psycopg.Error as e:
            raise translate(e) from e
        return [dict(r) for r in rows]


__all__ = [
    "AnchorResult",
    "AuditReader",
    "ChainStatus",
    "CheckpointError",
    "audit_hmac",
    "load_checkpoint",
    "sign_checkpoint",
    "take_checkpoint",
    "verify_against_checkpoint",
    "verify_hmac",
    "write_checkpoint",
]

CHECKPOINT_FORMAT = "xt-audit-checkpoint/1"


class CheckpointError(Exception):
    """Checkpoint không hợp lệ: sai định dạng, sai chữ ký, sai khóa."""


@dataclass(frozen=True)
class AnchorResult:
    tenant_id: str
    seq: int
    status: str  # ok | missing (đuôi chuỗi bị xóa) | mismatch (chuỗi bị tính lại) | broken
    events: int
    first_bad_seq: int | None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def _canon_json(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def take_checkpoint(conn: psycopg.Connection[Any], *, label: str = "") -> dict[str, Any]:
    """Đầu chuỗi audit của mọi tenant (kết nối role xt_app)."""
    rows = conn.execute("SELECT tenant_id, seq, hash FROM xt_audit.heads()").fetchall()
    return {
        "format": CHECKPOINT_FORMAT,
        "created_at": datetime.now(UTC).isoformat(),
        "label": label,
        "heads": [{"tenant_id": str(t), "seq": int(s), "hash": bytes(h).hex()} for t, s, h in rows],
    }


def sign_checkpoint(
    payload: Mapping[str, Any], key: Ed25519PrivateKey, key_id: str
) -> dict[str, Any]:
    body = {**payload, "key_id": key_id}
    sig = key.sign(_canon_json(body))
    return {**body, "signature": base64.b64encode(sig).decode()}


def write_checkpoint(doc: Mapping[str, Any], directory: Path) -> Path:
    """Ghi checkpoint ra NGOÀI Postgres (thư mục cấu hình, ví dụ ổ gắn kho đối tượng WORM).
    Không bao giờ ghi đè (O_EXCL); ghi tạm rồi đổi tên nguyên tử."""
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    final = directory / f"audit-checkpoint-{stamp}.json"
    tmp = directory / f".{final.name}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, sort_keys=True, indent=1)
        f.flush()
        os.fsync(f.fileno())
    if final.exists():
        tmp.unlink()
        raise CheckpointError(f"checkpoint {final} đã tồn tại")
    os.replace(tmp, final)
    return final


def latest_checkpoint(directory: Path) -> Path:
    files = sorted(directory.glob("audit-checkpoint-*.json"))
    if not files:
        raise CheckpointError(f"không có checkpoint trong {directory}")
    return files[-1]


def load_checkpoint(path: Path, public_key: Ed25519PublicKey) -> dict[str, Any]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        sig = base64.b64decode(doc.pop("signature"), validate=True)
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise CheckpointError(f"checkpoint {path} sai định dạng: {e}") from e
    if not isinstance(doc, dict) or doc.get("format") != CHECKPOINT_FORMAT:
        raise CheckpointError("checkpoint không đúng định dạng xt-audit-checkpoint/1")
    try:
        public_key.verify(sig, _canon_json(doc))
    except InvalidSignature as e:
        raise CheckpointError("chữ ký checkpoint không hợp lệ (bị sửa hoặc sai khóa)") from e
    return doc


def verify_against_checkpoint(
    conn: psycopg.Connection[Any], doc: Mapping[str, Any]
) -> list[AnchorResult]:
    """Mọi đầu chuỗi trong checkpoint phải còn nguyên trong DB và chuỗi từ đó vẫn toàn vẹn."""
    out: list[AnchorResult] = []
    for h in doc["heads"]:
        row = conn.execute(
            "SELECT status, events, first_bad_seq FROM xt_audit.verify_anchor(%s, %s, %s)",
            (UUID(h["tenant_id"]), int(h["seq"]), bytes.fromhex(h["hash"])),
        ).fetchone()
        assert row is not None
        out.append(
            AnchorResult(
                h["tenant_id"],
                int(h["seq"]),
                str(row[0]),
                int(row[1]),
                int(row[2]) if row[2] is not None else None,
            )
        )
    return out


def load_ed25519_private(data: bytes) -> Ed25519PrivateKey:
    """PEM PKCS#8 hoặc 32 byte thô."""
    data = data if len(data) == 32 else data.strip()
    if len(data) == 32:
        return Ed25519PrivateKey.from_private_bytes(data)
    key = serialization.load_pem_private_key(data, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise CheckpointError("khóa ký checkpoint phải là Ed25519")
    return key


def load_ed25519_public(data: bytes) -> Ed25519PublicKey:
    data = data if len(data) == 32 else data.strip()
    if len(data) == 32:
        return Ed25519PublicKey.from_public_bytes(data)
    key = serialization.load_pem_public_key(data)
    if not isinstance(key, Ed25519PublicKey):
        raise CheckpointError("khóa kiểm checkpoint phải là Ed25519")
    return key


def connect(dsn: str) -> psycopg.Connection[Any]:
    """Kết nối autocommit cho công cụ neo/kiểm audit (role xt_app)."""
    return psycopg.connect(dsn, autocommit=True)


def lock_audit_chain(cur: Any, tenant_id: UUID) -> None:
    """Khóa đầu chuỗi audit của tenant (đúng khóa advisory mà `xt_audit.append` lấy ở lần ghi có
    audit đầu tiên, giữ tới commit). Gọi TRƯỚC mọi khóa dòng dễ tranh chấp (instance workflow,
    tool_operation) để mọi transaction có cùng thứ tự khóa: chuỗi audit -> dòng. Không có bước này,
    một transaction đã ghi audit rồi chờ dòng X, trong khi transaction khác giữ X rồi chờ chuỗi
    audit -> deadlock (WA-11: xác nhận quyết định song song với rút đề nghị)."""
    cur.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended('xt_audit:' || %s::text, 0))",
        (str(tenant_id),),
    )
