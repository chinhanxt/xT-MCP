# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Lệnh quản trị (role xt_migrator, `XT_DB_MIGRATOR_URL`):

    xt db migrate
    xt tenant create --alias acme --name "Công ty A" --timezone Asia/Ho_Chi_Minh --llm-egress masked
    xt tenant bind-queue --tenant acme      (XT_DB_OWNER_URL + biến của xt worker)
    xt org import --tenant acme org.json
    xt org import --tenant acme seed/binh-minh/org.json --links links.json --base-roles employee
    xt pack install --tenant acme packages/de-xuat-phe-duyet --proposed-by <ai> --approved-by <ai>
                    [--confirm sha256:…]

`pack install` không có `--confirm` chỉ in kế hoạch (diễn tập DDL thật, rollback) và mã xác nhận;
chạy lại với đúng mã đó mới áp. Không có giá trị mặc định ngầm cho tenant, múi giờ hay người duyệt.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from xt.adapters.db.connection import AuditKey
from xt.adapters.db.integration import (
    AdminError,
    bind_queue_tenant,
    connect,
    import_org,
    installed_packs,
    register_tenant,
    tenant_of_alias,
)
from xt.adapters.db.migrations import migrate
from xt.entrypoints.composition.org_seed import is_hr_format, org_from_hr


def _dsn(env: Mapping[str, str]) -> str:
    dsn = env.get("XT_DB_MIGRATOR_URL", "").strip()
    if not dsn:
        raise AdminError("XT_DB_MIGRATOR_URL chưa đặt (DSN role xt_migrator)")
    return dsn


def _audit_key(env: Mapping[str, str]) -> AuditKey | None:
    path, kid = env.get("XT_AUDIT_KEY_FILE", "").strip(), env.get("XT_AUDIT_KEY_ID", "").strip()
    if not path and not kid:
        return None
    if not path or not kid.isdigit():
        raise AdminError("XT_AUDIT_KEY_FILE và XT_AUDIT_KEY_ID phải đặt cùng nhau")
    return AuditKey(int(kid), Path(path).read_bytes().strip())


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="xt", description="Lệnh quản trị xT")
    sub = p.add_subparsers(dest="group", required=True)
    db = sub.add_parser("db").add_subparsers(dest="cmd", required=True)
    db.add_parser("migrate", help="áp migration nền tảng còn thiếu")
    tenant = sub.add_parser("tenant").add_subparsers(dest="cmd", required=True)
    tc = tenant.add_parser("create", help="đăng ký tenant (alias = Organization của Keycloak)")
    tc.add_argument("--alias", required=True)
    tc.add_argument("--name", required=True)
    tc.add_argument("--timezone", required=True)
    tc.add_argument("--llm-egress", required=True, choices=["masked", "local_only"])
    tb = tenant.add_parser(
        "bind-queue",
        help="gắn tenant vào hàng đợi (job role, binding) và đăng ký relay hẹn giờ quy trình",
    )
    tb.add_argument("--tenant", required=True)
    org = sub.add_parser("org").add_subparsers(dest="cmd", required=True)
    oi = org.add_parser("import", help="nạp đơn vị/người/quyền agent từ tệp JSON")
    oi.add_argument("--tenant", required=True)
    oi.add_argument("file", type=Path)
    oi.add_argument(
        "--links", type=Path, help="tệp JSON {subjects: {key: sub}, agent_grants: [...]} (tệp HR)"
    )
    oi.add_argument(
        "--base-roles",
        default="",
        help="vai trò cộng cho mọi nhân sự của tệp HR, phân tách dấu phẩy (vd employee)",
    )
    oi.add_argument(
        "--mapping",
        type=Path,
        help="tệp JSON {departments: {id: mã phòng}, roles: {vai trò tệp: [vai trò gói]}} (tệp HR)",
    )
    pack = sub.add_parser("pack").add_subparsers(dest="cmd", required=True)
    pi = pack.add_parser("install", help="cài gói nghiệp vụ (in kế hoạch; --confirm để áp)")
    pi.add_argument("--tenant", required=True)
    pi.add_argument("path", type=Path)
    pi.add_argument("--proposed-by", required=True)
    pi.add_argument("--approved-by", required=True)
    pi.add_argument("--confirm", help="mã xác nhận (digest) in ra ở lần chạy xem kế hoạch")
    return p


