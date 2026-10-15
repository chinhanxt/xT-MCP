# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
import json, sys, uuid
import _paths
import psycopg
from psycopg_pool import ConnectionPool
from seedlib import *
from xtmodel.crud import Engine, Actor
PACKS = ["de-xuat-phe-duyet", "giao-viec-sla", "nhan-su-vong-doi"]
cat = load_catalog(PACKS, 3)
pool = ConnectionPool("host=localhost port=15440 dbname=xt user=xt_app password=app", min_size=1, max_size=2, open=True)
eng = Engine(pool, cat)
U_ = {}
for p in PACKS: U_.update(load_users(p)[0])
A = lambda k: actor_of(U_[k])
km = {}
print("### SEED")
for p in PACKS:
    print(p, seed_pack(eng, p, km, print))
def show(t, o):
    s = json.dumps(o, ensure_ascii=False, default=str)
    print(f"- {t}: {s[:330]}")
def code(o): return o.get("error", {}).get("code", "OK") if not o["ok"] else "OK"
viet, mai, ha, long_, anh = A("NV-0006"), A("NV-0005"), A("NV-0002"), A("NV-0001"), A("NV-0007")
wf = actor_of(U_["xt-workflow"])
print("\n### PACK 1: de-xuat-phe-duyet")
base = {"kind": km[("proposal_type","advance_request")], "requester": str(viet.actor_id), "owner_id": str(viet.actor_id), "department_id": str(dept_id("KD")),
        "department": "KD", "amount": 8000000, "reason": "Công tác Đà Nẵng, trả khách sạn và vé", "proposal_no": "TU-2026-0042"}
show("employee (Việt) tạo thẳng proposal qua CRUD", eng.create_record(viet, "proposal", base))
r = eng.create_record(wf, "proposal", base); show("xt_workflow tạo proposal cho Việt", {"ok": r["ok"], "code": r["record"]["proposal_no"] if r["ok"] else r})
dx = r["record"]
show("xt_workflow: duyệt cuối = chính người đề nghị (SoD CHECK)", eng.update_record(wf, "proposal", dx["id"], dx["revision"], {"state":"approved","final_approver":str(viet.actor_id),"approved_at":"2026-10-15T10:00:00+07:00"}))
show("xt_workflow: approved thiếu người duyệt (rule r2)", eng.update_record(wf, "proposal", dx["id"], dx["revision"], {"state":"approved"}))
show("xt_workflow: approved bởi Long (hợp lệ)", eng.update_record(wf, "proposal", dx["id"], dx["revision"], {"state":"approved","final_approver":str(long_.actor_id),"approved_at":"2026-10-15T10:00:00+07:00"}))
tu = {"proposal": dx["id"], "owner_id": str(viet.actor_id), "department_id": str(dept_id("KD")), "prepared_on": "2026-10-15", "amount": 80000000, "trip_start": "2026-11-20", "trip_end": "2026-11-18",
      "settlement_due": "2026-11-10", "payout_method": "bank_transfer"}
show("advance_request 80tr + ngày về < ngày đi + thiếu STK (storyboard S1)", eng.create_record(wf, "advance_request", tu))
show("advance_request hợp lệ 8tr", {"ok": eng.create_record(wf, "advance_request", {**tu, "amount": 8000000, "trip_end": "2026-11-22", "settlement_due": "2026-11-29", "bank_account": "9704000000006"})["ok"]})
show("payment_request 25tr không có HĐĐT", eng.create_record(wf, "payment_request", {"proposal": dx["id"], "owner_id": str(viet.actor_id), "requested_on": "2026-10-15", "amount": 25000000, "content": "Mua thiết bị", "supplier": "Cty X", "payment_method": "cash", "voucher_count": 2}))
hu = {"owner_id": str(viet.actor_id), "settled_on": "2026-11-30", "advance_received": 8000000, "voucher_list": "3 hóa đơn"}
tu_id = km[("advance_request", None)] if ("advance_request", None) in km else None
show("advance_settlement thực chi > tạm ứng mà không khai chi thừa", eng.create_record(wf, "advance_settlement", {**hu, "proposal": dx["id"], "advance_request": next(iter([v for (m,k),v in km.items() if m=="proposal"])), "actual_spent": 9000000}))
print("\n--- cô lập dòng theo vai trò (proposal)")
for nm, a in [("Việt (own)", viet), ("Anh (own, cùng phòng KD)", anh), ("Mai (TBP, department)", mai), ("Hà (KTT, all)", ha), ("agent (role agent, không có quyền DB)", Actor(tenant_id(), uuid.uuid4(), ("agent",)))]:
    o = eng.search_records(a, "proposal", {}, limit=50) if True else None
    show(nm, {"ok": o["ok"], "total": o.get("total"), "code": sorted(x["proposal_no"] for x in o.get("items", []))} if o["ok"] else o["error"])
