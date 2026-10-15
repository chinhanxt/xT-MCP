# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""`make demo-seed`: dữ liệu demo trên stack minimal thật, idempotent (chạy lại không đổi kết quả).

1. Realm `xt` (deploy/keycloak/provision.py) + client headless `xt-headless` + scope `xt-data`
   (provision_data.py) + lớp PostgREST (configure_data.py) + JWKS (fetch_jwks.py) -> bật `postgrest`.
2. Hai tenant thật (`xt tenant create`) = hai Organization Keycloak: A "Bình Minh"
   (seed/binh-minh/org.json), B "Hoa Mai" (seed/hoa-mai/org.json, nhỏ).
3. User Keycloak cho một tập đại diện (giám đốc, kế toán, 2 trưởng phòng, 3 nhân viên ở A; 3 người ở B (Hoa Mai);
   1 user thuộc cả hai Organization). Người duyệt có TOTP với seed đã biết; bí mật chỉ ở
   `${XT_SECRETS_DIR:-~/.config/xt}/e2e/demo-seed.json` (0600, ngoài repo).
4. `xt org import` (tệp HR + links sub/agent grant), `xt pack install` hai bước có mã xác nhận,
   `xt tenant bind-queue`, `xt doctor`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from xte2e import compose
from xte2e.config import DEPLOY, HEADLESS_CLIENT, ROOT, STACK_OVERRIDES, ensure_secret, load_users, save_users, settings
from xte2e.kcadmin import Admin

AGENT_CLIENT = "xt-desk-agent"
TOTP_ROLES = {"director", "dept_head", "accountant", "chief_accountant", "payment_accountant", "hr", "hr_pii", "it_admin", "admin"}
KC_ROLES = {"employee", "dept_head", "director", "chief_accountant", "payment_accountant", "hr", "hr_pii", "it_admin", "admin"}
# Vai trò KC cho vai trò HR không có trong realm (kế toán viên -> kế toán thanh toán).
KC_ROLE_ALIAS = {"accountant": "payment_accountant"}

TENANTS: list[dict[str, Any]] = [
    {
        "alias": "binhminh", "name": "Công ty TNHH Bình Minh", "domain": "binhminh.example",
        "org": ROOT / "seed" / "binh-minh" / "org.json",
        # 3 gói nghiệp vụ lõi trước, sau đó mọi gói khác có trong packages/ (vd. quản trị mô hình, kênh PII).
        "packs": ["de-xuat-phe-duyet", "giao-viec-sla", "nhan-su-vong-doi", *sorted(
            p.parent.name for p in (ROOT / "packages").glob("*/pack.json")
            if p.parent.name not in ("de-xuat-phe-duyet", "giao-viec-sla", "nhan-su-vong-doi"))],
        # giám đốc, kế toán, 2 trưởng phòng (Kinh doanh, Kế toán), 3 nhân viên Kinh doanh
        "people": ["NV-0001", "NV-0005", "NV-0010", "NV-0004", "NV-0013", "NV-0014", "NV-0020"],
    },
    {
        "alias": "hoamai", "name": "Cửa hàng Hoa Mai", "domain": "hoamai.example",
        "org": ROOT / "seed" / "hoa-mai" / "org.json",
        "packs": ["de-xuat-phe-duyet", "giao-viec-sla"],
        # chủ cửa hàng (giám đốc), trưởng nhóm Bán hàng, 1 nhân viên Bán hàng
        "people": ["NV-0001", "NV-0004", "NV-0007"],
        # Diễn tập SLA (E7): cùng gói, chỉ đổi DỮ LIỆU gói (mọi `sla.after` -> PT2M), không đổi code.
        "sla": "PT2M",
    },
]
MULTI_ORG_USER = "e2e.hai-to-chuc"


def log(msg: str) -> None:
    print(f"[demo-seed] {msg}", flush=True)


def host_env() -> dict[str, str]:
    return {**os.environ, **{k: v for k, v in STACK_OVERRIDES.items() if k not in os.environ}}


def run_host(argv: list[str]) -> str:
    r = subprocess.run(argv, cwd=DEPLOY, env=host_env(), text=True, capture_output=True, timeout=600)
    if r.returncode != 0:
        raise SystemExit(f"[demo-seed] {' '.join(argv[:2])} lỗi rc={r.returncode}\n{r.stdout[-1500:]}\n{r.stderr[-1500:]}")
    return r.stdout


def xt_ok(args: list[str], **kw: Any) -> str:
    rc, out = compose.xt(args, **kw)
    if rc != 0:
        raise SystemExit(f"[demo-seed] xt {' '.join(args[:2])} lỗi rc={rc}\n{out[-2500:]}")
    return out


