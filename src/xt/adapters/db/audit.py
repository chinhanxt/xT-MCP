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
import hashlib
import json
import os
from collections.abc import Mapping, Sequence
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
    "DIGEST_VERSION",
    "AnchorResult",
    "AuditReader",
    "CatalogDiff",
    "ChainStatus",
    "CheckpointError",
    "audit_hmac",
    "canonical_entry",
    "checkpoint_outdated",
    "diff_security_catalog",
    "load_checkpoint",
    "security_catalog",
    "sign_checkpoint",
    "take_checkpoint",
    "verify_against_checkpoint",
    "verify_hmac",
    "write_checkpoint",
]

CHECKPOINT_FORMAT = "xt-audit-checkpoint/1"
# Phiên bản cách tính digest hàm/catalog (trường `digest_version` được ký). v1 (không có trường):
# ACL/role của policy theo thứ tự OID, ACL NULL ≠ ACL mặc định tường minh → lệch sau pg_restore.
# v2: chuẩn hóa theo tên, sắp xếp, ACL NULL = acldefault. Checkpoint khác phiên bản không so được
# digest: báo "cũ — cần neo lại", không coi là bị sửa (chuỗi audit vẫn được kiểm).
DIGEST_VERSION = 2

# Bảng xt_meta KHÔNG vào hash-chain, kèm lý do. Mọi bảng xt_meta khác PHẢI có trigger
# xt_audit_meta (test kiểm theo catalog thật, không theo danh sách cứng của bảng có audit).
META_AUDIT_EXEMPT: dict[str, str] = {
    "platform": "cấu hình cài đặt (phiên bản), ghi bằng superuser",
    "schema_migration": "sổ migration",
    "session_ctx": "ngữ cảnh RBAC tạm theo transaction",
    "pgrst_client": "allowlist azp của PostgREST, ghi bằng superuser",
    "apply_job": "hàng đợi pha 2; nguồn sự thật là catalog_version (có audit)",
    "model_proposal": "đề xuất bất biến; duyệt/áp nằm ở model_change_request (có audit)",
    "pii_token": "vault token; mỗi lần lộ ra ghi pii_egress (có audit)",
    "pii_token_source": "ánh xạ token; lộ ra = pii_egress",
    "pii_token_seen": "ánh xạ token; lộ ra = pii_egress",
    "pii_reveal_ticket": "vé dùng một lần; lần xem ghi pii_egress",
    "workflow_timer_outbox": "sổ idempotency hẹn giờ",
    "workflow_timer_fire": "sổ idempotency hẹn giờ",
    "notification_request": "sổ gửi/idempotency",
    "notification_delivery": "sổ gửi/idempotency",
    "notification_source": "sổ gửi/idempotency",
    "telegram_link_code": "mã liên kết dùng một lần",
    "telegram_update": "chống lặp update Telegram",
    "confirmation_binding": "nonce xác nhận, hết hạn",
    "confirmation_session": "nonce xác nhận, hết hạn",
    "aggregate_explain": "metadata truy vấn dẫn xuất, hết hạn 24h; không chứa kết quả aggregate",
    "single_use_key": "nonce dùng một lần",
    "workflow_instance": "trạng thái engine; chuyển trạng thái ghi process_event (có audit)",
    "workflow_request": "trạng thái engine; chuyển trạng thái ghi process_event (có audit)",
}


class CheckpointError(Exception):
    """Checkpoint không hợp lệ: sai định dạng, sai chữ ký, sai khóa."""


