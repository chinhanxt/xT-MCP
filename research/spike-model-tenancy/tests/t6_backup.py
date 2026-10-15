# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xT contributors
"""Sao lưu/khôi phục MỘT tenant: B1 (pg_dump -t 'xt_data.t7__*') vs A (COPY lọc tenant_id). Chạy sau t3/t5."""
import hashlib, subprocess
from common import *  # noqa
from psycopg import sql

NT, NM, ROWS = 10, 30, 1000
x = su()
t = time.perf_counter()
x.execute("SET session_replication_role = replica")   # nạp nhanh: bỏ trigger audit/FK (chỉ superuser)
for s in range(1, NT + 1):
    for i in range(NM):
        for tb in (f"model_{i:02d}", f"t{s}__model_{i:02d}"):
            x.execute(sql.SQL("INSERT INTO {} (tenant_id, truong_0, truong_1, truong_2, truong_3) "
                              "SELECT %s, 'k' || g, g * 1.5, g * 1000, date '2026-01-01' + (g %% 300) FROM generate_series(1, %s) g "
                              "ON CONFLICT DO NOTHING").format(sql.Identifier("xt_data", tb)), (tid(s), ROWS))
x.execute("RESET session_replication_role")
print(f"nạp {NT} tenant x {NM} model x {ROWS} dòng cho A và B1: {time.perf_counter()-t:.1f}s")

def digest(variant, s):
    h = hashlib.sha256(); n = 0
    for i in range(NM):
        T = sql.Identifier("xt_data", f"model_{i:02d}" if variant == "A" else f"t{s}__model_{i:02d}")
        for r in x.execute(sql.SQL("SELECT id, truong_0, truong_1, truong_2, truong_3 FROM {} WHERE tenant_id = %s ORDER BY id").format(T), (tid(s),)):
            h.update(repr(r).encode()); n += 1
    return n, h.hexdigest()[:12]

S = 7
print("\n### B1: pg_dump -Fc -t 'xt_data.t7__*'")
before = digest("B1", S)
t = time.perf_counter()
dump = subprocess.run(["docker", "exec", CONTAINER, "pg_dump", "-U", "xt_owner", "-Fc", "-t", f"xt_data.t{S}__*", "xt"], capture_output=True, check=True).stdout
td = time.perf_counter() - t
x.execute(sql.SQL("UPDATE {} SET truong_0 = 'HỎNG' || id").format(sql.Identifier("xt_data", f"t{S}__model_05")))   # sự cố
t = time.perf_counter()
x.execute("DROP TABLE " + ", ".join(sql.Identifier("xt_data", f"t{S}__model_{i:02d}").as_string() for i in range(NM)) + " CASCADE")
subprocess.run(["docker", "exec", "-i", CONTAINER, "pg_restore", "-U", "xt_owner", "-d", "xt", "--exit-on-error"], input=dump, capture_output=True, check=True)
tr = time.perf_counter() - t
after = digest("B1", S)
pol = x.execute("SELECT count(*) FROM pg_policies WHERE tablename LIKE %s", (f"t{S}\\_\\_%",)).fetchone()[0]
trg = x.execute("SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal AND tgrelid::regclass::text LIKE %s", (f"xt_data.t{S}\\_\\_%",)).fetchone()[0]
acl = x.execute("SELECT has_column_privilege('xt_app', %s, 'truong_1', 'UPDATE'), has_table_privilege('xt_app', %s, 'UPDATE')",
                (f"xt_data.t{S}__model_00",) * 2).fetchone()
print(f"  dump {td:.2f}s, {len(dump)/1e6:.1f} MB | xóa+restore {tr:.2f}s | dữ liệu trước {before} sau {after} -> {'KHỚP' if before == after else 'LỆCH'}")
print(f"  sau restore: policy={pol}, trigger={trg}, quyền cột xt_app (UPDATE truong_1, UPDATE cả bảng)={acl} | tenant khác không bị khóa (chỉ DROP bảng của t{S})")

print("\n### A: COPY (SELECT … WHERE tenant_id = T7) cho từng model, khôi phục = DELETE + COPY")
before = digest("A", S)
t = time.perf_counter()
dumps = {}
for i in range(NM):
    T = sql.Identifier("xt_data", f"model_{i:02d}")
    with x.cursor().copy(sql.SQL("COPY (SELECT * FROM {} WHERE tenant_id = {}) TO STDOUT (FORMAT binary)").format(T, sql.Literal(str(tid(S))))) as cp:
        dumps[i] = b"".join(bytes(b) for b in cp)
td = time.perf_counter() - t
x.execute(sql.SQL("UPDATE xt_data.model_05 SET truong_0 = 'HỎNG' || id WHERE tenant_id = %s"), (tid(S),))
t = time.perf_counter()
with x.transaction():
    x.execute("SET LOCAL session_replication_role = replica")   # không thì trigger audit ghi 60k sự kiện
    for i in reversed(range(NM)):
        x.execute(sql.SQL("DELETE FROM {} WHERE tenant_id = %s").format(sql.Identifier("xt_data", f"model_{i:02d}")), (tid(S),))
    for i in range(NM):
        with x.cursor().copy(sql.SQL("COPY {} FROM STDIN (FORMAT binary)").format(sql.Identifier("xt_data", f"model_{i:02d}"))) as cp:
            cp.write(dumps[i])
tr = time.perf_counter() - t
after = digest("A", S)
print(f"  export {td:.2f}s, {sum(map(len, dumps.values()))/1e6:.1f} MB | DELETE+COPY {tr:.2f}s | trước {before} sau {after} -> {'KHỚP' if before == after else 'LỆCH'}")
print("  lưu ý: cần superuser (session_replication_role) hoặc chịu trigger audit; DELETE để lại dead tuple trong bảng chung (cần VACUUM);")
print("  schema/policy/quyền KHÔNG nằm trong bản sao lưu tenant (là của chung), khôi phục về một phiên bản spec cũ không làm được riêng 1 tenant")
with x.transaction():
    x.execute("SELECT 1")
st = x.execute("SELECT n_dead_tup FROM pg_stat_user_tables WHERE relname = 'model_05' AND schemaname = 'xt_data'").fetchone()
print("  dead tuple model_05 sau khôi phục A:", st[0] if st else None)
