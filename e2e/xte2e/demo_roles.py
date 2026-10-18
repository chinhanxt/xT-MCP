# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Gán vai trò gói cho người demo qua ĐÚNG luồng sản phẩm, không sửa bảng.

    python -m xte2e.demo_roles PLAN.json            # xem kế hoạch, không ghi gì
    python -m xte2e.demo_roles PLAN.json --apply    # chạy

Luồng (gói nhan-su-vong-doi >= 0.4.2, quy trình employee_onboarding v7), mỗi người một hồ sơ:
1. Người vận hành (HR, IT tạo quyền, IT duyệt) chưa có đăng nhập thì được tạo user Keycloak
   (mật khẩu + TOTP, bí mật lưu vào demo-seed.json 0600) và nối vào xT bằng
   `xt org import --links` (subject + agent grant L1), giữ nguyên subject của người đã có.
2. HR tạo `department`/`employee` còn thiếu, tạo `lifecycle_case` (onboarding), `start_process`.
3. IT (người tạo) tạo `access_grant` (system keycloak) cho từng vai trò.
4. Bước human-only hoàn thành bằng `transition_record`: dừng ở confirmation_required, người được
   giao xác nhận trên /desk/confirm (mật khẩu + TOTP), rồi gọi lại cùng idempotency_key.
   Bước IT do người duyệt IT (khác HR, khác người tạo quyền, khác người được cấp) hoàn thành,
   op `sync_access_grants` áp quyền.
5. Kế toán và quản lý trực tiếp hoàn thành bước của mình; quy trình kích hoạt nhân viên và
   đóng hồ sơ.

Kế hoạch mẫu: e2e/demo-roles.binhminh.json.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import uuid
from pathlib import Path
from typing import Any

from xte2e import mcpc, oidc
from xte2e.config import load_users, save_users, settings
from xte2e.kcadmin import Admin
from xte2e.seed import AGENT_CLIENT, TENANTS, import_org, links_for, seed_users

STEPS = ("hr", "it", "accounting", "manager")


def log(msg: str) -> None:
    print(f"[demo-roles] {msg}", flush=True)