@dataclass(frozen=True)
class AnchorResult:
    tenant_id: str
    seq: int
    # ok | missing (đuôi chuỗi bị xóa) | mismatch (chuỗi bị tính lại) | broken | functions_changed
    status: str
    events: int
    first_bad_seq: int | None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def _canon_json(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


AUDIT_FUNCTIONS_SQL = (
    "SELECT p.oid::regprocedure::text, pg_get_functiondef(p.oid),"
    " pg_get_userbyid(p.proowner)::text, coalesce(p.proacl, acldefault('f', p.proowner))::text[],"
    " p.proleakproof, p.proisstrict,"
    " p.proparallel, p.prosupport::regproc::text"
    " FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace"
    " WHERE (n.nspname IN ('xt', 'xt_audit', 'xt_meta')"
    # mọi hàm mà policy RLS của bảng model/metadata tham chiếu (pg_depend)
    " OR p.oid IN (SELECT d.refobjid FROM pg_depend d JOIN pg_policy pp ON pp.oid = d.objid"
    " JOIN pg_class pc ON pc.oid = pp.polrelid JOIN pg_namespace pn ON pn.oid = pc.relnamespace"
    " WHERE d.classid = 'pg_policy'::regclass AND d.refclassid = 'pg_proc'::regclass"
    " AND (pn.nspname ~ '^xt_t[0-9]+$' OR pn.nspname = 'xt_meta')))"
    " AND p.prokind <> 'a'"
    " AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid = 'pg_proc'::regclass"
    " AND d.objid = p.oid AND d.deptype = 'e')"
)


def audit_functions_digest(conn: psycopg.Connection[Any]) -> str:
    """sha256 của thân + chủ + SECURITY DEFINER của mọi hàm audit, hàm ngữ cảnh RLS (`xt.cur`…) và
    mọi hàm policy RLS tham chiếu (đọc thẳng pg_proc, không gọi hàm của audit). Checkpoint ghi lại;
    kiểm lại thấy khác → hàm đã bị thay (R3-05, quyết định 16/10 về xt.cur)."""
    rows = sorted(
        tuple(",".join(sorted(x)) if isinstance(x, list) else str(x) for x in r)
        for r in conn.execute(AUDIT_FUNCTIONS_SQL).fetchall()
    )
    h = hashlib.sha256()
    for r in rows:
        h.update("\x1f".join(r).encode() + b"\x1e")
    return h.hexdigest()


# Catalog liên quan bảo mật của các schema xt* (quyết định "phòng ngừa + phát hiện", 16/10/2026):
# mỗi dòng (loại, tên đối tượng, định nghĩa). Đọc thẳng catalog hệ thống, không gọi hàm của xT.
SECURITY_CATALOG_SQL = """
WITH ns AS (SELECT oid, nspname FROM pg_namespace
             WHERE nspname ~ '^(xt|xt_meta|xt_audit|xt_t[0-9]+|api_t[0-9]+)$'),
rel AS (SELECT c.oid, n.nspname || '.' || c.relname AS name, c.relowner, c.relrowsecurity,
               c.relforcerowsecurity, c.relacl, c.reloptions, c.relkind
          FROM pg_class c JOIN ns n ON n.oid = c.relnamespace
         WHERE c.relkind IN ('r', 'p', 'v'))
SELECT 'schema', n.nspname, pg_get_userbyid(s.nspowner)::text,
       coalesce(s.nspacl, acldefault('n', s.nspowner))::text[]
  FROM ns n JOIN pg_namespace s ON s.oid = n.oid
UNION ALL
SELECT 'function', p.oid::regprocedure::text,
       concat_ws('|', pg_get_functiondef(p.oid), pg_get_userbyid(p.proowner), p.proleakproof,
                 p.proisstrict, p.proparallel, p.prosupport::regproc::text),
       coalesce(p.proacl, acldefault('f', p.proowner))::text[]
  FROM pg_proc p JOIN ns n ON n.oid = p.pronamespace
 WHERE p.prokind <> 'a'
UNION ALL
SELECT 'table', r.name, concat_ws('|', pg_get_userbyid(r.relowner), r.relrowsecurity,
       r.relforcerowsecurity, r.reloptions::text),
       coalesce(r.relacl, acldefault('r', r.relowner))::text[] FROM rel r
UNION ALL
SELECT 'view', r.name, pg_get_viewdef(r.oid), NULL::text[] FROM rel r WHERE r.relkind = 'v'
UNION ALL
SELECT 'type', n.nspname || '.' || t.typname,
       concat_ws('|', t.typtype, format_type(t.typbasetype, t.typtypmod), t.typnotnull,
                 t.typdefault, pg_get_userbyid(t.typowner)),
       coalesce(t.typacl, acldefault('T', t.typowner))::text[]
  FROM pg_type t JOIN ns n ON n.oid = t.typnamespace WHERE t.typtype IN ('d', 'e')
UNION ALL
SELECT 'constraint', n.nspname || '.' || t.typname || '.' || k.conname,
       concat_ws('|', pg_get_constraintdef(k.oid), k.convalidated), NULL::text[]
  FROM pg_constraint k JOIN pg_type t ON t.oid = k.contypid
  JOIN ns n ON n.oid = t.typnamespace
UNION ALL
SELECT 'policy', r.name || '.' || p.polname,
       concat_ws('|', p.polpermissive, p.polcmd,
                 pg_get_expr(p.polqual, p.polrelid), pg_get_expr(p.polwithcheck, p.polrelid)),
       ARRAY(SELECT CASE WHEN x = 0 THEN 'public' ELSE pg_get_userbyid(x)::text END
               FROM unnest(p.polroles) x)
  FROM pg_policy p JOIN rel r ON r.oid = p.polrelid
UNION ALL
SELECT 'trigger', r.name || '.' || g.tgname,
       concat_ws('|', pg_get_triggerdef(g.oid), g.tgenabled), NULL::text[]
  FROM pg_trigger g JOIN rel r ON r.oid = g.tgrelid WHERE NOT g.tgisinternal
UNION ALL
SELECT 'rule', r.name || '.' || w.rulename, pg_get_ruledef(w.oid), NULL::text[]
  FROM pg_rewrite w JOIN rel r ON r.oid = w.ev_class WHERE w.rulename <> '_RETURN'
UNION ALL
SELECT 'constraint', r.name || '.' || k.conname,
       concat_ws('|', pg_get_constraintdef(k.oid), k.convalidated), NULL::text[]
  FROM pg_constraint k JOIN rel r ON r.oid = k.conrelid
UNION ALL
SELECT 'default', r.name || '.' || a.attname,
       concat_ws('|', a.attgenerated, pg_get_expr(d.adbin, d.adrelid)), NULL::text[]
  FROM pg_attrdef d JOIN rel r ON r.oid = d.adrelid
  JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum
UNION ALL
SELECT 'column', r.name || '.' || a.attname,
       concat_ws('|', format_type(a.atttypid, a.atttypmod), a.attnotnull),
       coalesce(a.attacl, '{}'::aclitem[])::text[]
  FROM pg_attribute a JOIN rel r ON r.oid = a.attrelid
 WHERE a.attnum > 0 AND NOT a.attisdropped AND (a.attname = 'tenant_id' OR a.attacl IS NOT NULL)
UNION ALL
SELECT 'index', r.name || '.' || ic.relname, pg_get_indexdef(i.indexrelid), NULL::text[]
  FROM pg_index i JOIN rel r ON r.oid = i.indrelid JOIN pg_class ic ON ic.oid = i.indexrelid
UNION ALL
SELECT 'event_trigger', e.evtname,
       concat_ws('|', e.evtevent, e.evtfoid::regprocedure::text, e.evtenabled,
                 pg_get_userbyid(e.evtowner)),
       e.evttags
  FROM pg_event_trigger e
UNION ALL
SELECT 'role', a.rolname, concat_ws('|', a.rolsuper, a.rolbypassrls, a.rolcanlogin, a.rolinherit,
       a.rolcreaterole, a.rolcreatedb, a.rolreplication), NULL::text[]
  FROM pg_roles a WHERE a.rolname ~ '^xt_'
UNION ALL
SELECT 'membership', pg_get_userbyid(m.member) || '->' || pg_get_userbyid(m.roleid),
       concat_ws('|', m.admin_option, m.inherit_option, m.set_option), NULL::text[]
  FROM pg_auth_members m
 WHERE pg_get_userbyid(m.roleid) ~ '^xt_' OR pg_get_userbyid(m.member) ~ '^xt_'
"""


def canonical_entry(definition: object, members: Sequence[str] | None) -> str:
    """Dạng chuẩn ổn định qua pg_dump/pg_restore: định nghĩa (chỉ tên, không OID) + tập phần tử
    (mục ACL `grantee=quyền/grantor` theo TÊN role, role của policy, tag event trigger) đã sắp
    xếp. Thứ tự gốc của các mảng này theo OID/thứ tự GRANT nên đổi khi khôi phục; ACL NULL đã
    được SQL thay bằng `acldefault` nên tương đương với ACL tường minh bằng mặc định."""
    text = str(definition or "")
    if members is None:
        return text
    return text + "\x1f" + "\x1e".join(sorted(set(members)))


def security_catalog(conn: psycopg.Connection[Any]) -> dict[str, str]:
    """{"<loại>:<tên>": sha256(định nghĩa)} cho toàn bộ catalog liên quan bảo mật của xt*."""
    out: dict[str, str] = {}
    for kind, name, definition, members in conn.execute(SECURITY_CATALOG_SQL).fetchall():
        canon = canonical_entry(definition, members)
        out[f"{kind}:{name}"] = hashlib.sha256(canon.encode()).hexdigest()
    return out


def checkpoint_outdated(doc: Mapping[str, Any]) -> bool:
    """Checkpoint ký với cách tính digest cũ: digest hàm/catalog không so được với hiện tại."""
    return bool(doc.get("digest_version", 1) != DIGEST_VERSION)


@dataclass(frozen=True)
class CatalogDiff:
    changed: list[str]
    added: list[str]
    removed: list[str]

    @property
    def ok(self) -> bool:
        return not (self.changed or self.added or self.removed)


def diff_security_catalog(conn: psycopg.Connection[Any], doc: Mapping[str, Any]) -> CatalogDiff:
    """So catalog hiện tại với checkpoint: báo ĐÚNG đối tượng nào đổi / thêm / mất."""
    then: Mapping[str, str] = doc.get("security_catalog") or {}
    now = security_catalog(conn)
    return CatalogDiff(
        changed=sorted(k for k in then.keys() & now.keys() if then[k] != now[k]),
        added=sorted(now.keys() - then.keys()),
        removed=sorted(then.keys() - now.keys()),
    )


def take_checkpoint(conn: psycopg.Connection[Any], *, label: str = "") -> dict[str, Any]:
    """Đầu chuỗi audit của mọi tenant + digest các hàm audit (kết nối role xt_app)."""
    rows = conn.execute("SELECT tenant_id, seq, hash FROM xt_audit.heads()").fetchall()
    return {
        "format": CHECKPOINT_FORMAT,
        "digest_version": DIGEST_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "label": label,
        "audit_functions": audit_functions_digest(conn),
        "security_catalog": security_catalog(conn),
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


ZERO_HASH = bytes(32)


def _canon(tenant: str, row: Any) -> bytes:
    """Bản Python của `xt_audit.canon` (phải khớp từng byte)."""
    _seq, actor, eff, azp, trace, at, model, rid, op, diff, _prev, _hash = row
    stamp = at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")
    parts = [
        tenant,
        str(actor) if actor else "-",
        str(eff) if eff else "-",
        azp if azp is not None else "-",
        trace if trace is not None else "-",
        stamp,
        model,
        str(rid),
        op,
        diff,
    ]
    return "|".join(parts).encode("utf-8")


def recompute_chain(rows: Sequence[Any], tenant: str) -> tuple[dict[int, bytes], int | None]:
    """Tính lại chuỗi SHA-256 ở client từ dòng thô. Trả (seq → hash đã tính, seq hỏng đầu tiên)."""
    prev = ZERO_HASH
    out: dict[int, bytes] = {}
    for row in rows:
        seq, stored_prev, stored = int(row[0]), bytes(row[10]), bytes(row[11])
        h = hashlib.sha256(prev + _canon(tenant, row)).digest()
        if stored_prev != prev or stored != h:
            return out, seq
        out[seq] = h
        prev = h
    return out, None


def verify_against_checkpoint(
    conn: psycopg.Connection[Any], doc: Mapping[str, Any]
) -> list[AnchorResult]:
    """Kiểm ở CLIENT (không tin hàm kiểm trong DB, R3-05): đọc dòng thô của chuỗi, tự tính lại
    SHA-256 theo `canon`, rồi đối chiếu đầu chuỗi đã ký. Digest hàm audit khác checkpoint →
    `functions_changed` cho mọi tenant."""
    changed = (
        bool(doc.get("audit_functions"))
        and not checkpoint_outdated(doc)
        and doc["audit_functions"] != audit_functions_digest(conn)
    )
    out: list[AnchorResult] = []
    for h in doc["heads"]:
        tenant, seq, want = h["tenant_id"], int(h["seq"]), bytes.fromhex(h["hash"])
        rows = conn.execute(
            "SELECT seq, actor_id, effective_actor, client_azp, trace_id, at, model, record_id, op,"
            " diff, prev_hash, hash FROM xt_audit.chain(%s)",
            (UUID(tenant),),
        ).fetchall()
        hashes, bad = recompute_chain(rows, tenant)
        if changed:
            status = "functions_changed"
        elif seq == 0:
            status = "broken" if bad is not None else "ok"
        elif bad is not None and bad <= seq:
            status = "broken"
        elif seq not in hashes:
            status = "missing" if all(int(r[0]) != seq for r in rows) else "broken"
        elif hashes[seq] != want:
            status = "mismatch"
        elif bad is not None:
            status = "broken"
        else:
            status = "ok"
        out.append(AnchorResult(tenant, seq, status, len(rows), bad))
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
