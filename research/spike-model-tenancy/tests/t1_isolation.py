# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xT contributors
"""Cô lập: A (bảng chung + RLS) vs B1 (xt_data.t<seq>__<model>) vs B2 (schema xt_t<seq>), 2 tenant model KHÁC nhau."""
import copy, uuid
from common import *  # noqa
from examples import V1
from psycopg import sql
from xtmodel.crud import Actor, Engine

reset()
m = mig(); add_tenants(m, 2)
V2T = copy.deepcopy(V1)                     # tenant 2: model khác (thêm trường, bỏ email, thêm model du_an)
kh = V2T["models"][0]
kh["fields"] = [f for f in kh["fields"] if f["name"] != "email"] + [
    {"id": "f9", "name": "nganh_nghe", "type": "text", "label": "Ngành nghề"}]
V2T["models"].append({"id": "m7", "name": "du_an", "label": "Dự án", "fields": [
    {"id": "f1", "name": "ten_du_an", "type": "text", "label": "Tên dự án", "required": True},
    {"id": "f2", "name": "khach_hang", "type": "ref", "ref": "khach_hang", "label": "Khách hàng"}],
    "permissions": {"manager": {"actions": ["create", "read"], "scope": "all"}}})

print("### Áp dụng catalog")
apply_spec(m, V1, lay("A", 0), 0)
print("A : catalog chung v1 (tenant_seq=0) -> xt_data.khach_hang, xt_data.de_xuat_tam_ung")
for s, spec in ((1, V1), (2, V2T)):
    for v in ("B1", "B2"):
        apply_spec(m, spec, lay(v, s), s if v == "B1" else 100 + s)
print("B1: xt_data.t1__*, xt_data.t2__* | B2: xt_t1.*, xt_t2.* (catalog riêng mỗi tenant; B2 dùng tenant_seq 101/102 cho catalog)")
print("tenant 2 B1 cột khach_hang:", [r[0] for r in m.execute("SELECT column_name FROM information_schema.columns WHERE table_name='t2__khach_hang' AND ordinal_position>10 ORDER BY ordinal_position")])
print("tenant 1 B1 cột khach_hang:", [r[0] for r in m.execute("SELECT column_name FROM information_schema.columns WHERE table_name='t1__khach_hang' AND ordinal_position>10 ORDER BY ordinal_position")])

print("\n### B1 KHÔNG có tiền tố index -> va chạm tên index trong cùng schema")
from xtmodel.spec import Catalog
with using(C.Layout(table_prefix="t9__", index_prefix="")):
    try:
        with m.transaction(force_rollback=True):
            for st in C.create_model_steps(Catalog.model_validate(V1).models[0]):
                m.execute(st.sql)
    except psycopg.Error as e:
        print("  ->", type(e).__name__, e.diag.message_primary)
with using(C.Layout(table_prefix="t9999999__", index_prefix="t9999999_")):
    print("  tên index dài nhất (id 24+24 ký tự, seq 7 chữ số):", len(C.iname("ixr_" + "m" * 24 + "_" + "f" * 24)), "byte <= 63")

print("\n### Dữ liệu qua Engine (CRUD tổng quát chạy dưới layout của tenant)")
pool = ConnectionPool(APP, min_size=1, max_size=1, open=True)
ids = {}
for v in ("A", "B1", "B2"):
    for s, spec in ((1, V1), (2, V2T)):
        cat = Catalog.model_validate(spec if v != "A" else V1)
        e = Engine(pool, cat, lay(v, s))
        a = Actor(tid(s), uuid.UUID(int=s), ("manager",))
        data = {"ma_kh": "KH0001", "ten": f"Khách của tenant {s}"}
        if v != "A" and s == 2: data["nganh_nghe"] = "Xây dựng"
        r = e.create_record(a, "khach_hang", data)
        ids[(v, s)] = r["record"]["id"]
        dup = e.create_record(a, "khach_hang", data)
        if s == 1: print(f"  {v}: tạo KH0001 ok; trùng lần 2 -> {dup['error']['code']}: {dup['error']['message']}")
    if v != "A":
        e2 = Engine(pool, Catalog.model_validate(V2T), lay(v, 2))
        r = e2.create_record(Actor(tid(2), uuid.UUID(int=2), ("manager",)), "du_an", {"ten_du_an": "Dự án X", "khach_hang": ids[(v, 1)]})
        print(f"  {v}: tenant 2 tạo du_an trỏ khach_hang của tenant 1 -> {r['error']['code']}")
