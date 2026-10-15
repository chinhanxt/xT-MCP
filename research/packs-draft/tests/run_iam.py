# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
import json, sys, dataclasses, builtins, os
_QUIET = __name__ != '__main__'
_p = builtins.print
def print(*a, **k):
    if not _QUIET: _p(*a, **k)
import _paths
from contracts import Actor, Request, Host, fingerprint
from mini import Mini
from seedlib import ROOT, load_users
T = "binhminh"
def iam_actor(u, **kw):
    d = dict(id=u["key"], tenant=T, kind=u.get("kind", "human"), roles=tuple(u["roles"]), team="none", department=u["phong_ban"], limit=u.get("han_muc", 0), autonomy=u.get("autonomy", 3), behalf=u.get("behalf"))
    d.update(kw); return Actor(**d)
def pack_users(pack): return load_users(pack)[0]
def X(t, i): return f"xrn:{T}:{t}:{i}"
def build_host(pack, extra_actors=(), approvals=None):
    users = pack_users(pack)
    humans = {k: iam_actor(u) for k, u in users.items() if u.get("kind", "human") == "human"}
    for a in extra_actors: humans[a.id] = a
    doc = json.loads((ROOT / pack / "policies" / "iam.json").read_text())
    if os.environ.get("IAM_MERGED"):      # một policy document cho cả tenant = hợp 3 pack (id statement phải không trùng)
        docs = [json.loads((ROOT / q / "policies" / "iam.json").read_text()) for q in ("de-xuat-phe-duyet", "giao-viec-sla", "nhan-su-vong-doi")]
        doc = {"version": "1", "tenant": T, "statements": [x for d_ in docs for x in d_["statements"]]}
    return Host(Mini(doc), actors=humans, approvals=approvals or {}), humans, doc
def res(owner, creator=None, amount=0, dept="KD"):
    return {"owner": owner, "creator": creator or owner, "amount": amount, "team": "none", "department": dept}
def agent(users, behalf, autonomy=1): return iam_actor(users["agent-claude"], behalf=behalf, autonomy=autonomy)
def receipt(req, by):
    return {"by": by, "expires": 200, "binding": (req.actor.id, req.actor.behalf, req.actor.tenant, req.action, req.xrn, req.revision, fingerprint(req.resource))}
FAIL = []
import io, contextlib
def run(host, label, req, expect, expect_stmt=None):
    d = host.evaluate(req)
    got = "allow" if d.allowed and d.reason in ("allow", "delegated_allow") else d.reason
    ok = (got == expect) and (expect_stmt is None or any(x == expect_stmt or x.endswith("." + expect_stmt) for x in d.statements))
    if not ok: FAIL.append(label)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:<78} -> {got:<18} stmts={list(d.statements)}")
    return d
N = 0
def X(t, i): return f"xrn:{T}:{t}:{i}"

