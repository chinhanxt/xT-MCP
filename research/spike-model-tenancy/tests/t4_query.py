# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xT contributors
"""Hiệu năng truy vấn có/không RLS, A (100k dòng chung) vs B1 (2k dòng/bảng). Chạy ngay sau t2."""
import uuid
from common import *  # noqa
from psycopg import sql

S = 7
OWNER = uuid.UUID(int=S * 1000 + 3)
TBL = {"A": sql.Identifier("xt_data", "de_xuat_tam_ung"), "B1": sql.Identifier("xt_data", f"t{S}__de_xuat_tam_ung")}
Q = {
    "trang 20 dòng mới nhất": ("SELECT * FROM {t} WHERE {w} ORDER BY created_at DESC, id LIMIT 20", "true"),
    "đếm so_tien >= 50tr": ("SELECT count(*) FROM {t} WHERE {w} AND so_tien >= 50000000", "true"),
    "staff own: đếm + trang": ("SELECT * FROM {t} WHERE {w} AND owner_id = %(o)s ORDER BY created_at DESC, id LIMIT 20", "true"),
    "tổng tiền theo trạng thái": ("SELECT trang_thai, sum(so_tien) FROM {t} WHERE {w} GROUP BY 1", "true"),
}
app = psycopg.connect(APP); mg = psycopg.connect(MIG)
print(f"tenant {S}: A bảng chung {mg.execute('SELECT count(*) FROM xt_data.de_xuat_tam_ung').fetchone()[0]} dòng (tenant {S}: "
      f"{mg.execute('SELECT count(*) FROM xt_data.de_xuat_tam_ung WHERE tenant_id=%s', (tid(S),)).fetchone()[0]}) | B1 bảng riêng "
      f"{mg.execute(sql.SQL('SELECT count(*) FROM {}').format(TBL['B1'])).fetchone()[0]} dòng")
print(f"{'truy vấn':28s} {'A+RLS':>8s} {'A noRLS':>8s} {'B1+RLS':>8s} {'B1 noRLS':>9s}  (ms, median 30 lần)")
for name, (q, _) in Q.items():
    row = []
    for v in ("A", "B1"):
        for rls in (True, False):
            conn = app if rls else mg
            w = sql.SQL("true") if (rls or v == "B1") else sql.SQL("tenant_id = %(t)s")
            stmt = sql.SQL(q).format(t=TBL[v], w=w)
            args = {"o": OWNER, "t": tid(S)}
            def run():
                with conn.transaction():
                    if rls:
                        ctx(conn, S, actor=OWNER, roles="staff" if "own" in name else "accountant")
                    conn.execute(stmt, args).fetchall()
            run(); row.append(med(run, 30))
    print(f"{name:28s} " + " ".join(f"{x:8.2f}" for x in row))
with app.transaction():
    ctx(app, S, roles="accountant")
    print("EXPLAIN A+RLS 'đếm so_tien >= 50tr':")
    for r in app.execute("EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF) SELECT count(*) FROM xt_data.de_xuat_tam_ung WHERE so_tien >= 50000000").fetchall():
        if "Filter" not in r[0]: print("   ", r[0])