def run(argv: Sequence[str], env: Mapping[str, str]) -> int:
    args = _parser().parse_args(argv)
    dsn = _dsn(env)
    if args.group == "db":
        with connect(dsn) as conn:
            ran = migrate(conn)
        print("đã áp: " + (", ".join(ran) if ran else "(không có migration mới)"))
        return 0
    if args.group == "tenant" and args.cmd == "bind-queue":
        return _bind_queue(args.tenant, dsn, env)
    if args.group == "tenant":
        with connect(dsn) as conn:
            ref = register_tenant(
                conn,
                alias=args.alias,
                name=args.name,
                timezone=args.timezone,
                llm_egress=args.llm_egress,
            )
        print(f"tenant {args.alias}: seq={ref.seq} id={ref.id}")
        return 0
    if args.group == "org":
        data = json.loads(args.file.read_text(encoding="utf-8"))
        if is_hr_format(data):
            links = json.loads(args.links.read_text(encoding="utf-8")) if args.links else None
            base = [r.strip() for r in args.base_roles.split(",") if r.strip()]
            mapping = json.loads(args.mapping.read_text(encoding="utf-8")) if args.mapping else None
            data = org_from_hr(data, links=links, base_roles=base, mapping=mapping)
        elif args.links or args.base_roles or args.mapping:
            raise AdminError("--links/--base-roles/--mapping chỉ dùng với tệp HR (departments/employees)")
        with connect(dsn) as conn:
            ref = tenant_of_alias(conn, args.tenant)
            counts = import_org(conn, ref, data)
        print(f"đã nạp vào {args.tenant}: {counts}")
        return 0
    from xt.entrypoints.composition.packs import install, load_pack, plan_install

    pack = load_pack(args.path)
    with connect(dsn) as conn:
        ref = tenant_of_alias(conn, args.tenant)
        installed = installed_packs(conn, ref)
    key = _audit_key(env)
    if not args.confirm:
        plan = plan_install(
            pack,
            migrator_dsn=dsn,
            tenant=ref,
            alias=args.tenant,
            installed=installed,
            audit_key=key,
        )
        print(plan.render_vi())
        if plan.problems:
            return 1
        print("\nĐã kiểm, CHƯA áp. Người duyệt xem kế hoạch rồi chạy lại với --confirm <mã>.")
        return 0
    plan = install(
        pack,
        migrator_dsn=dsn,
        tenant=ref,
        alias=args.tenant,
        installed=installed,
        audit_key=key,
        confirm=args.confirm,
        proposed_by=args.proposed_by,
        approved_by=args.approved_by,
    )
    print(f"đã cài {pack.manifest.pack} {pack.manifest.version} vào {args.tenant} ({plan.digest})")
    return 0


def _bind_queue(alias: str, dsn: str, env: Mapping[str, str]) -> int:
    import asyncio

    from xt.entrypoints.composition.worker import build_jobs

    owner = env.get("XT_DB_OWNER_URL", "").strip()
    if not owner:
        raise AdminError("XT_DB_OWNER_URL chưa đặt (role có CREATEROLE để tạo job role tenant)")
    with connect(dsn) as conn:
        ref = tenant_of_alias(conn, alias)
    with connect(owner) as conn:
        role = bind_queue_tenant(conn, ref)
    try:
        jobs = build_jobs(env)
    except (ValueError, OSError) as e:
        raise AdminError(f"cấu hình worker: {e}") from e
    asyncio.run(jobs.register_relay(ref.id))
    print(f"tenant {alias}: job role {role}, relay hẹn giờ quy trình đã đăng ký")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return run(list(sys.argv[1:] if argv is None else argv), dict(os.environ))
    except AdminError as e:
        print(f"xt: {e}", file=sys.stderr)
        return 2