def wait_core() -> None:
    end = time.time() + 300
    while time.time() < end:
        ps = compose.ps()
        states = dict(line.split("\t", 1) for line in ps.splitlines() if "\t" in line)
        need = ["postgres", "keycloak", "apisix", "xt-api"]
        if all(s in states and "healthy" in states[s] or (s == "xt-api" and states.get(s, "").startswith("running")) for s in need):
            return
        time.sleep(3)
    raise SystemExit(f"[demo-seed] stack chưa sẵn sàng:\n{compose.ps()}")


# ------------------------------------------------------------------ realm/data ----
def realm_and_data(kc: Admin) -> dict[str, Any]:
    out: dict[str, Any] = {}
    log("provision realm (deploy/keycloak/provision.py)")
    out["provision_realm"] = run_host([sys.executable, "keycloak/provision.py"]).splitlines()[-3:]
    kc.login()
    # Client headless `xt-cli` do provision.py tạo; dọn client tạm của bản harness cũ nếu còn.
    old = kc.client("xt-headless")
    if old:
        kc.call("DELETE", f"/clients/{old['id']}")
    if kc.client(HEADLESS_CLIENT) is None:
        raise SystemExit(f"[demo-seed] realm thiếu client {HEADLESS_CLIENT} (deploy/keycloak/provision.py)")
    log("scope xt-data + xt-workflow (deploy/keycloak/provision_data.py)")
    out["provision_data"] = run_host([sys.executable, "keycloak/provision_data.py"]).splitlines()[-1:]
    log("lớp PostgREST trong DB (deploy/scripts/configure_data.py, kết nối quản trị trong mạng compose)")
    e = settings().env
    secret_env = {
        "XT_DATA_SUPERUSER_URL": compose.superuser_dsn(),
        "XT_PGRST_PASSWORD": e["XT_PGRST_PASSWORD"],
        "XT_DATA_CLIENTS": HEADLESS_CLIENT,
        "XT_DOMAIN_AUTH": settings().hosts["auth"],
        "XT_REALM": settings().realm,
    }
    r = compose.dc(["run", "--rm", "--no-deps", "-T", *sum((["-e", k] for k in secret_env), []),
                    "-v", f"{DEPLOY / 'scripts'}:/scripts:ro", "--entrypoint", "python", "xt-api",
                    "/scripts/configure_data.py"], env=secret_env, check=False)
    if r.returncode != 0:
        raise SystemExit(f"[demo-seed] configure_data lỗi\n{r.stdout[-1500:]}{r.stderr[-1500:]}")
    out["configure_data"] = r.stdout.strip().splitlines()[-1:]
    log("JWKS cho PostgREST (deploy/scripts/fetch_jwks.py)")
    out["fetch_jwks"] = run_host([sys.executable, "scripts/fetch_jwks.py"]).splitlines()[-1:]
    compose.dc(["up", "-d", "--no-deps", "postgrest"])
    compose.dc(["kill", "-s", "USR2", "postgrest"], check=False)
    return out


# ---------------------------------------------------------------------- tenant ----
def tenant_exists(alias: str) -> bool:
    return compose.psql(f"SELECT count(*) FROM xt_meta.tenant_profile WHERE alias = '{alias}'") == "1"


def tenant_row(alias: str) -> dict[str, Any]:
    row = compose.psql(
        "SELECT t.seq, t.id FROM xt_meta.tenant_profile p JOIN xt_meta.tenant t ON t.id = p.tenant_id"
        f" WHERE p.alias = '{alias}'"
    )
    seq, tid = row.split("\t")
    return {"seq": int(seq), "id": tid, "schema": f"xt_t{seq}", "profile": f"api_t{seq}"}


def installed(alias: str) -> set[str]:
    rows = compose.psql(
        "SELECT i.pack FROM xt_meta.pack_install i JOIN xt_meta.tenant_profile p ON p.tenant_id = i.tenant_id"
        f" WHERE p.alias = '{alias}'"
    )
    return {r for r in rows.splitlines() if r}


def kc_username(email: str) -> str:
    return email.split("@", 1)[0]


