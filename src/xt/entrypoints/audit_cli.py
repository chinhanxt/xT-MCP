# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Neo chuỗi audit ra ngoài Postgres (checkpoint ký Ed25519) và kiểm lại:

    xt audit checkpoint                       # ghi checkpoint mới vào XT_AUDIT_CHECKPOINT_DIR
    xt audit verify --against-checkpoint latest|<tệp>

Biến môi trường (không có giá trị mặc định ngầm):
    XT_DB_APP_URL                 DSN role xt_app (chỉ cần EXECUTE xt_audit.heads/verify_anchor)
    XT_AUDIT_CHECKPOINT_DIR       thư mục NGOÀI Postgres (ổ gắn kho đối tượng/WORM)
    XT_AUDIT_SIGNING_KEY_FILE     khóa riêng Ed25519 (PEM PKCS#8 hoặc 32 byte), cho `checkpoint`
    XT_AUDIT_SIGNING_KEY_ID       mã khóa ghi vào checkpoint
    XT_AUDIT_VERIFY_KEY_FILE      khóa công khai Ed25519 (PEM hoặc 32 byte) — cho `verify`

Mã thoát: 0 toàn vẹn, 1 phát hiện sửa/xóa audit, 2 lỗi cấu hình/checkpoint.
Worker gọi `checkpoint` định kỳ (ví dụ mỗi giờ); người kiểm chạy `verify` bằng khóa công khai.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from xt.adapters.db.audit import (
    CheckpointError,
    connect,
    latest_checkpoint,
    load_checkpoint,
    load_ed25519_private,
    load_ed25519_public,
    sign_checkpoint,
    take_checkpoint,
    verify_against_checkpoint,
    write_checkpoint,
)


class ConfigError(Exception):
    pass


def _need(env: Mapping[str, str], name: str) -> str:
    v = env.get(name, "").strip()
    if not v:
        raise ConfigError(f"{name} chưa đặt")
    return v


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="xt audit", description="Neo và kiểm chuỗi audit")
    sub = p.add_subparsers(dest="cmd", required=True)
    cp = sub.add_parser("checkpoint", help="ký và ghi đầu chuỗi audit mọi tenant ra ngoài DB")
    cp.add_argument("--label", default="", help="nhãn tùy ý ghi vào checkpoint")
    v = sub.add_parser("verify", help="đối chiếu DB với một checkpoint đã ký")
    v.add_argument("--against-checkpoint", required=True, help="'latest' hoặc đường dẫn tệp")
    return p


def run(argv: Sequence[str], env: Mapping[str, str]) -> int:
    args = _parser().parse_args(list(argv))
    dsn = _need(env, "XT_DB_APP_URL")
    folder = Path(_need(env, "XT_AUDIT_CHECKPOINT_DIR"))
    if args.cmd == "checkpoint":
        key = load_ed25519_private(Path(_need(env, "XT_AUDIT_SIGNING_KEY_FILE")).read_bytes())
        key_id = _need(env, "XT_AUDIT_SIGNING_KEY_ID")
        with connect(dsn) as conn:
            doc = sign_checkpoint(take_checkpoint(conn, label=args.label), key, key_id)
        path = write_checkpoint(doc, folder)
        print(f"đã ghi {path} ({len(doc['heads'])} tenant)")
        return 0
    pub = load_ed25519_public(Path(_need(env, "XT_AUDIT_VERIFY_KEY_FILE")).read_bytes())
    ref = args.against_checkpoint
    path = latest_checkpoint(folder) if ref == "latest" else Path(ref)
    doc = load_checkpoint(path, pub)
    with connect(dsn) as conn:
        results = verify_against_checkpoint(conn, doc)
    bad = [r for r in results if not r.ok]
    for r in results:
        print(f"{r.tenant_id} seq={r.seq} {r.status} events={r.events}")
    print(f"checkpoint {path.name}: {len(results) - len(bad)}/{len(results)} tenant toàn vẹn")
    return 1 if bad else 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return run(sys.argv[1:] if argv is None else argv, dict(os.environ))
    except (ConfigError, CheckpointError, OSError) as e:
        print(f"xt audit: {e}", file=sys.stderr)
        return 2
