import sys, time, json, psycopg; sys.path.insert(0, "src")
from psycopg.types.json import Jsonb
from xtflow import *
DSN = "postgresql://postgres:x@localhost:15432/spike"
XML = open("src/tamung_v1.bpmn").read()
def ghi_so(amount, requester): return {"so": "PC-9999", "amount": amount}
def notify(msg): print("   [notify]", msg); return {"sent": True}
eng = lambda: safe_engine({"ghi_so": ghi_so, "notify": notify})
step = sys.argv[1]
with psycopg.connect(DSN, autocommit=True) as c:
    if step == "save":
        c.execute("create table if not exists wf_instance(id serial primary key, process_id text, state jsonb not null, updated timestamptz default now())")
        wf = new_instance(XML, "tamung", eng(), {"amount": 50_000_000, "requester": "nguyenvana"})
        raw = SER.serialize_json(wf)
        print("size bytes (chưa bỏ spec):", len(raw.encode()))
        d = json.loads(raw); print("keys:", list(d)); 
        print("size spec:", len(json.dumps(d["spec"])), "| tasks:", len(d["tasks"]), "| size tasks:", len(json.dumps(d["tasks"])))
        # tách spec ra khỏi instance (lưu 1 lần theo version)
        inst = {k: v for k, v in d.items() if k not in ("spec", "subprocess_specs")}
        print("size instance không spec:", len(json.dumps(inst)))
        print("tên pk:", c.execute("insert into wf_instance(process_id,state) values ('tamung',%s) returning id", [Jsonb(d)]).fetchone())
    elif step == "load":
        row = c.execute("select id, state from wf_instance order by id desc limit 1").fetchone()
        print("pg jsonb size:", c.execute("select pg_column_size(state), octet_length(state::text) from wf_instance where id=%s", [row[0]]).fetchone())
        wf = from_state(row[1], eng())
        print(" reloaded READY:", [(t.task_spec.name, t.task_spec.lane) for t in ready_user_tasks(wf)])
        complete(wf, ready_user_tasks(wf)[0], {"tp_decision": "approve"}); print(" READY:", [t.task_spec.name for t in ready_user_tasks(wf)])
        c.execute("update wf_instance set state=%s where id=%s", [Jsonb(to_state(wf)), row[0]])
    elif step == "load2":
        row = c.execute("select id, state from wf_instance order by id desc limit 1").fetchone()
        wf = from_state(row[1], eng())
        complete(wf, ready_user_tasks(wf)[0], {"gd_decision": "approve"}); print(" completed:", wf.is_completed(), wf.data)
    elif step == "timer_save":
        wf = new_instance(XML, "tamung", eng(), {"amount": 1, "requester": "t"})
        c.execute("insert into wf_instance(process_id,state) values ('tamung_timer',%s)", [Jsonb(to_state(wf))]); print("saved with pending timer", time.time())
    elif step == "timer_load":
        row = c.execute("select state from wf_instance where process_id='tamung_timer' order by id desc limit 1").fetchone()
        wf = from_state(row[0], eng())
        wf.refresh_timers(); wf.do_engine_steps()
        print(" timer sau reload ở process mới -> READY:", [t.task_spec.name for t in ready_user_tasks(wf)])