print("=== IAM mode:", "MERGED (1 doc, %d statements)" % len(build_host("de-xuat-phe-duyet")[2]["statements"]) if os.environ.get("IAM_MERGED") else "PER-PACK documents")
print("=== PACK 1: de-xuat-phe-duyet")
users = pack_users("de-xuat-phe-duyet")
h, hum, doc = build_host("de-xuat-phe-duyet")
V, MAI, LONG, HA, BAO, THINH = (hum[k] for k in ("NV-0006", "NV-0005", "NV-0001", "NV-0002", "NV-0003", "NV-0012"))
xr = X("de_xuat", "TU-2026-0042"); r8 = res("NV-0006", amount=8_000_000)
run(h, "Việt tạo đề xuất của chính mình", Request(actor=V, action="record.create", xrn=xr, resource=r8), "allow", "nv-own-create")
run(h, "Việt đọc đề xuất của Anh (NV-0007)", Request(actor=V, action="record.read", xrn=X("de_xuat","TU-2026-0050"), resource=res("NV-0007")), "implicit_deny")
run(h, "Việt tự duyệt đề xuất của mình", Request(actor=V, action="record.approve", xrn=xr, resource=r8), "self_approval")
run(h, "Mai (TBP KD) duyệt đề xuất của Việt (phòng KD)", Request(actor=MAI, action="record.approve", xrn=xr, resource=r8), "allow", "tbp-approve")
run(h, "Mai tự duyệt đề xuất do chính mình đề nghị", Request(actor=MAI, action="record.approve", xrn=X("de_xuat","TU-2026-0051"), resource=res("NV-0005", amount=8_000_000)), "self_approval")
run(h, "Long (GĐ) duyệt đề xuất của Việt", Request(actor=LONG, action="record.approve", xrn=xr, resource=r8), "allow", "gd-read-approve")
run(h, "Thịnh (TBP IT) duyệt đề xuất phòng KD (sai phòng)", Request(actor=THINH, action="record.approve", xrn=xr, resource=r8), "implicit_deny")
run(h, "Hà (KTT) duyệt thanh toán 80tr của Việt", Request(actor=HA, action="record.approve", xrn=X("de_xuat","TT-2026-0007"), resource=res("NV-0006", amount=80_000_000)), "allow", "ktt-read-approve")
run(h, "Long xóa đề xuất (chứng từ không được xóa)", Request(actor=LONG, action="record.delete", xrn=xr, resource=r8), "explicit_deny", "deny-delete-chung-tu")
ag1 = agent(users, "NV-0006", 1); h2, _, _ = build_host("de-xuat-phe-duyet", [ag1])
run(h2, "agent-claude (L1, thay Việt) tạo nháp đề xuất", Request(actor=ag1, action="record.create", xrn=xr, resource=r8), "allow", "agent-l0-l1")
run(h2, "agent-claude (L1, thay Việt) đọc đề xuất của Anh", Request(actor=ag1, action="record.read", xrn=X("de_xuat","TU-2026-0050"), resource=res("NV-0007")), "behalf_permission")
d = run(h2, "agent-claude (L1, thay Việt) TỰ DUYỆT đề xuất của Việt", Request(actor=ag1, action="record.approve", xrn=xr, resource=r8), "self_approval")
ag1m = agent(users, "NV-0005", 1); h3, _, _ = build_host("de-xuat-phe-duyet", [ag1m])
run(h3, "agent-claude (L1, thay Mai) duyệt đề xuất của Việt", Request(actor=ag1m, action="record.approve", xrn=xr, resource=r8), "autonomy_ceiling")
ag3 = agent(users, "NV-0005", 3)
req = Request(actor=ag3, action="record.approve", xrn=xr, resource=r8, approval="rc1")
h4, _, _ = build_host("de-xuat-phe-duyet", [ag3], approvals={"rc1": receipt(req, "NV-0001")})
d = run(h4, "agent L3 + receipt hợp lệ của Long, thay Mai duyệt đề xuất của Việt", req, "explicit_deny", "deny-agent-decide")
run(h4, "agent L3 (thay Mai) xóa đề xuất, không có receipt", Request(actor=ag3, action="record.delete", xrn=xr, resource=r8), "approval_required")
reqd = Request(actor=ag3, action="record.delete", xrn=xr, resource=r8, approval="rc2")
h5, _, _ = build_host("de-xuat-phe-duyet", [ag3], approvals={"rc2": receipt(reqd, "NV-0001")})
run(h5, "agent L3 (thay Mai) xóa đề xuất, có receipt của Long (Long cũng không được xóa)", reqd, "approver_permission")
ag_other = dataclasses.replace(V, tenant="ankhang")
run(h, "actor tenant ankhang truy cập xrn của binhminh", Request(actor=ag_other, action="record.read", xrn=xr, resource=r8), "tenant_boundary")
print("--- explain() cho ca agent tự duyệt bị chặn bởi policy (L3 + receipt):")
m = Mini(doc); ex = m.explain(req); tr = [t for t in ex["trace"] if t["matched"] or t["id"] in ("agent-l0-l1",)]
print("  ", json.dumps({"allowed": ex["allowed"], "reason": ex["reason"], "statements": ex["statements"], "matched_trace": [{"id": t["id"], "effect": t["effect"], "predicates": t["predicates"]} for t in ex["trace"] if t["matched"]]}, ensure_ascii=False)[:600])

print("\n=== PACK 2: giao-viec-sla")
users = pack_users("giao-viec-sla"); h, hum, doc = build_host("giao-viec-sla")
V, MAI, ANH, LONG, THINH = (hum[k] for k in ("NV-0006", "NV-0005", "NV-0007", "NV-0008", "NV-0012")) if False else (hum["NV-0006"], hum["NV-0005"], hum["NV-0007"], hum["NV-0001"], hum["NV-0012"])
cv = X("cong_viec", "CV-2026-0001"); rcv = res("NV-0006", creator="NV-0006")      # owner = creator = người làm (quy ước pack)
run(h, "Việt (người làm) cập nhật tiến độ việc của mình", Request(actor=V, action="record.update", xrn=cv, resource=rcv), "allow", "nguoi-thuc-hien-update")
run(h, "Anh cập nhật việc của Việt", Request(actor=ANH, action="record.update", xrn=cv, resource=rcv), "implicit_deny")
run(h, "Việt tự nghiệm thu việc của mình", Request(actor=V, action="record.approve", xrn=cv, resource=rcv), "self_approval")
run(h, "Mai (người giao, TBP KD) nghiệm thu việc của Việt", Request(actor=MAI, action="record.approve", xrn=cv, resource=rcv), "allow", "nghiem-thu")
run(h, "[GAP] Mai nghiệm thu khi creator = người giao (đúng dữ liệu DB)", Request(actor=MAI, action="record.approve", xrn=cv, resource=res("NV-0006", creator="NV-0005")), "self_approval")
run(h, "Thịnh (TBP IT) nghiệm thu việc phòng KD", Request(actor=THINH, action="record.approve", xrn=cv, resource=rcv), "implicit_deny")
run(h, "Mai tạo công việc giao cho nhân viên phòng KD", Request(actor=MAI, action="record.create", xrn=X("cong_viec","CV-2026-0100"), resource=res("NV-0006", creator="NV-0005")), "allow", "tbp-giao-viec")
ag = agent(users, "NV-0005", 1); h2, _, _ = build_host("giao-viec-sla", [ag])
run(h2, "agent-claude (L1, thay Mai) tạo công việc (tách việc từ chỉ đạo)", Request(actor=ag, action="record.create", xrn=X("cong_viec","CV-2026-0100"), resource=res("NV-0006", creator="NV-0005")), "allow", "agent-l0-l1")
run(h2, "agent-claude (L1, thay Mai) nghiệm thu", Request(actor=ag, action="record.approve", xrn=cv, resource=rcv), "autonomy_ceiling")
ag3 = agent(users, "NV-0005", 3); rq = Request(actor=ag3, action="record.approve", xrn=cv, resource=rcv, approval="rc")
h3, _, _ = build_host("giao-viec-sla", [ag3], approvals={"rc": receipt(rq, "NV-0001")})
run(h3, "agent L3 + receipt của Long nghiệm thu thay Mai", rq, "explicit_deny", "deny-agent-nghiem-thu")

