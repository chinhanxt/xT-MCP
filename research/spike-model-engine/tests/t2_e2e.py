# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xT contributors
"""E2E: plan+apply v1, CRUD + poka-yoke, cô lập tenant, phạm vi own/department, OCC, audit hash-chain."""

import uuid
from common import *  # noqa
from examples import V1
from xtmodel.spec import Catalog
from xtmodel import planner as P
from xtmodel.crud import Actor, Engine

m = mig()
cat = Catalog.model_validate(V1)
p = P.plan(P.load_current(m), cat, m, rehearse=True)
print(p.render_vi())
res = P.apply(m, cat, p.hash, set(), proposed_by="agent:claude", approved_by="user:giam_doc")
print(
    "APPLY:", {k: res[k] for k in ("version", "plan_hash", "tx_steps")}, "post:", len(res["post"])
)

pool = app_pool(1)  # pool 1 kết nối: ép tái sử dụng giữa các tenant
eng = Engine(pool, cat)
TA, TB = uuid.UUID(int=0xA), uuid.UUID(int=0xB)
D1, D2 = uuid.uuid4(), uuid.uuid4()
staff1 = Actor(TA, uuid.UUID(int=0xA1), ("staff",), department_ids=(D1,))
staff2 = Actor(TA, uuid.UUID(int=0xA2), ("staff",), department_ids=(D1,))
staff9 = Actor(TA, uuid.UUID(int=0xA9), ("staff",), department_ids=(D2,))
mgrA = Actor(TA, uuid.UUID(int=0xA3), ("manager",), department_ids=(D1,))
acct = Actor(TA, uuid.UUID(int=0xA4), ("accountant",))
mgrB = Actor(TB, uuid.UUID(int=0xB3), ("manager",))
staffB = Actor(TB, uuid.UUID(int=0xB1), ("staff",))

print("\n### CRUD + poka-yoke")
kA = eng.create_record(
    mgrA, "khach_hang", {"ma_kh": "KH0001", "ten": "Công ty Minh Phát", "hang": "vang"}
)
show("tạo khách hàng (tenant A)", kA)
kB = eng.create_record(
    mgrB, "khach_hang", {"ma_kh": "KH0001", "ten": "Cty B trùng mã nhưng khác tenant"}
)
show("cùng mã KH0001 ở tenant B (unique theo tenant)", {"ok": kB["ok"]})
show(
    "trùng mã trong tenant A",
    eng.create_record(mgrA, "khach_hang", {"ma_kh": "KH0001", "ten": "Trùng"}),
)
show(
    "sai định dạng + thiếu trường + enum sai",
    eng.create_record(mgrA, "khach_hang", {"ma_kh": "kh-1", "hang": "kim_cuong"}),
)
good = {
    "noi_dung": "Công tác Đà Nẵng 3 ngày",
    "so_tien": 8_000_000,
    "tu_ngay": "2026-10-20",
    "den_ngay": "2026-10-22",
    "khach_hang": kA["record"]["id"],
    "department_id": str(D1),
}
r1 = eng.create_record(staff1, "de_xuat_tam_ung", good)
show("staff1 tạo đề xuất", r1)
show(
    "gõ sai tên trường",
    eng.create_record(staff1, "de_xuat_tam_ung", {**good, "so_tien_vnd": 1, "so_tien": None}),
)
show(
    "đến ngày < từ ngày (rule DSL, kiểm ở app)",
    eng.create_record(staff1, "de_xuat_tam_ung", {**good, "den_ngay": "2026-10-01"}),
)
show(
    "tiền lẻ + dưới min + ngày sai",
    eng.create_record(
        staff1, "de_xuat_tam_ung", {**good, "so_tien": 50_000.5, "tu_ngay": "20/10/2026"}
    ),
)
show(
    "ref sang khách hàng của tenant B",
    eng.create_record(staff1, "de_xuat_tam_ung", {**good, "khach_hang": kB["record"]["id"]}),
)
show("kế toán (chỉ read) thử tạo", eng.create_record(acct, "de_xuat_tam_ung", good))
show(
    "staff1 tạo hộ người khác (owner_id lạ) -> RLS WITH CHECK",
    eng.create_record(staff1, "de_xuat_tam_ung", {**good, "owner_id": str(staff2.actor_id)}),
)
r2 = eng.create_record(
    staff2, "de_xuat_tam_ung", {**good, "so_tien": 80_000_000, "department_id": str(D1)}
)
r9 = eng.create_record(staff9, "de_xuat_tam_ung", {**good, "department_id": str(D2)})
rB = eng.create_record(
    staffB, "de_xuat_tam_ung", {**{k: v for k, v in good.items() if k != "khach_hang"}}
)

