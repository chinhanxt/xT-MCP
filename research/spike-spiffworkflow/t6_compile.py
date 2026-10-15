import sys, yaml, copy, time; sys.path.insert(0, "src")
from xtflow import *
from compiler import *
spec = yaml.safe_load(SPEC_YAML.format(version=1, threshold=10000000))
xml = compile_spec(spec, ALLOWED_OPS)
print("XML bytes:", len(xml), "| yaml bytes:", len(SPEC_YAML))
L = []
eng = lambda: safe_engine({"ghi_so": lambda amount, requester: (L.append(amount), {"so": len(L)})[1], "notify": lambda msg: print("   [notify]", msg) or {"ok": 1}})
# chạy tay vì decision var theo step id (tp_decision, gd_decision)
def drive(amount, decisions, sleep=0):
    wf = new_instance(xml, "tamung", eng(), {"amount": amount, "requester": "a"})
    if sleep: time.sleep(sleep); wf.refresh_timers(); wf.do_engine_steps()
    path = []
    while ready_user_tasks(wf):
        t = ready_user_tasks(wf)[0]; step = t.task_spec.name.replace("ut_", "").replace("_esc", "")
        path.append(f"{t.task_spec.bpmn_name}@{t.task_spec.lane}")
        complete(wf, t, {f"{step}_decision": decisions[step]})
    from SpiffWorkflow.util.task import TaskState
    end = 'REJECTED' if any(t.task_spec.name == 'end_rejected' for t in wf.get_tasks(state=TaskState.COMPLETED)) else 'APPROVED/ghi sổ'
    return path, wf.is_completed(), end
print("5tr, approve   :", drive(5_000_000, {"tp": "approve"}))
print("50tr, approve  :", drive(50_000_000, {"tp": "approve", "gd": "approve"}))
print("50tr, GĐ reject:", drive(50_000_000, {"tp": "approve", "gd": "reject"}))
print("5tr, TP reject :", drive(5_000_000, {"tp": "reject"}))
print("5tr, SLA quá hạn:", drive(5_000_000, {"tp": "approve"}, sleep=2.2))
print("LEDGER", L)
print("--- spec xấu (agent sinh sai/độc) phải bị từ chối khi COMPILE:")
bad = []
s = copy.deepcopy(spec); s["steps"][0]["role"] = "hacker"; bad.append(("role lạ", s))
s = copy.deepcopy(spec); s["steps"][2]["op"] = "os_system"; bad.append(("op ngoài allowlist", s))
s = copy.deepcopy(spec); s["steps"][1]["when"] = "__import__('os').system('id')"; bad.append(("when độc", s))
s = copy.deepcopy(spec); s["steps"][0]["sla"]["after"] = "'+__import__('os')+'"; bad.append(("SLA không phải ISO", s))
s = copy.deepcopy(spec); s["steps"][2]["params"] = {"amount": "amount", "requester": "open('/etc/passwd').read()"}; bad.append(("param service độc", s))
s = copy.deepcopy(spec); s["steps"][1]["type"] = "script"; bad.append(("type script", s))
for label, s in bad:
    try: compile_spec(s, ALLOWED_OPS); print("  !!! KHÔNG chặn:", label)
    except (SpecError, UnsafeExpression, SyntaxError) as e: print(f"  chặn {label:<22}: {type(e).__name__}: {e}")
