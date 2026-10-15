# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xT contributors
"""Migration 1 model trên 50 tenant: A (1 bảng chung 100k dòng) vs B1 (50 bảng x 2k dòng).
Đo thời gian, độ trễ ghi đồng thời, lock_timeout, lỗi cục bộ, pha 2 bằng hàng đợi apply_job."""
import copy, io, random, threading, uuid
from common import *  # noqa
from examples import V1
from psycopg import sql

N, ROWS = 50, 2000
reset()
m = mig(); add_tenants(m, N)
V1b = copy.deepcopy(V1)
t = time.perf_counter(); apply_spec(m, V1b, lay("A", 0), 0); tA = time.perf_counter() - t
t = time.perf_counter()
for s in range(1, N + 1):
    apply_spec(m, V1b, lay("B1", s), s)
tB = time.perf_counter() - t
print(f"Cấp phát v1: A {tA*1000:.0f} ms (1 lần) | B1 {tB*1000:.0f} ms cho {N} tenant ({tB/N*1000:.0f} ms/tenant)")

def tbl(v, s):
    return sql.Identifier("xt_data", "de_xuat_tam_ung" if v == "A" else f"t{s}__de_xuat_tam_ung")

random.seed(1)
def rows(s):
    out = io.StringIO()
    for k in range(ROWS):
        amt = 400_000_000 if (s == 37 and k == 0) else random.randrange(1, 900) * 100000
        st = random.choice(["nhap", "cho_duyet"])
        out.write(f"{tid(s)}\t{uuid.UUID(int=s*1000 + k % 20)}\tCông tác đợt {k}\t{amt}\t2026-03-01\t2026-03-02\t{st}\n")
    return out.getvalue()
t = time.perf_counter()
for v in ("A", "B1"):
    for s in range(1, N + 1):
        T = tbl(v, s)
        if v == "B1" or s == 1:
            m.execute(sql.SQL("ALTER TABLE {} DISABLE TRIGGER xt_audit").format(T))
        with m.cursor().copy(sql.SQL("COPY {} (tenant_id, owner_id, noi_dung, so_tien, tu_ngay, den_ngay, trang_thai) FROM STDIN").format(T)) as cp:
            cp.write(rows(s))
        if v == "B1" or s == N:
            m.execute(sql.SQL("ALTER TABLE {} ENABLE TRIGGER xt_audit").format(T))
with su() as x:
    x.execute("ANALYZE")
print(f"Nạp {N*ROWS} dòng cho mỗi biến thể (COPY bằng xt_migrator, tắt audit khi nạp): {time.perf_counter()-t:.1f}s; "
      f"tenant 37 có 1 dòng 400tr (sẽ vi phạm max mới 300tr)")

V2b = copy.deepcopy(V1b); V2b["version"] = 2
dx = V2b["models"][1]
for f in dx["fields"]:
    if f["name"] == "so_tien": f["max"] = 300_000_000
dx["fields"] += [{"id": "f20", "name": "ma_du_an", "type": "text", "label": "Mã dự án"},
                 {"id": "f21", "name": "kenh", "type": "enum", "label": "Kênh", "required": True, "default": "web",
                  "options": [{"value": "web", "label": "Web"}, {"value": "chat", "label": "Chat"}]}]
dx["indexes"].append({"id": "i2", "fields": ["so_tien"]})


class Writer(threading.Thread):
    """Ghi liên tục vào tenant 5 bằng xt_app (staff) để đo độ trễ khi migration chạy."""
    def __init__(self, v):
        super().__init__(daemon=True); self.v, self.stop, self.lat, self.err = v, False, [], 0
    def run(self):
        with psycopg.connect(APP, autocommit=True) as c:
            while not self.stop:
                t0 = time.perf_counter()
                try:
                    with c.transaction():
                        ctx(c, 5, actor=uuid.UUID(int=5000), roles="staff")
                        c.execute("SET LOCAL lock_timeout = '10s'")
                        c.execute(sql.SQL("INSERT INTO {} (noi_dung, so_tien, tu_ngay, den_ngay) VALUES ('ghi song song', 200000, '2026-01-01', '2026-01-02')").format(tbl(self.v, 5)))
                except psycopg.Error:
                    self.err += 1
                self.lat.append((time.perf_counter() - t0) * 1000)
                time.sleep(0.005)
    def report(self):
        l = sorted(self.lat)
        return f"ghi tenant 5: {len(l)} lệnh, p50 {l[len(l)//2]:.1f} ms, p99 {l[int(len(l)*.99)]:.1f} ms, max {l[-1]:.0f} ms, lỗi {self.err}"


def holder(v, s):
    h = psycopg.connect(SU)
    h.execute(sql.SQL("SELECT count(*) FROM {}").format(tbl(v, s)))   # giữ ACCESS SHARE tới khi rollback
    return h

print("\n### A: một bảng chung")
try:
    apply_spec(m, V2b, lay("A", 0), 0, defer_post=True)
except Exception as e:
    print(f"  lần 1: {type(e).__name__}: {str(e)[:120]} -> 1 dòng của tenant 37 chặn cả 50 tenant")
m.execute("UPDATE xt_data.de_xuat_tam_ung SET so_tien = 300000000 WHERE so_tien > 300000000")
h = holder("A", 12)
t = time.perf_counter()
try:
    apply_spec(m, V2b, lay("A", 0), 0, defer_post=True, lock_timeout="2s")
