# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
import json, pathlib, uuid, sys
import _paths
from xtmodel.crud import Actor
from _paths import PACKS_ROOT as ROOT
NS = uuid.UUID("b1b1b1b1-0000-4000-8000-00000000b1b1")
def tenant_id(t="binhminh"): return uuid.uuid5(NS, "tenant:" + t)
def user_id(k, t="binhminh"): return uuid.uuid5(NS, f"{t}:user:{k}")
def dept_id(k, t="binhminh"): return uuid.uuid5(NS, f"{t}:dept:{k}")
def load_users(pack):
    d = json.loads((ROOT / pack / "seed" / "00_nguoi_dung.json").read_text())
    return {u["key"]: u for u in d["users"]}, d
def actor_of(u, t="binhminh"):
    return Actor(tenant_id(t), user_id(u["key"], t), tuple(u["roles"]), department_ids=(dept_id(u["phong_ban"], t),))
SEED_ROLES = ("xt_workflow", "quan_tri", "nhan_su", "hr_pii", "it_admin", "ke_toan_thanh_toan")
def seed_actor(t="binhminh"): return Actor(tenant_id(t), user_id("seed-loader", t), SEED_ROLES)
def seed_pack(eng, pack, keymap, log):
    files = sorted(p for p in (ROOT / pack / "seed").glob("*.json") if not p.name.startswith("00_"))
    def res(v):
        if isinstance(v, str) and v.startswith("@"):
            kind, key = v[1:].split(":", 1)
            if kind == "user": return str(user_id(key))
            if kind == "dept": return str(dept_id(key))
            return keymap[(kind, key)]
        return v
    n = {}
    for f in files:
        d = json.loads(f.read_text())
        for blk in (d["models"] if "models" in d else [d]):
            for r in blk["records"]:
                key = r.get("_key")
                data = {k: res(v) for k, v in r.items() if k != "_key" and v is not None}
                out = eng.create_record(seed_actor(), blk["model"], data)
                if not out["ok"]:
                    raise SystemExit(f"SEED FAIL {pack}/{f.name} {blk['model']} {key}: {json.dumps(out, ensure_ascii=False)[:600]}")
                if key: keymap[(blk["model"], key)] = out["record"]["id"]
                n[blk["model"]] = n.get(blk["model"], 0) + 1
    return n

def load_catalog(packs, version):
    from xtmodel.spec import Catalog
    ms = []
    for p in packs:
        for f in sorted((ROOT / p / "models").glob("*.json")):
            ms.append(json.loads(f.read_text()))
    return Catalog.model_validate({"version": version, "models": ms})
