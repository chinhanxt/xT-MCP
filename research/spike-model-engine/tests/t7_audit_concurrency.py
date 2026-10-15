# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xT contributors
"""8 luồng ghi song song cùng tenant: chuỗi audit vẫn tuyến tính; 2 tenant song song không chặn nhau."""

import threading, time, uuid
from common import *  # noqa
from xtmodel import planner as P
from xtmodel.crud import Actor, Engine

m = mig()
pool = app_pool(16)
eng = Engine(pool, P.load_current(m))
TC, TD = uuid.uuid4(), uuid.uuid4()
errs = []


def worker(tenant, i, n):
    a = Actor(tenant, uuid.uuid4(), ("staff",))
    for k in range(n):
        r = eng.create_record(
            a,
            "de_xuat_chi",
            {
                "noi_dung": f"song song {i}-{k}",
                "so_tien": 200000,
                "tu_ngay": "2026-01-01",
                "den_ngay": "2026-01-02",
            },
        )
        if not r["ok"]:
            errs.append(r)


for tenants, label in [([TC], "1 tenant x 8 luồng"), ([TC, TD], "2 tenant x 8 luồng")]:
    th = [threading.Thread(target=worker, args=(t, i, 150)) for t in tenants for i in range(8)]
    t0 = time.perf_counter()
    [x.start() for x in th]
    [x.join() for x in th]
    dt = time.perf_counter() - t0
    print(
        f"{label}: {150 * 8 * len(tenants)} bản ghi trong {dt:.2f}s = {150 * 8 * len(tenants) / dt:.0f} ghi/s, lỗi={len(errs)}"
    )
with psycopg.connect(SU) as c:
    for t in (TC, TD):
        print(
            "verify",
            str(t)[:8],
            c.execute("SELECT * FROM xt_audit.verify(%s)", (str(t),)).fetchone(),
        )
pool.close()