except Exception as e:
    print(f"  lần 2 (một giao dịch đọc dài của tenant 12 đang mở): {type(e).__name__}: {getattr(getattr(e,'diag',None),'message_primary',e)} sau {time.perf_counter()-t:.1f}s -> KHÔNG tenant nào được nâng cấp")
h.rollback(); h.close()
w = Writer("A"); w.start(); time.sleep(0.3)
t = time.perf_counter(); p, r = apply_spec(m, V2b, lay("A", 0), 0, defer_post=True); t1 = time.perf_counter() - t
t = time.perf_counter(); js = P.run_jobs(m); t2 = time.perf_counter() - t
time.sleep(0.3); w.stop = True; w.join()
print(f"  lần 3 (đã sửa dữ liệu): pha 1 {t1*1000:.0f} ms ({r['tx_steps']} câu, {r['queued']} job) | pha 2 {t2*1000:.0f} ms {js} | {w.report()}")

print("\n### B1: 50 bảng, mỗi tenant 1 transaction pha 1 + job pha 2")
with su() as x:   # trả lại dòng vi phạm cho tenant 37 ở B1 (A đã sửa ở trên)
    pass
h = holder("B1", 12)
w = Writer("B1"); w.start(); time.sleep(0.3)
res, per = {}, []
t = time.perf_counter()
for s in range(1, N + 1):
    t0 = time.perf_counter()
    try:
        apply_spec(m, V2b, lay("B1", s), s, defer_post=True, lock_timeout="2s")
        res[s] = "ok"
    except P.ApplyError as e:
        res[s] = "BLOCKED: " + str(e)[:70]
    except psycopg.Error as e:
        res[s] = type(e).__name__
    per.append((time.perf_counter() - t0) * 1000)
t1 = time.perf_counter() - t
h.rollback(); h.close()
ok = [s for s, v in res.items() if v == "ok"]
print(f"  pha 1: {t1*1000:.0f} ms tổng, p50 {sorted(per)[N//2]:.0f} ms/tenant | ok {len(ok)}/{N} | lỗi: { {s: v for s, v in res.items() if v != 'ok'} }")
h = holder("B1", 20)
seen = []
def mon():
    with psycopg.connect(SU, autocommit=True) as x:
        for _ in range(300):
            r = x.execute("SELECT a.pid, left(a.query, 70), pg_blocking_pids(a.pid), (SELECT left(query,50) FROM pg_stat_activity b WHERE b.pid = (pg_blocking_pids(a.pid))[1]) "
                          "FROM pg_stat_activity a WHERE cardinality(pg_blocking_pids(a.pid)) > 0").fetchall()
            if r: seen.append(r); return
            time.sleep(0.01)
threading.Thread(target=mon, daemon=True).start()
t = time.perf_counter(); js = P.run_jobs(m, lock_timeout="1s"); t2 = time.perf_counter() - t
print("   bị chặn bởi:", seen[:1])
h.rollback(); h.close()
print(f"  pha 2 lượt 1 (tenant 20 bị giữ khóa): {t2*1000:.0f} ms {js}")
print("   job lỗi:", m.execute("SELECT tenant_seq, op_id, status, attempts, left(last_error, 60) FROM xt_meta.apply_job WHERE status <> 'done' AND tenant_seq > 0 ORDER BY id LIMIT 3").fetchall())
js = P.run_jobs(m); print("  pha 2 lượt 2 (retry):", js)
t = time.perf_counter(); apply_spec(m, V2b, lay("B1", 12), 12, defer_post=True); P.run_jobs(m)
print(f"  retry tenant 12 sau khi khóa nhả: ok trong {(time.perf_counter()-t)*1000:.0f} ms")
time.sleep(0.3); w.stop = True; w.join()
print(" ", w.report())
print("  phân bố phiên bản catalog B1:", m.execute("SELECT v, count(*) FROM (SELECT tenant_seq, max(version) v FROM xt_meta.catalog_version WHERE tenant_seq > 0 GROUP BY 1) x GROUP BY 1 ORDER BY 1").fetchall(),
      "| index invalid:", m.execute("SELECT count(*) FROM pg_index WHERE NOT indisvalid").fetchone()[0],
      "| constraint NOT VALID:", m.execute("SELECT count(*) FROM pg_constraint WHERE NOT convalidated AND connamespace='xt_data'::regnamespace").fetchone()[0])

print("\n### B1: thêm 1 cột cho cả 50 tenant trong MỘT transaction (nguyên tử)")
from xtmodel.spec import Catalog
c = psycopg.connect(MIG)
t = time.perf_counter()
with c.transaction():
    c.execute("SET LOCAL lock_timeout = '3s'")
    for s in ok + [12]:
        with using(lay("B1", s)):
            c.execute(sql.SQL("ALTER TABLE {} ADD COLUMN ghi_chu_v3 text").format(tbl("B1", s)))
    nlocks = c.execute("SELECT count(*) FROM pg_locks WHERE pid = pg_backend_pid()").fetchone()[0]
print(f"  {len(ok)+1} ALTER trong 1 transaction: {(time.perf_counter()-t)*1000:.0f} ms, giữ {nlocks} khóa đồng thời (max_locks_per_transaction mặc định 64 x 100 kết nối)")
c.close()