class Operator:
    def __init__(self, state: dict[str, Any], tenant: str) -> None:
        self.state, self.tenant, self.s = state, tenant, settings()
        self.sessions: dict[str, oidc.Session] = {}

    def user(self, key: str) -> dict[str, Any]:
        for u in self.state["users"].values():
            if u.get("tenant") == self.tenant and u.get("key") == key:
                return u
        raise KeyError(f"{self.tenant}/{key} chưa có đăng nhập")

    def call(self, key: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        if key not in self.sessions:
            self.sessions[key] = oidc.Session(
                self.user(key), AGENT_CLIENT, f"{self.s.desk}/auth/callback", "openid mcp-xt"
            )
        sess = self.sessions[key]
        return mcpc.call(sess.access(), tool, args, key=sess.dpop)

    def ok(self, key: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        r = self.call(key, tool, args)
        if not r.get("ok"):
            raise RuntimeError(
                f"{key} {tool}: {json.dumps(r.get('error'), ensure_ascii=False)[:600]}"
            )
        return r["data"]

    def confirmed(self, key: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        """Human-only: confirmation_required -> người được giao xác nhận -> gọi lại cùng key."""
        r = self.call(key, tool, args)
        code = (r.get("error") or {}).get("code")
        if code == "confirmation_required":
            url = r["error"]["details"]["confirm_url"]
            res = self.confirm(url, self.user(key))
            if (
                res.get("stage") != "decided"
                or res.get("status") != 200
                or "Đã thực hiện" not in res.get("body", "")
            ):
                raise RuntimeError(
                    f"{key} xác nhận thất bại: {res.get('stage')} {res.get('body', '')[:300]}"
                )
            r = self.call(key, tool, args)
        if not r.get("ok"):
            raise RuntimeError(
                f"{key} {tool}: {json.dumps(r.get('error'), ensure_ascii=False)[:600]}"
            )
        return r["data"]

    def confirm(self, url: str, approver: dict[str, Any]) -> dict[str, Any]:
        b = oidc.Browser()
        cb = f"{self.s.desk}/desk/confirm/callback"
        try:
            step = b.get(url, stop=cb)
            if (
                step.kind == "page"
                and "kc-form-login" not in step.page
                and "kc-otp-login-form" not in step.page
            ):
                return {"stage": "start", "status": step.status}
            step = b.login_pages(
                step,
                approver["username"],
                approver["password"],
                approver.get("totp"),
                stop=cb,
                consent=False,
            )
            r = b.c.get(step.url)
            forms = [f for f in oidc.forms(r.text) if "/desk/confirm" in f["action"]]
            if r.status_code != 200 or not forms:
                return {"stage": "callback", "status": r.status_code, "body": r.text[:300]}
            f = forms[0]
            action = (
                f["action"] if f["action"].startswith("http") else f"{self.s.desk}{f['action']}"
            )
            r2 = b.c.post(action, data={**f["inputs"], "decision": "approve"})
            text = re.sub(
                r"\s+",
                " ",
                re.sub(
                    r"<[^>]+>",
                    " ",
                    re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", r2.text, flags=re.S),
                ),
            )
            return {"stage": "decided", "status": r2.status_code, "body": text.strip()[:600]}
        finally:
            b.close()


def org_people(tenant: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        e["id"]: e for e in json.loads(Path(tenant["org"]).read_text(encoding="utf-8"))["employees"]
    }


def ensure_operators(
    state: dict[str, Any], tenant: dict[str, Any], keys: list[str], apply: bool
) -> None:
    have = {u.get("key") for u in state["users"].values() if u.get("tenant") == tenant["alias"]}
    missing = [k for k in dict.fromkeys(keys) if k not in have]
    if not missing:
        return
    log(f"cần tạo đăng nhập cho: {missing}")
    if not apply:
        return
    kc = Admin()
    kc.wait_ready()
    t_state = state["tenants"][tenant["alias"]]
    hr = json.loads(Path(tenant["org"]).read_text(encoding="utf-8"))
    new = seed_users(kc, {**tenant, "people": missing}, hr, state, t_state["org_id"])
    links = links_for({**t_state["links"]["subjects"], **new})
    log("xt org import --links (giữ subject cũ, thêm người vận hành)")
    log(
        import_org(tenant["alias"], Path(tenant["org"]), links, force=True).strip().splitlines()[-1]
    )
    t_state["links"] = links
    save_users(state)


def one(
    op: Operator, actor: str, model: str, where: dict[str, Any], fields: list[str] | None = None
) -> dict[str, Any] | None:
    args: dict[str, Any] = {"model_id": model, "where": where, "limit": 5}
    if fields:
        args["fields"] = fields
    rows = op.ok(actor, "search_records", args)["items"]
    return rows[0] if rows else None


def idem(*parts: str) -> str:
    return "demo-roles-" + uuid.uuid5(uuid.NAMESPACE_URL, "/".join(parts)).hex[:24]


def person_ref(op: Operator, actor: str, name: str) -> str:
    people = op.ok(actor, "find_people", {"query": name, "limit": 5})["people"]
    hit = [p for p in people if p.get("name") == name]
    if len(hit) != 1:
        raise RuntimeError(f"không xác định duy nhất được người '{name}' ({len(hit)})")
    return str(hit[0]["ref"])


def run(plan: dict[str, Any], apply: bool, attempt: int = 0) -> dict[str, Any]:
    alias = plan["tenant"]
    tenant = next(t for t in TENANTS if t["alias"] == alias)
    people = org_people(tenant)
    ops = plan["operators"]
    hr, it_a, it_b, acc = ops["hr"], ops["it_creator"], ops["it_approver"], ops["accounting"]
    for g in plan["grants"]:  # SoD trước khi ghi bất cứ gì
        if g["person"] in (hr, it_a, it_b) or len({hr, it_a, it_b}) != 3:
            raise SystemExit(
                f"SoD: người được cấp {g['person']} trùng người vận hành, hoặc HR/IT trùng nhau"
            )
    state = load_users()
    ensure_operators(
        state, tenant, [hr, it_a, it_b, acc, *(g["manager"] for g in plan["grants"])], apply
    )
    for g in plan["grants"]:
        name = people[g["person"]]["full_name"]
        log(f"{g['person']} {name}: {g['roles']} (quản lý {g['manager']})")
    if not apply:
        log("chưa ghi gì; thêm --apply để chạy")
        return {"applied": False}
    op = Operator(state, alias)
    today = dt.date.today().isoformat()
    dept_spec = plan["department"]
    dept = one(op, hr, "department", {"code": dept_spec["code"]})
    if dept is None:
        dept = op.ok(
            hr,
            "create_record",
            {
                "model_id": "department",
                "values": {**dept_spec, "active": True},
                "idempotency_key": idem(alias, "dept", dept_spec["code"]),
            },
        )["record"]
    hr_ref = person_ref(op, hr, people[hr]["full_name"])
    report: dict[str, Any] = {"applied": True, "grants": []}
    for g in plan["grants"]:
        key = g["person"]
        e = people[key]
        user_ref = person_ref(op, hr, e["full_name"])
        emp = one(op, hr, "employee", {"emp_no": key})
        if emp is None:
            emp = op.ok(
                hr,
                "create_record",
                {
                    "model_id": "employee",
                    "values": {
                        "emp_no": key,
                        "user_id": user_ref,
                        "full_name": e["full_name"],
                        "job_title": e.get("title") or "Nhân viên",
                        "department": dept["ref"],
                        "state": "permanent",
                        "synthetic": True,
                    },
                    "idempotency_key": idem(alias, "emp", key),
                },
            )["record"]
        # mẫu ^(ON|OF)-YYYY-NNNN; dải 9xxx dành cho hồ sơ cấp vai trò demo
        code = f"ON-{today[:4]}-9{key[-3:]}"
        case = one(op, hr, "lifecycle_case", {"code": code})
        if case is None:
            case = op.ok(
                hr,
                "create_record",
                {
                    "model_id": "lifecycle_case",
                    "values": {
                        "code": code,
                        "employee": emp["ref"],
                        "kind": "onboarding",
                        "effective_on": today,
                        "state": "processing",
                        "initiated_by": hr_ref,
                    },
                    "idempotency_key": idem(alias, "case", key),
                },
            )["record"]
        for role in g["roles"]:
            if (
                one(
                    op,
                    it_a,
                    "access_grant",
                    {"employee": emp["ref"], "role": role, "system": "keycloak"},
                )
                is None
            ):
                op.ok(
                    it_a,
                    "create_record",
                    {
                        "model_id": "access_grant",
                        "values": {
                            "employee": emp["ref"],
                            "system": "keycloak",
                            "role": role,
                            "issued_on": today,
                        },
                        "idempotency_key": idem(alias, "grant", key, role),
                    },
                )
        started = (
            one(op, hr, "lifecycle_case", {"code": code}, ["code", "process_instance_id"]) or {}
        )
        if not (started.get("values") or {}).get("process_instance_id"):
            op.ok(
                hr,
                "start_process",
                {
                    "process_id": "employee_onboarding",
                    "subject_ref": case["ref"],
                    "parameters": {
                        "case_id": case["ref"].split(":", 1)[1],
                        "employee_id": emp["ref"].split(":", 1)[1],
                        "employee_user": user_ref,
                    },
                    "idempotency_key": idem(alias, "start", key),
                },
            )
        actors = {"hr": hr, "it": it_b, "accounting": acc, "manager": g["manager"]}
        done: dict[str, str] = {}
        for step in STEPS:
            task = one(
                op,
                hr,
                "lifecycle_task",
                {"lifecycle_case": case["ref"], "step": step},
                ["step", "state"],
            )
            if task is None:
                raise RuntimeError(f"{key}: chưa có lifecycle_task bước {step}")
            if (task.get("values") or {}).get("state", task.get("state")) != "done":
                op.confirmed(
                    actors[step],
                    "transition_record",
                    {
                        "ref": task["ref"],
                        "to_state": "done",
                        "expected_revision": task["revision"],
                        "idempotency_key": idem(alias, "task", key, step, str(attempt)),
                    },
                )
            done[step] = actors[step]
        final = one(op, hr, "lifecycle_case", {"code": code}, ["code", "state"]) or {}
        report["grants"].append(
            {
                "person": key,
                "roles": g["roles"],
                "case": code,
                "steps": done,
                "case_state": (final.get("values") or {}).get("state", final.get("state")),
            }
        )
        log(f"{key}: xong ({report['grants'][-1]['case_state']})")
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="demo-roles")
    ap.add_argument("plan", type=Path)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument(
        "--attempt",
        type=int,
        default=0,
        help="tăng khi một xác nhận trước đó bị từ chối/hết hạn (idempotency_key mới cho bước đó)",
    )
    a = ap.parse_args(argv)
    out = run(json.loads(a.plan.read_text(encoding="utf-8")), a.apply, a.attempt)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