def seed_users(kc: Admin, t: dict[str, Any], hr: dict[str, Any], state_ref: dict[str, Any], org_id: str) -> dict[str, str]:
    secrets_db: dict[str, Any] = state_ref.setdefault("users", {})
    by_id = {e["id"]: e for e in hr["employees"]}
    subjects: dict[str, str] = {}
    for key in t["people"]:
        e = by_id[key]
        roles = list(e.get("roles", []))
        kc_roles = sorted({KC_ROLE_ALIAS.get(r, r) for r in [*roles, "employee"]} & KC_ROLES)
        approver = bool(set(roles) & TOTP_ROLES)
        uname = kc_username(e["email"])
        fresh = "password" not in secrets_db.get(uname, {}) or (approver and "totp" not in secrets_db[uname])
        ent = ensure_secret(secrets_db.setdefault(uname, {}), totp=approver)
        family, _, given = e["full_name"].partition(" ")
        uid = kc.upsert_user({"username": uname, "email": e["email"], "firstName": given or family,
                              "lastName": family}, ent["password"], ent.get("totp"), recreate=fresh)
        kc.set_realm_roles(uid, kc_roles)
        kc.add_member(org_id, uid)
        ent.update({"username": uname, "sub": uid, "tenant": t["alias"], "key": key, "name": e["full_name"],
                    "roles": roles, "kc_roles": kc_roles, "approver": approver, "department": e["department_id"]})
        subjects[key] = uid
        save_users(state_ref)  # lưu ngay: seed bị ngắt giữa chừng vẫn giữ đúng bí mật đã nạp vào Keycloak
    return subjects


def links_for(subjects: dict[str, str]) -> dict[str, Any]:
    """Quyền L1 cho agent Desk (xt-desk-agent) và client headless (xt-cli, REST /workflow/v1) thay mặt
    từng người có đăng nhập; người vận hành cấp tường minh, không có quyền ngầm."""
    return {"subjects": subjects,
            "agent_grants": [{"person": k, "client_id": cid, "autonomy": 1, "roles": ["agent"]}
                             for k in subjects for cid in (AGENT_CLIENT, HEADLESS_CLIENT)]}


def import_org(alias: str, org_file: Path, links: dict[str, Any], extra_grants: list[dict[str, Any]] | None = None) -> str:
    """`xt org import` thật. Tệp tạm 0644 (container uid 10001 phải đọc được; không chứa bí mật)."""
    d = Path(tempfile.mkdtemp(prefix="xt-e2e-org-"))
    try:
        d.chmod(0o755)
        lk = {**links, "agent_grants": [*links["agent_grants"], *(extra_grants or [])]}
        (d / "links.json").write_text(json.dumps(lk, ensure_ascii=False), encoding="utf-8")
        shutil.copyfile(org_file, d / "org.json")
        for f in d.iterdir():
            f.chmod(0o644)
        return xt_ok(["org", "import", "--tenant", alias, "/seed/org.json", "--links", "/seed/links.json",
                      "--base-roles", "employee"], mounts=[(d, "/seed")])
    finally:
        shutil.rmtree(d, ignore_errors=True)


def sla_variant(pack: str, after: str) -> Path:
    """Bản sao gói với mọi SLA = `after` (tệp tạm 0755/0644 cho container đọc); chỉ dữ liệu, không code."""
    d = Path(tempfile.mkdtemp(prefix="xt-e2e-pack-"))
    dst = d / pack
    shutil.copytree(ROOT / "packages" / pack, dst)
    for f in (dst / "processes").glob("*.json"):
        f.write_text(re.sub(r'"after":\s*"PT[0-9]+[HMS]"', f'"after": "{after}"', f.read_text(encoding="utf-8")),
                     encoding="utf-8")
    for p in [d, *d.rglob("*")]:
        p.chmod(0o755 if p.is_dir() else 0o644)
    return d


def install_pack(alias: str, pack: str, approver: str, sla: str | None = None) -> dict[str, Any]:
    """Hai bước như vận hành thật: xem kế hoạch (diễn tập, chưa áp) -> chạy lại với đúng mã xác nhận."""
    common = ["pack", "install", "--tenant", alias, f"/packages/{pack}",
              "--proposed-by", "agent:demo-seed", "--approved-by", f"person:{approver}"]
    src = sla_variant(pack, sla) if sla and pack == "de-xuat-phe-duyet" else ROOT / "packages"
    try:
        return _install(common, [(src, "/packages")], pack)
    finally:
        if src != ROOT / "packages":
            shutil.rmtree(src, ignore_errors=True)


def _install(common: list[str], mounts: list[tuple[Path, str]], pack: str) -> dict[str, Any]:
    rc, plan = compose.xt(common, mounts=mounts)
    m = re.search(r"(sha256:[0-9a-f]{64})", plan)
    if rc != 0 or not m:
        # Không dừng cả seed: gói không cài được là một khoảng trống sản phẩm, E0 báo đúng tên.
        problems = plan.split("KHÔNG CÀI ĐƯỢC:", 1)[-1].strip().splitlines() if "KHÔNG CÀI ĐƯỢC:" in plan else plan.strip().splitlines()[-5:]
        log(f"  gói {pack}: KHÔNG CÀI ĐƯỢC (rc={rc}): {' | '.join(x.strip(' -') for x in problems)}")
        return {"ok": False, "rc": rc, "problems": [x.strip(" -") for x in problems]}
    rc, done = compose.xt([*common, "--confirm", m.group(1)], mounts=mounts)
    if rc != 0:
        log(f"  gói {pack}: --confirm lỗi rc={rc}: {done.strip().splitlines()[-1:]}")
        return {"ok": False, "rc": rc, "digest": m.group(1), "problems": done.strip().splitlines()[-5:]}
    return {"ok": True, "plan_tail": "\n".join(plan.strip().splitlines()[-6:]), "digest": m.group(1),
            "install": done.strip().splitlines()[-1]}


