import sys, json, hashlib, yaml, time, psycopg; sys.path.insert(0, "src")
from psycopg.types.json import Jsonb
from xtflow import *
from compiler import *
DSN = "postgresql://postgres:x@localhost:15432/spike"
L = []
eng = lambda: safe_engine({"ghi_so": lambda amount, requester: (L.append(amount), {"so": len(L)})[1], "notify": lambda msg: {"ok": 1}})
with psycopg.connect(DSN, autocommit=True) as c:
    c.execute("drop table if exists proc_instance; drop table if exists proc_def")
    c.execute("""create table proc_def(key text, version int, bpmn_xml text not null, sha256 text, primary key(key,version));
                 create table proc_instance(id serial primary key, key text, version int, state jsonb not null, next_due timestamptz, done bool default false);""")
    def deploy(version, threshold, extra=False):
        spec = yaml.safe_load(SPEC_YAML.format(version=version, threshold=threshold))
        if extra: spec["roles"].append("ke_toan"); spec["steps"].insert(2, {"id": "kt", "type": "approve", "name": "Kế toán kiểm tra", "role": "ke_toan"})
        xml = compile_spec(spec, ALLOWED_OPS)
        c.execute("insert into proc_def values (%s,%s,%s,%s)", ["tamung", version, xml, hashlib.sha256(xml.encode()).hexdigest()])
    def start(version, data):
        xml = c.execute("select bpmn_xml from proc_def where key='tamung' and version=%s", [version]).fetchone()[0]
        wf = new_instance(xml, "tamung", eng(), data); return save(None, version, wf)
    def save(iid, version, wf):
        due = min([e.value for e in wf.waiting_events()], default=None)
        st = Jsonb(to_state(wf))
        if iid is None: return c.execute("insert into proc_instance(key,version,state,next_due,done) values ('tamung',%s,%s,%s,%s) returning id", [version, st, due, wf.is_completed()]).fetchone()[0]
        c.execute("update proc_instance set state=%s,next_due=%s,done=%s where id=%s", [st, due, wf.is_completed(), iid]); return iid
    def load(iid):
        v, st = c.execute("select version,state from proc_instance where id=%s", [iid]).fetchone(); return v, from_state(st, eng())
    def step(iid, decisions):
        v, wf = load(iid); path = []
        while ready_user_tasks(wf):
            t = ready_user_tasks(wf)[0]; k = t.task_spec.name.replace("ut_", "").replace("_esc", "")
            path.append(t.task_spec.bpmn_name); complete(wf, t, {f"{k}_decision": decisions.get(k, "approve")})
        save(iid, v, wf); return v, path, wf.is_completed()
    deploy(1, 10_000_000)
    a = start(1, {"amount": 15_000_000, "requester": "A"})
    print("instance A (v1) tạo, amount 15tr")
    deploy(2, 20_000_000, extra=True)     # v2: ngưỡng 20tr + thêm bước Kế toán
    b = start(2, {"amount": 15_000_000, "requester": "B"})
    print("deploy v2 (ngưỡng 20tr + bước kế toán); instance B (v2) tạo, amount 15tr")
    print("proc_def:", c.execute("select key,version,length(bpmn_xml),left(sha256,10) from proc_def order by version").fetchall())
    print("A hoàn tất (process mới, reload từ PG):", step(a, {}))
    print("B hoàn tất:", step(b, {}))
    print("ledger:", L)
    print("state A có spec nhúng; spec A có 'gd'?", 'ut_gd' in json.dumps(c.execute("select state->'spec'->'task_specs' from proc_instance where id=%s",[a]).fetchone()[0]),
          "| spec B có 'kt'?", 'ut_kt' in json.dumps(c.execute("select state->'spec'->'task_specs' from proc_instance where id=%s",[b]).fetchone()[0]))
    # poller SLA: instance C v1 có timer treo, worker poll theo next_due
    cc = start(1, {"amount": 1, "requester": "C"})
    print("C next_due =", c.execute("select next_due from proc_instance where id=%s", [cc]).fetchone()[0])
    time.sleep(2.3)
    due = c.execute("select id from proc_instance where not done and next_due <= now()").fetchall()
    print("poller tìm thấy instance quá hạn:", due)
    for (i,) in due:
        v, wf = load(i); wf.refresh_timers(); wf.do_engine_steps(); save(i, v, wf)
        print(f" instance {i}: READY sau poll =", [(t.task_spec.bpmn_name, t.task_spec.lane) for t in ready_user_tasks(wf)])