r = Engine(pool, Catalog.model_validate(V1)).create_record(Actor(tid(1), uuid.UUID(int=0xA1), ("staff",)), "de_xuat_tam_ung",
      {"noi_dung": "Công tác", "so_tien": 200000, "tu_ngay": "2026-01-01", "den_ngay": "2026-01-02", "khach_hang": ids[("A", 2)]})
print(f"  A : tenant 1 tạo đề xuất trỏ khach_hang của tenant 2 (cùng bảng) -> {r['error']['code']}")

print("\n### Tấn công/nhầm lẫn ở tầng SQL (xt_app, pool 1 kết nối)")
T = {"A": ("xt_data", "khach_hang"), "B1": ("xt_data", "t{}__khach_hang"), "B2": ("xt_t{}", "khach_hang")}
def tbl(v, s):
    sc, t = T[v]
    return sql.Identifier(sc.format(s), t.format(s))
with pool.connection() as c:   # BẪY: kết nối non-autocommit, execute ngoài transaction => transaction ngầm
    c.execute("SELECT 1")
    with c.transaction():        # thành SAVEPOINT, set_config(...,true) sống tới hết transaction NGẦM bên ngoài
        ctx(c, 1, roles="manager")
    print("  BẪY psycopg: sau 'with transaction()' lồng trong transaction ngầm, xt.tenant_id còn =",
          repr(c.execute("SELECT current_setting('xt.tenant_id', true)").fetchone()[0])[:14] + "...'",
          "| status:", c.info.transaction_status.name)
    c.rollback()
with psycopg.connect(APP, autocommit=True) as c:
    for v in ("A", "B1", "B2"):
        n0 = c.execute(sql.SQL("SELECT count(*) FROM {}").format(tbl(v, 1))).fetchone()[0]
        with c.transaction():
            ctx(c, 1, roles="manager")
            wrong = c.execute(sql.SQL("SELECT count(*) FROM {}").format(tbl(v, 2))).fetchone()[0]
            own = c.execute(sql.SQL("SELECT count(*) FROM {}").format(tbl(v, 1))).fetchone()[0]
        print(f"  {v:2s}: quên đặt tenant -> {n0} dòng | ngữ cảnh T1 đọc bảng của T2 (lỗi routing; A: cùng bảng) -> {wrong} | T1 đọc bảng mình -> {own}")
        try:
            with c.transaction():
                ctx(c, 1, roles="manager")
                c.execute(sql.SQL("INSERT INTO {} (tenant_id, ma_kh, ten) VALUES (%s, 'KH0009', 'chen cheo')").format(tbl(v, 1)), (str(tid(2)),))
        except psycopg.Error as e:
            print(f"      chèn tenant_id=T2 vào bảng T1 khi ngữ cảnh T1 -> {type(e).__name__}")
# B1 không RLS (chỉ dựa vào tên bảng): mô phỏng bằng cách tắt RLS trên bảng của T2
with su() as s_:
    s_.execute("ALTER TABLE xt_data.t2__khach_hang NO FORCE ROW LEVEL SECURITY"); s_.execute("ALTER TABLE xt_data.t2__khach_hang DISABLE ROW LEVEL SECURITY")
with pool.connection() as c, c.transaction():
    ctx(c, 1, roles="manager")
    print("  B1 KHÔNG RLS: ngữ cảnh T1 đọc nhầm bảng T2 ->", c.execute("SELECT count(*), max(ten) FROM xt_data.t2__khach_hang").fetchone(), "<- rò dữ liệu")
with su() as s_:
    s_.execute("ALTER TABLE xt_data.t2__khach_hang ENABLE ROW LEVEL SECURITY"); s_.execute("ALTER TABLE xt_data.t2__khach_hang FORCE ROW LEVEL SECURITY")
pool.close()
