# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xT contributors
"""Chi phí runtime của catalog lớn (chạy sau t3_bloat): relcache/kết nối, lock khi ALTER hàng loạt."""
from common import *  # noqa
from psycopg import sql

N, NM = 50, 30
with su() as x:
    x.execute("GRANT pg_read_all_stats TO xt_app")   # chỉ để đo pg_backend_memory_contexts

def cache_kb(c):
    return c.execute("SELECT sum(total_bytes)/1024 FROM pg_backend_memory_contexts WHERE name IN ('CacheMemoryContext') OR parent = 'CacheMemoryContext'").fetchone()[0]
for label, tables in [("1 tenant (30 bảng B1)", [f"t7__model_{i:02d}" for i in range(NM)]),
                      ("A (30 bảng chung)", [f"model_{i:02d}" for i in range(NM)]),
                      ("toàn bộ 1500 bảng B1", [f"t{s}__model_{i:02d}" for s in range(1, N + 1) for i in range(NM)])]:
    with psycopg.connect(APP, autocommit=True) as c:
        k0 = cache_kb(c)
        t = time.perf_counter()
        try:
            with c.transaction():
                ctx(c, 7)
                for tb in tables:
                    c.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier("xt_data", tb)))
            note = "1 transaction"
        except psycopg.Error as e:
            print(f"  {label}: đọc trong 1 transaction -> {type(e).__name__}: {e.diag.message_primary} ({e.diag.message_hint})")
            t = time.perf_counter()
            for tb in tables:   # mỗi câu 1 transaction (autocommit) — không giữ khóa tích lũy
                with c.transaction():
                    ctx(c, 7)
                    c.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier("xt_data", tb)))
            note = "mỗi bảng 1 transaction"
        t1 = time.perf_counter() - t
        t = time.perf_counter()
        for tb in tables:
            with c.transaction():
                ctx(c, 7)
                c.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier("xt_data", tb)))
        t2 = time.perf_counter() - t
        print(f"kết nối mới, {label:24s}: lần đầu {t1*1000:7.0f} ms ({note}), lần 2 {t2*1000:6.0f} ms; relcache {cache_kb(c)-k0:7.0f} KB/kết nối")

print("\nALTER cả 1500 bảng B1 trong MỘT transaction (max_locks_per_transaction=64, max_connections=100):")
c = psycopg.connect(MIG)
t = time.perf_counter()
try:
    with c.transaction():
        for s in range(1, N + 1):
            for i in range(NM):
                c.execute(sql.SQL("ALTER TABLE {} ADD COLUMN v2 text").format(sql.Identifier("xt_data", f"t{s}__model_{i:02d}")))
        n = c.execute("SELECT count(*) FROM pg_locks WHERE pid = pg_backend_pid()").fetchone()[0]
        print(f"  ok {(time.perf_counter()-t):.1f}s, giữ {n} khóa")
        raise RuntimeError("rollback")
except psycopg.Error as e:
    print(f"  -> {type(e).__name__}: {e.diag.message_primary} | {e.diag.message_hint} (sau {(time.perf_counter()-t):.1f}s)")
except RuntimeError:
    pass
c.close()