print("\n=== PACK 3: nhan-su-vong-doi")
users = pack_users("nhan-su-vong-doi"); h, hum, doc = build_host("nhan-su-vong-doi")
HUNG, VIET, LINH, TAM, VY, MAI, HA = (hum[k] for k in ("NV-0011","NV-0006","NV-0010","NV-0009","NV-0013","NV-0005","NV-0002"))
pii = X("nhan_vien_pii", "NV-0011")
run(h, "Hùng đọc hồ sơ PII của chính mình", Request(actor=HUNG, action="record.read", xrn=pii, resource=res("NV-0011", dept="NS")), "allow", "nv-own-read")
run(h, "Việt đọc PII của Hùng", Request(actor=VIET, action="record.read", xrn=pii, resource=res("NV-0011", dept="NS")), "implicit_deny")
run(h, "Linh (hr_pii) đọc PII của Hùng", Request(actor=LINH, action="record.read", xrn=pii, resource=res("NV-0011", dept="NS")), "allow", "hr-pii")
run(h, "Tâm (nhan_su, không có hr_pii) đọc PII của Hùng", Request(actor=TAM, action="record.read", xrn=pii, resource=res("NV-0011", dept="NS")), "implicit_deny")
agp = agent(users, "NV-0010", 1); hp, _, _ = build_host("nhan-su-vong-doi", [agp])
run(hp, "agent-claude (thay Linh, người có hr_pii) đọc PII", Request(actor=agp, action="record.read", xrn=pii, resource=res("NV-0011", dept="NS")), "explicit_deny", "deny-agent-pii")
run(hp, "agent-claude (thay Linh) đọc hồ sơ nhân viên (không PII)", Request(actor=agp, action="record.read", xrn=X("nhan_vien","NV-0015"), resource=res("NV-0015", dept="KD")), "allow", "agent-l0-l1")
hs = X("ho_so_vong_doi", "ON-2026-0002")
run(h, "Linh duyệt bước HR hồ sơ onboarding NV-0016 (phòng KD)", Request(actor=LINH, action="record.approve", xrn=hs, resource=res("NV-0016", creator="NV-0009")), "allow", "nhan-su-all")
run(h, "Tâm (người khởi tạo hồ sơ) tự duyệt bước HR", Request(actor=TAM, action="record.approve", xrn=hs, resource=res("NV-0016", creator="NV-0009")), "self_approval")
run(h, "Vy (it_admin) duyệt bước IT", Request(actor=VY, action="record.approve", xrn=hs, resource=res("NV-0016", creator="NV-0009")), "allow", "it-admin")
run(h, "Mai (quản lý KD) duyệt bước quản lý của hồ sơ KD", Request(actor=MAI, action="record.approve", xrn=hs, resource=res("NV-0016", creator="NV-0009")), "allow", "tbp-ban-giao")
run(h, "Mai duyệt bước quản lý của hồ sơ nhân viên phòng IT", Request(actor=MAI, action="record.approve", xrn=hs, resource=res("NV-0014", creator="NV-0009", dept="IT")), "implicit_deny")
anh = hum["NV-0007"]
run(h, "Anh (người nghỉ việc) tự duyệt hồ sơ nghỉ việc của mình", Request(actor=anh, action="record.approve", xrn=X("ho_so_vong_doi","OF-2026-0001"), resource=res("NV-0007", creator="NV-0009")), "self_approval")
run(h, "Hà (KTT) duyệt quyết toán nghỉ việc", Request(actor=HA, action="record.approve", xrn=X("quyet_toan_nghi_viec","OF-2026-0001"), resource=res("NV-0007", creator="NV-0009")), "allow", "ke-toan")
run(h, "Long xóa hồ sơ nhân viên", Request(actor=hum["NV-0001"], action="record.delete", xrn=X("nhan_vien","NV-0007"), resource=res("NV-0007")), "explicit_deny", "deny-delete-ho-so")
print("\nFAILED CASES:", FAIL or "none")
