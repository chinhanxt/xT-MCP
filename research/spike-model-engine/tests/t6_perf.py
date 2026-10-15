# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xT contributors
"""Hiệu năng: 50 model x 30 trường (compile/plan/apply) + 100k dòng tìm kiếm với RLS."""

import random, statistics, time, uuid
from common import *  # noqa
from xtmodel.spec import Catalog
from xtmodel import planner as P, compiler as C
from xtmodel.crud import Actor, Engine

m = mig()
cur = P.load_current(m)
base = cur.model_dump(mode="json", by_alias=True)
TYPES = ["text", "number", "money", "date", "datetime", "bool", "enum", "user", "file", "json"]
models = []
for i in range(50):
    fields = []
    for j in range(30):
        t = TYPES[j % len(TYPES)]
        f = {"id": f"f{j}", "name": f"truong_{j}", "type": t, "label": f"Trường {j}"}
        if t == "enum":
            f["options"] = [{"value": f"v{k}", "label": f"Giá trị {k}"} for k in range(5)]
        if t == "text" and j % 3 == 0:
            f.update(min=1, max=200)
        if t == "money":
            f.update(min=0)
        if j == 0:
            f.update(required=True, unique=True)
        fields.append(f)
    if i > 0:
        fields[-1] = {
            "id": "f29",
            "name": "cha",
            "type": "ref",
            "ref": f"perf_{i - 1:02d}",
            "label": "Cha",
        }
    models.append(
        {
            "id": f"p{i:02d}",
            "name": f"perf_{i:02d}",
            "label": f"Model hiệu năng {i}",
            "fields": fields,
            "indexes": [{"id": "i1", "fields": ["truong_1", "truong_3"]}],
            "rules": [
                {
                    "id": "r1",
                    "message": "t3 <= t13",
                    "check": {"field": "truong_3", "op": "<=", "other_field": "truong_13"},
                }
            ],
            "permissions": {
                "staff": {"actions": ["create", "read"], "scope": "own"},
                "manager": {"actions": ["read", "update"], "scope": "all"},
            },
        }
    )
spec = {"version": cur.version + 1, "models": base["models"] + models}

t = time.perf_counter()
cat = Catalog.model_validate(spec)
t_val = time.perf_counter() - t
t = time.perf_counter()
steps = [s.text() for mm in cat.models for s in C.create_model_steps(mm)]
t_comp = time.perf_counter() - t
t = time.perf_counter()
p = P.plan(cur, cat, m)
t_plan = time.perf_counter() - t
t = time.perf_counter()
r = P.apply(m, cat, p.hash, set(), "agent:perf", "user:perf")
t_apply = time.perf_counter() - t
print(
    f"50 model x 30 trường (+2 model cũ): validate {t_val * 1000:.0f} ms | compile {len(steps)} câu DDL {t_comp * 1000:.0f} ms | "
    f"plan(diff, đếm) {t_plan * 1000:.0f} ms | apply 1 transaction {r['tx_steps']} câu {t_apply * 1000:.0f} ms | DDL {sum(map(len, steps)) / 1024:.0f} KiB"
)

# ------------------------------------------------------- 100k dòng ----
TA, TB = uuid.UUID(int=0xA), uuid.UUID(int=0xB)
depts = [uuid.UUID(int=0xD00 + k) for k in range(5)]
staff = [(uuid.UUID(int=0x5000 + k), depts[k % 5]) for k in range(50)]
pool = app_pool(4)
eng = Engine(pool, cat)
with psycopg.connect(APP) as c:
    try:
        with c.transaction():
            c.execute("SELECT set_config('xt.tenant_id', %s, true)", (str(TA),))
            with c.cursor().copy(
                "COPY xt_data.de_xuat_chi (noi_dung, so_tien, tu_ngay, den_ngay) FROM STDIN"
            ) as cp:
                cp.write_row(("abcde", 200000, "2026-01-01", "2026-01-02"))
    except psycopg.Error as e:
        print("COPY FROM dưới RLS ->", type(e).__name__, e.diag.message_primary)

random.seed(7)
PLACES = ["Đà Nẵng", "Hà Nội", "Hải Phòng", "Cần Thơ", "Huế", "Nha Trang"]


def load(tenant, n, people):
    """Nạp bằng xt_app qua RLS + trigger audit (đường ghi thật), executemany theo owner."""
    t = time.perf_counter()
    groups = {}
    for k in range(n):
        groups.setdefault(people[k % len(people)], []).append(k)
    with pool.connection() as c:
        for (actor, dept), ks in groups.items():
            rows = []
            for k in ks:
                d = random.randint(0, 330)
                amt = random.randrange(1, 400) * 100000
                st = random.choice(["nhap", "cho_duyet", "da_duyet"])
                if st == "da_duyet" and amt > 50_000_000:
                    st = "cho_duyet"
                day = f"2026-{1 + d // 28 % 12:02d}-{1 + d % 28:02d}"
                rows.append(
                    (f"Công tác {random.choice(PLACES)} đợt {k}", amt, day, day, st, str(dept))
                )
            with c.transaction(), c.cursor() as cu:
                cu.execute(
                    "SELECT set_config('xt.tenant_id', %s, true), set_config('xt.actor_id', %s, true), "
                    "set_config('xt.roles', 'staff', true)",
                    (str(tenant), str(actor)),
                )
                cu.executemany(
                    "INSERT INTO xt_data.de_xuat_chi (noi_dung, so_tien, tu_ngay, den_ngay, trang_thai, department_id) "
                    "VALUES (%s,%s,%s,%s,%s,%s)",
                    rows,
                )
    return time.perf_counter() - t