print("\n### Phạm vi dòng (RLS)")
for who, a in [
    ("staff1(own)", staff1),
    ("staff2(own)", staff2),
    ("mgrA(department D1)", mgrA),
    ("acct(all trong tenant)", acct),
    ("staffB(tenant B)", staffB),
]:
    s = eng.search_records(a, "de_xuat_tam_ung")
    print(
        f"{who:28s} total={s['total']} owners={sorted({str(i['owner_id'])[-2:] for i in s['items']})}"
    )
sB = eng.search_records(mgrB, "khach_hang", {"field": "ma_kh", "op": "=", "value": "KH0001"})
show(
    "mgrB tìm KH0001 -> chỉ thấy bản của B",
    {"total": sB["total"], "la_ban_ghi_cua_B": sB["items"][0]["id"] == kB["record"]["id"]},
)
show(
    "tenant B sửa bản ghi tenant A theo id",
    eng.update_record(mgrB, "de_xuat_tam_ung", r1["record"]["id"], 1, {"noi_dung": "hack hack"}),
)
show(
    "staff2 sửa đề xuất của staff1",
    eng.update_record(staff2, "de_xuat_tam_ung", r1["record"]["id"], 1, {"noi_dung": "sửa trộm"}),
)
show(
    "kế toán sửa (không có quyền update)",
    eng.update_record(acct, "de_xuat_tam_ung", r1["record"]["id"], 1, {"noi_dung": "x"}),
)
with pool.connection() as c:  # cùng kết nối vật lý vừa phục vụ tenant A/B, không set ngữ cảnh
    print(
        "xt_app không đặt tenant -> thấy",
        c.execute("SELECT count(*) FROM xt_data.de_xuat_tam_ung").fetchone()[0],
        "dòng;",
        "current_setting sau commit =",
        repr(c.execute("SELECT current_setting('xt.tenant_id', true)").fetchone()[0]),
    )
    try:
        with c.transaction():
            c.execute(
                "SELECT set_config('xt.tenant_id', %s, true), set_config('xt.actor_id', %s, true), set_config('xt.roles','staff',true)",
                (str(TA), str(staff1.actor_id)),
            )
            c.execute(
                "INSERT INTO xt_data.de_xuat_tam_ung (tenant_id, noi_dung, so_tien, tu_ngay, den_ngay) VALUES (%s,'chèn sang B',200000,'2026-01-01','2026-01-02')",
                (str(TB),),
            )
    except psycopg.Error as e:
        print(
            "SQL thô chèn tenant_id=B khi ngữ cảnh là A ->",
            type(e).__name__,
            e.diag.message_primary,
        )
    try:
        c.execute("ALTER TABLE xt_data.de_xuat_tam_ung DISABLE ROW LEVEL SECURITY")
    except psycopg.Error as e:
        print("xt_app tắt RLS ->", type(e).__name__, e.diag.message_primary)

print("\n### Optimistic concurrency + rule ở DB")
u1 = eng.update_record(mgrA, "de_xuat_tam_ung", r2["record"]["id"], 1, {"trang_thai": "cho_duyet"})
show("mgrA cập nhật rev1 -> ok", {"ok": u1["ok"], "revision": u1["record"]["revision"]})
show(
    "staff2 cập nhật cùng rev1 (đã cũ)",
    eng.update_record(
        staff2, "de_xuat_tam_ung", r2["record"]["id"], 1, {"noi_dung": "Sửa nội dung cũ"}
    ),
)
show(
    "duyệt 80tr thiếu người duyệt (CHECK r2 ở DB)",
    eng.update_record(mgrA, "de_xuat_tam_ung", r2["record"]["id"], 2, {"trang_thai": "da_duyet"}),
)
u3 = eng.update_record(
    mgrA,
    "de_xuat_tam_ung",
    r2["record"]["id"],
    2,
    {"trang_thai": "da_duyet", "nguoi_duyet": str(mgrA.actor_id)},
)
show(
    "duyệt có người duyệt",
    {
        "ok": u3["ok"],
        "revision": u3["record"]["revision"],
        "trang_thai": u3["record"]["trang_thai"],
    },
)
show(
    "gán null cho trường bắt buộc",
    eng.update_record(mgrA, "de_xuat_tam_ung", r2["record"]["id"], 3, {"noi_dung": None}),
)
show(
    "xóa khách hàng đang được tham chiếu",
    eng.delete_record(mgrA, "khach_hang", kA["record"]["id"], 1),
)

