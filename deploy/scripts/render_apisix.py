#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Sinh deploy/apisix/rendered/apisix.yaml từ apisix.yaml.tmpl + deploy/.env (hostname không hard-code).

Route riêng của máy chủ (hostname KHÔNG thuộc xT, vd một dịch vụ khác dùng chung APISIX/tunnel) đặt trong
tệp không commit `deploy/apisix/extra-routes.json` (hoặc đường dẫn `XT_APISIX_EXTRA_ROUTES`), dạng
`{"routes": [{"id", "hosts": [...], ...}]}` (JSON, chèn vào YAML dạng flow). Mỗi route phải có `hosts` tường minh và không được trùng hostname
XT_DOMAIN_* nào, nên không thể chiếm route của xT.
"""
from __future__ import annotations

import os
import re
import sys
import json
from pathlib import Path
from typing import Any

DEPLOY = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from envfile import load_env  # noqa: E402


XT_HOST_KEYS = ("XT_DOMAIN_DESK", "XT_DOMAIN_MCP", "XT_DOMAIN_API", "XT_DOMAIN_AUTH")


def extra_routes(env: dict[str, str]) -> list[dict[str, Any]]:
    explicit = env.get("XT_APISIX_EXTRA_ROUTES", "").strip()
    path = Path(explicit) if explicit else DEPLOY / "apisix" / "extra-routes.json"
    if not path.exists():
        if explicit:
            sys.exit(f"XT_APISIX_EXTRA_ROUTES trỏ tới tệp không tồn tại: {path}")
        return []
    try:
        doc = json.loads(path.read_text())
    except ValueError as exc:
        sys.exit(f"{path}: JSON không hợp lệ ({exc})")
    routes = doc.get("routes") if isinstance(doc, dict) else None
    if not isinstance(routes, list) or not routes:
        sys.exit(f"{path}: cần khóa `routes` là danh sách không rỗng")
    xt_hosts = {env[k].strip().lower() for k in XT_HOST_KEYS if env.get(k, "").strip()}
    for route in routes:
        if not isinstance(route, dict) or not isinstance(route.get("id"), str) or not re.fullmatch(r"[A-Za-z0-9_-]+", route["id"]):
            sys.exit(f"{path}: mỗi route cần `id` (chữ, số, - hoặc _)")
        hosts = route.get("hosts")
        if "host" in route or not isinstance(hosts, list) or not hosts or not all(isinstance(h, str) and h.strip() for h in hosts):
            sys.exit(f"{path}: route {route['id']} phải khai `hosts` (danh sách) tường minh")
        clash = sorted(h for h in hosts if h.strip().lower() in xt_hosts)
        if clash:
            sys.exit(f"{path}: route {route['id']} không được dùng hostname của xT: {clash}")
    return routes


def insert_routes(rendered: str, routes: list[dict[str, Any]]) -> str:
    if not routes:
        return rendered
    ids = re.findall(r"^  - id: ([A-Za-z0-9_-]+)", rendered, flags=re.M) + [r["id"] for r in routes]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        sys.exit(f"route id bị trùng: {dup}")
    head, sep, tail = rendered.rpartition("#END")
    if not sep:
        sys.exit("apisix.yaml.tmpl thiếu dòng #END")
    block = "".join(f"  - {json.dumps(r, ensure_ascii=False)}\n" for r in routes)
    return f"{head.rstrip()}\n\n  # ---------- route riêng của máy (extra-routes.json, không commit) ----------\n{block}{sep}{tail}"


def main() -> int:
    env = {**load_env(DEPLOY / ".env"), **os.environ}
    tmpl = (DEPLOY / "apisix" / "apisix.yaml.tmpl").read_text()
    missing: list[str] = []

    def sub(m: re.Match[str]) -> str:
        v = env.get(m.group(1), "").strip()
        if not v:
            missing.append(m.group(1))
        return v

    out = re.sub(r"@@([A-Z0-9_]+)@@", sub, tmpl)
    if missing:
        sys.exit(f"thiếu biến: {sorted(set(missing))} (deploy/.env)")
    extra = extra_routes(env)
    out = insert_routes(out, extra)
    dest = DEPLOY / "apisix" / "rendered" / "apisix.yaml"
    dest.parent.mkdir(exist_ok=True)
    with dest.open("w") as f:  # ghi tại chỗ: giữ inode cho bind mount
        f.write(out)
    dest.chmod(0o644)  # APISIX chạy non-root: tệp chép tay 0600 làm gateway không nạp được route (404 mọi host)
    print(f"đã sinh {dest}" + (f" (+{len(extra)} route riêng: {', '.join(r['id'] for r in extra)})" if extra else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