def bind_queue(alias: str) -> str:
    e = settings().env
    db = e.get("POSTGRES_DB") or "xt"
    secret_env = {
        "XT_DB_OWNER_URL": compose.superuser_dsn(),
        "XT_DB_MIGRATOR_URL": f"postgresql://xt_migrator:{e['XT_DB_MIGRATOR_PASSWORD']}@postgres:5432/{db}",
        # Biến bắt buộc của handler worker (composition/worker.py); lệnh vận hành cần cùng cấu hình worker.
        "XT_WORKFLOW_RELAY_SECONDS": os.environ.get("XT_WORKFLOW_RELAY_SECONDS", "30"),
    }
    return xt_ok(["tenant", "bind-queue", "--tenant", alias], service="xt-worker", secret_env=secret_env).strip()


def main() -> int:
    s = settings()
    wait_core()
    kc = Admin()
    kc.wait_ready()
    state: dict[str, Any] = load_users()
    secrets_db: dict[str, Any] = state.setdefault("users", {})
    report: dict[str, Any] = {"started": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    report["realm_data"] = realm_and_data(kc)
    tenants: dict[str, Any] = state.setdefault("tenants", {})
    for t in TENANTS:
        alias = t["alias"]
        if tenant_exists(alias):
            log(f"tenant {alias}: đã có")
        else:
            log(f"tenant {alias}: xt tenant create")
            report[f"tenant-{alias}"] = xt_ok(["tenant", "create", "--alias", alias, "--name", t["name"],
                                               "--timezone", "Asia/Ho_Chi_Minh", "--llm-egress", "masked"]).strip()
        org_id = kc.upsert_org(alias, t["name"], t["domain"])
        hr = json.loads(Path(t["org"]).read_text(encoding="utf-8"))
        subjects = seed_users(kc, t, hr, state, org_id)
        links = links_for(subjects)
        log(f"tenant {alias}: xt org import ({len(subjects)} người có đăng nhập)")
        report[f"org-{alias}"] = import_org(alias, Path(t["org"]), links).strip().splitlines()[-1]
        have = installed(alias)
        director = next(k for k in t["people"] if "director" in secrets_db[kc_username(
            next(e for e in hr["employees"] if e["id"] == k)["email"])]["roles"])
        for pack in t["packs"]:
            if pack in have:
                log(f"tenant {alias}: gói {pack} đã cài")
                continue
            log(f"tenant {alias}: xt pack install {pack} (kế hoạch -> --confirm)")
            res = install_pack(alias, pack, director, t.get("sla"))
            report[f"pack-{alias}-{pack}"] = res
            failures = state.setdefault("pack_failures", {})
            if res["ok"]:
                failures.pop(f"{alias}/{pack}", None)
            else:
                failures[f"{alias}/{pack}"] = res["problems"]
        log(f"tenant {alias}: xt tenant bind-queue")
        report[f"bind-{alias}"] = bind_queue(alias)
        tenants[alias] = {**tenant_row(alias), "name": t["name"], "org_id": org_id, "links": links,
                          "org_file": str(t["org"]), "packs": t["packs"], "sla": t.get("sla")}
    # User thuộc hai Organization (E4: tenant_ambiguous); không có người trong xT.
    ent = ensure_secret(secrets_db.setdefault(MULTI_ORG_USER, {}), totp=False)
    uid = kc.upsert_user({"username": MULTI_ORG_USER, "email": "hai-to-chuc@e2e.example", "firstName": "Hai",
                          "lastName": "Tổ chức"}, ent["password"], None)
    kc.set_realm_roles(uid, ["employee"])
    for t in tenants.values():
        kc.add_member(t["org_id"], uid)
    ent.update({"username": MULTI_ORG_USER, "sub": uid, "tenant": None, "key": None, "roles": ["employee"],
                "approver": False, "orgs": sorted(tenants)})
    rc, doctor = compose.xt(["doctor"])
    report["doctor"] = {"rc": rc, "out": doctor.strip().splitlines()[-15:]}
    state["seeded_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    state["project"] = s.project
    save_users(state)
    (s.state_dir / "demo-seed-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"xong: {len(tenants)} tenant, {len(secrets_db)} user; bí mật ở {s.state_dir}/demo-seed.json (0600)")
    log(f"xt doctor rc={rc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