print("\n### Search DSL")
for i in range(3, 28):
    eng.create_record(
        mgrA,
        "khach_hang",
        {
            "ma_kh": f"KH{i:04d}",
            "ten": f"Khách {'Hà Nội' if i % 2 else 'Sài Gòn'} {i}",
            "hang": "vang" if i % 3 == 0 else "thuong",
        },
    )
s = eng.search_records(
    mgrA,
    "khach_hang",
    {
        "and": [
            {"field": "ten", "op": "contains", "value": "hà nội"},
            {
                "or": [
                    {"field": "hang", "op": "=", "value": "vang"},
                    {"field": "ma_kh", "op": "in", "value": ["KH0005", "KH0007"]},
                ]
            },
        ]
    },
    sort=[{"field": "ma_kh", "dir": "desc"}],
    limit=3,
    offset=0,
)
show(
    "tìm 'hà nội' AND (vàng OR mã in [...]) trang 1",
    {"total": s["total"], "has_more": s["has_more"], "ma": [x["ma_kh"] for x in s["items"]]},
)
show(
    "bộ lọc tên trường lạ",
    eng.search_records(mgrA, "khach_hang", {"field": "ten; drop table x", "op": "=", "value": "a"}),
)
show(
    "bộ lọc giá trị chứa SQL (được bind)",
    eng.search_records(mgrA, "khach_hang", {"field": "ten", "op": "=", "value": "x' OR '1'='1"})[
        "total"
    ],
)
show(
    "contains với % và _ được escape",
    eng.search_records(mgrA, "khach_hang", {"field": "ten", "op": "contains", "value": "%"})[
        "total"
    ],
)
show(
    "toán tử sai kiểu",
    eng.search_records(mgrA, "khach_hang", {"field": "hang", "op": ">", "value": 5}),
)
show("model không tồn tại", eng.search_records(mgrA, "khach_hangg"))

print("\n### Audit hash-chain")
with pool.connection() as c:
    with c.transaction():
        c.execute("SELECT set_config('xt.tenant_id', %s, true)", (str(TA),))
        print(
            "xt_app verify tenant A:",
            c.execute("SELECT * FROM xt_audit.verify(%s)", (str(TA),)).fetchone(),
            "| thấy event tenant B:",
            c.execute(
                "SELECT count(*) FROM xt_audit.event WHERE tenant_id = %s", (str(TB),)
            ).fetchone()[0],
        )
        ev = c.execute(
            "SELECT seq, op, model, diff FROM xt_audit.event WHERE op='UPDATE' ORDER BY seq LIMIT 1"
        ).fetchone()
        print("ví dụ event UPDATE:", ev[0], ev[1], ev[2], ev[3])
for who, dsn in [("xt_app", APP), ("xt_migrator", MIG)]:
    with psycopg.connect(dsn, autocommit=True) as c:
        for q in (
            "UPDATE xt_audit.event SET op='X' WHERE seq=1",
            "DELETE FROM xt_audit.event",
            "INSERT INTO xt_audit.event (tenant_id,at,model,record_id,op,diff,prev_hash,hash) VALUES (gen_random_uuid(),now(),'x',gen_random_uuid(),'x','{}','','')",
        ):
            try:
                c.execute(q)
                print(who, q[:20], "-> ĐƯỢC (lỗi!)")
            except psycopg.Error as e:
                print(f"{who:11s} {q[:22]:22s} -> {type(e).__name__}: {e.diag.message_primary}")
with psycopg.connect(SU, autocommit=True) as c:
    try:
        c.execute("UPDATE xt_audit.event SET op='X' WHERE seq=3")
    except psycopg.Error as e:
        print("superuser UPDATE (trigger) ->", e.diag.message_primary)
    c.execute("ALTER TABLE xt_audit.event DISABLE TRIGGER no_update_delete")
    c.execute(
        "UPDATE xt_audit.event SET diff = jsonb_set(diff, '{new,so_tien}', '1') WHERE seq = (SELECT min(seq) FROM xt_audit.event WHERE model='de_xuat_tam_ung' AND tenant_id=%s)",
        (str(TA),),
    )
    c.execute("ALTER TABLE xt_audit.event ENABLE TRIGGER no_update_delete")
    print(
        "superuser tắt trigger rồi sửa số tiền -> verify:",
        c.execute("SELECT * FROM xt_audit.verify(%s)", (str(TA),)).fetchone(),
        "| tenant B vẫn:",
        c.execute("SELECT * FROM xt_audit.verify(%s)", (str(TB),)).fetchone(),
    )
pool.close()