dA = load(TA, 80_000, staff[:40])
dB = load(TB, 20_000, staff[40:])
su = psycopg.connect(SU, autocommit=True)
su.execute("ANALYZE xt_data.de_xuat_chi")
su.execute("ANALYZE xt_audit.event")
print(
    f"nạp 100k dòng qua xt_app (RLS + audit hash-chain): {dA + dB:.1f}s = {100_000 / (dA + dB):.0f} dòng/s; "
    f"tổng dòng {m.execute('SELECT count(*) FROM xt_data.de_xuat_chi').fetchone()[0]}, "
    f"audit {su.execute('SELECT count(*) FROM xt_audit.event').fetchone()[0]}"
)

s0 = staff[0]
cases = [
    (
        "staff own, mặc định (created_at desc)",
        Actor(TA, s0[0], ("staff",), department_ids=(s0[1],)),
        None,
        None,
    ),
    (
        "manager department, trạng thái + tiền >= 10tr",
        Actor(TA, uuid.uuid4(), ("manager",), department_ids=(depts[0],)),
        {
            "and": [
                {"field": "trang_thai", "op": "=", "value": "cho_duyet"},
                {"field": "so_tien", "op": ">=", "value": 10_000_000},
            ]
        },
        [{"field": "so_tien", "dir": "desc"}],
    ),
    (
        "accountant all tenant A, contains 'đà nẵng'",
        Actor(TA, uuid.uuid4(), ("accountant",)),
        {"field": "noi_dung", "op": "contains", "value": "đà nẵng"},
        None,
    ),
    (
        "accountant all tenant A, khoảng ngày + OR",
        Actor(TA, uuid.uuid4(), ("accountant",)),
        {
            "and": [
                {"field": "tu_ngay", "op": ">=", "value": "2026-03-01"},
                {"field": "tu_ngay", "op": "<", "value": "2026-04-01"},
                {
                    "or": [
                        {"field": "trang_thai", "op": "=", "value": "da_duyet"},
                        {"field": "khan", "op": "=", "value": True},
                    ]
                },
            ]
        },
        [{"field": "tu_ngay", "dir": "asc"}],
    ),
    ("accountant all tenant B, không lọc", Actor(TB, uuid.uuid4(), ("accountant",)), None, None),
]
for label, a, f, srt in cases:
    ts = []
    for _ in range(7):
        t = time.perf_counter()
        r = eng.search_records(a, "de_xuat_chi", f, srt, limit=20, offset=40)
        ts.append(time.perf_counter() - t)
    print(
        f"  {label:48s} total={r['total']:6d} items={len(r['items'])} median={statistics.median(ts) * 1000:6.1f} ms (count + page)"
    )

with pool.connection() as c:
    with c.transaction():
        c.execute(
            "SELECT set_config('xt.tenant_id', %s, true), set_config('xt.actor_id', %s, true), set_config('xt.roles','manager',true), "
            "set_config('xt.department_ids', %s, true)",
            (str(TA), str(uuid.uuid4()), str(depts[0])),
        )
        plan = c.execute(
            "EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF, SUMMARY ON) SELECT count(*) FROM xt_data.de_xuat_chi "
            "WHERE trang_thai = 'cho_duyet' AND so_tien >= 10000000"
        ).fetchall()
        print("EXPLAIN manager/department count:")
        [print("   ", r[0]) for r in plan]
    with c.transaction():
        c.execute(
            "SELECT set_config('xt.tenant_id', %s, true), set_config('xt.actor_id', %s, true), set_config('xt.roles','staff',true)",
            (str(TA), str(s0[0])),
        )
        plan = c.execute(
            "EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF, SUMMARY ON) SELECT * FROM xt_data.de_xuat_chi ORDER BY created_at DESC, id LIMIT 20"
        ).fetchall()
        print("EXPLAIN staff own page:")
        [print("   ", r[0]) for r in plan]
with pool.connection() as c:
    with c.transaction():
        c.execute("SELECT set_config('xt.tenant_id', %s, true)", (str(TA),))
        t = time.perf_counter()
        v = c.execute("SELECT * FROM xt_audit.verify(%s)", (str(TA),)).fetchone()
        print(f"verify chuỗi audit tenant A: {v} trong {(time.perf_counter() - t) * 1000:.0f} ms")
pool.close()
