import sys, time, json; sys.path.insert(0, "src")
from xtflow import *
XML = open("src/tamung_v1.bpmn").read()
LEDGER = []
def ghi_so(amount, requester): LEDGER.append((requester, amount)); return {"so": f"PC-{len(LEDGER):04d}", "amount": amount}
def notify(msg): print("   [notify]", msg); return {"sent": True}
eng = lambda: safe_engine({"ghi_so": ghi_so, "notify": notify})
def show(wf): return [(t.task_spec.name, t.task_spec.lane, t.task_spec.bpmn_name) for t in ready_user_tasks(wf)]

for amount in (5_000_000, 50_000_000):
    print(f"== amount={amount}")
    wf = new_instance(XML, "tamung", eng(), {"amount": amount, "requester": "nguyenvana"})
    print(" READY:", show(wf))
    complete(wf, ready_user_tasks(wf)[0], {"tp_decision": "approve"})
    print(" READY sau TP:", show(wf))
    while ready_user_tasks(wf):
        complete(wf, ready_user_tasks(wf)[0], {"gd_decision": "approve"})
        print(" READY tiếp:", show(wf))
    print(" completed:", wf.is_completed(), "| data:", {k: v for k, v in wf.data.items()}, "| task data:", wf.last_task.data)
print("LEDGER", LEDGER)

print("== timer SLA (PT2S) – không ai duyệt")
wf = new_instance(XML, "tamung", eng(), {"amount": 1_000_000, "requester": "b"})
print(" t=0 READY:", show(wf))
wf.refresh_timers(); print(" t=0 sau refresh_timers, READY:", show(wf))
time.sleep(2.2)
print(" t=2.2 chưa refresh, READY:", show(wf))
wf.refresh_timers(); wf.do_engine_steps()
print(" t=2.2 sau refresh_timers+do_engine_steps, READY:", show(wf))
complete(wf, ready_user_tasks(wf)[0], {"gd_decision": "approve"})
print(" completed:", wf.is_completed(), "LEDGER:", LEDGER)
print(" waiting_events:", wf.waiting_events())