o = eng.search_records(Actor(tenant_id("ankhang"), uuid.uuid4(), ("director",)), "proposal", {}, limit=50)
show("giám đốc tenant ankhang xem proposal", {"ok": o["ok"], "total": o.get("total")})
print("\n### PACK 2: giao-viec-sla")
mk = {"task_no": "CV-2026-0099", "title": "Thử tự nghiệm thu", "kind": km[("task_type","general")], "assigner": str(mai.actor_id), "assignee": str(viet.actor_id), "owner_id": str(viet.actor_id),
      "department_id": str(dept_id("KD")), "deadline": "2026-10-30T17:00:00+07:00", "done_criteria": "Có báo cáo gửi trưởng phòng"}
show("Việt tự cập nhật cv (không có quyền ghi)", eng.update_record(viet, "task", km[("task","CV-2026-0001")], 1, {"progress": 100, "state": "done"}))
show("Mai (TBP) tạo công việc giao cho Việt", {"ok": (r2 := eng.create_record(mai, "task", mk))["ok"]})
show("Mai tạo việc Khẩn nhưng lớp SLA thường (rule)", eng.create_record(mai, "task", {**mk, "task_no": "CV-2026-0098", "priority": "urgent"}))
show("hoàn thành mà không có kết quả (rule)", eng.update_record(wf, "task", km[("task","CV-2026-0001")], 1, {"state": "done"}))
show("tiến độ 150% (max 100)", eng.update_record(wf, "task", km[("task","CV-2026-0001")], 1, {"progress": 150}))
show("phụ thuộc tự tham chiếu (ref self) hợp lệ", eng.update_record(wf, "task", km[("task","CV-2026-0001")], 1, {"depends_on_task": km[("task","CV-2026-0004")]}))
o = eng.search_records(viet, "task", {}, limit=50); show("Việt (own) thấy cv", sorted(x["task_no"] for x in o["items"]))
o = eng.search_records(anh, "task", {}, limit=50); show("Anh (own) thấy cv", sorted(x["task_no"] for x in o["items"]))
o = eng.search_records(mai, "task", {}, limit=50); show("Mai (department KD) thấy cv", sorted(x["task_no"] for x in o["items"]))
print("\n### PACK 3: nhan-su-vong-doi")
hung, linh, tam, thinh = A("NV-0011"), A("NV-0010"), A("NV-0009"), A("NV-0012")
for nm, a in [("Hùng (employee) đọc PII", hung), ("Việt đọc PII", viet), ("Linh (hr_pii) đọc PII", linh), ("Tâm (hr, không hr_pii) đọc PII", tam)]:
    o = eng.search_records(a, "employee_pii", {}, limit=50)
    show(nm, {"total": o.get("total"), "luong": [x.get("base_salary") for x in o.get("items", [])][:3]} if o["ok"] else o["error"])
o = eng.search_records(Actor(tenant_id(), uuid.uuid4(), ("agent",)), "employee_pii", {}, limit=5); show("agent đọc PII (DB)", o["error"] if not o["ok"] else o)
o = eng.search_records(viet, "employee", {}, limit=50); show("Việt (own) đọc employee", {"total": o["total"]})
o = eng.search_records(thinh, "employee", {}, limit=50); show("Thịnh (TBP IT, dept) đọc employee", {"total": o["total"]})
show("employee nghỉ mà thiếu ngày làm việc cuối (rule)", eng.update_record(linh, "employee", km[("employee","NV-0015")], 1, {"state": "resigned"}))
show("quyết toán đã chi khi còn tài sản chưa trả (rule)", eng.create_record(A("NV-0003"), "offboarding_settlement", {"lifecycle_case": km[("lifecycle_case","ON-2026-0001")], "owner_id": str(A("NV-0015").actor_id), "total_payout": 1000000, "unreturned_assets": 1, "paid": True}))
print("\n### RLS thô")
with psycopg.connect("host=localhost port=15440 dbname=xt user=xt_owner password=owner") as su:
    cnt = {t: su.execute(f'select count(*) from xt_data.{t}').fetchone()[0] for t in ["proposal_type","proposal","advance_request","approval_step","task_type","task","task_item","sla_log","weekly_digest","department","employee","employee_pii","lifecycle_case","lifecycle_task","asset_issue","access_grant"]}
    print("row counts:", cnt)
    print("audit hash-chain verify (n, first_bad_seq):", su.execute("select * from xt_audit.verify(%s)", (str(tenant_id()),)).fetchone())
pool.close()
