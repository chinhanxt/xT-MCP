# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xT contributors
"""Change planning trên dữ liệu thật (chạy sau t2): v1 -> v2 -> v3."""

import copy, json, threading, time
from common import *  # noqa
from examples import V1
from xtmodel.spec import Catalog
from xtmodel import planner as P

m = mig()
with psycopg.connect(SU, autocommit=True) as su:  # dữ liệu "bẩn" để thấy đếm vi phạm
    su.execute(
        "UPDATE xt_data.khach_hang SET ten = repeat('Tên rất dài ', 6) WHERE ma_kh IN ('KH0003','KH0004')"
    )
    su.execute("UPDATE xt_data.khach_hang SET email = 'a@b.vn' WHERE ma_kh IN ('KH0005','KH0006')")

v2 = copy.deepcopy(V1)
v2["version"] = 2
kh, dx = v2["models"]
kh["fields"] = [f for f in kh["fields"] if f["name"] != "ghi_chu"] + []
for f in kh["fields"]:
    if f["name"] == "ten":
        f["max"] = 50  # siết: có 2 dòng dài 72 ký tự
    if f["name"] == "email":
        f["name"], f["label"], f["unique"] = (
            "email_lien_he",
            "Email liên hệ",
            True,
        )  # rename + unique (có trùng)
kh["fields"] += [
    {
        "id": "f5",
        "name": "ghi_chu_noi_bo",
        "type": "text",
        "label": "Ghi chú nội bộ",
    },  # cùng id f5 => RENAME
    {
        "id": "f6",
        "name": "so_dien_thoai",
        "type": "text",
        "label": "Số điện thoại",
        "regex": "^0[0-9]{9}$",
    },
    {
        "id": "f7",
        "name": "khu_vuc",
        "type": "enum",
        "label": "Khu vực",
        "required": True,
        "default": "mien_bac",
        "options": [
            {"value": "mien_bac", "label": "Miền Bắc"},
            {"value": "mien_nam", "label": "Miền Nam"},
        ],
    },
    {"id": "f8", "name": "ma_so_thue", "type": "text", "label": "Mã số thuế", "required": True},
]
kh["indexes"] = [{"id": "i1", "fields": ["ten"]}]
kh["permissions"]["sales"]["scope"] = "team"
for f in dx["fields"]:
    if f["name"] == "trang_thai":
        f["options"] = [o for o in f["options"] if o["value"] != "tu_choi"]
    if f["name"] == "noi_dung":
        f["min"] = 3  # nới
dx["fields"] = [f for f in dx["fields"] if f["name"] != "ty_le_vat"]
dx["rules"].append(
    {
        "id": "r3",
        "message": "Đề xuất khẩn tối đa 5 triệu",
        "check": {
            "or": [
                {"field": "khan", "op": "!=", "value": True},
                {"field": "so_tien", "op": "<=", "value": 5000000},
            ]
        },
    }
)
v2["models"].append(
    {
        "id": "m3",
        "name": "hop_dong",
        "label": "Hợp đồng",
        "fields": [
            {
                "id": "f1",
                "name": "so_hd",
                "type": "text",
                "label": "Số hợp đồng",
                "required": True,
                "unique": True,
            },
            {
                "id": "f2",
                "name": "khach_hang",
                "type": "ref",
                "ref": "khach_hang",
                "label": "Khách hàng",
                "required": True,
            },
            {"id": "f3", "name": "gia_tri", "type": "money", "label": "Giá trị"},
        ],
        "permissions": {
            "manager": {"actions": ["create", "read", "update", "delete"], "scope": "all"}
        },
    }
)

print("### Lần 1: spec v2 do agent đề xuất")
c2 = Catalog.model_validate(v2)
p = P.plan(P.load_current(m), c2, m, rehearse=True)
print(p.render_vi())
try:
    P.apply(m, c2, p.hash, set(), "agent:claude", "user:gd")
except P.ApplyError as e:
    print("APPLY ->", e)

print("\n### Lần 2: agent sửa theo phản hồi (MST không bắt buộc, bỏ unique email, ten max 100)")
for f in kh["fields"]:
    if f["name"] == "ma_so_thue":
        f["required"] = False
    if f["name"] == "email_lien_he":
        f["unique"] = False
    if f["name"] == "ten":
        f["max"] = 100
c2 = Catalog.model_validate(v2)
p = P.plan(P.load_current(m), c2, m, rehearse=True)
print(p.render_vi())
j = p.to_json()
print(
    "JSON (rút gọn):",
    json.dumps(
        {
            k: j[k]
            for k in (
                "base_version",
                "target_version",
                "plan_hash",
                "summary",
                "destructive_op_ids",
            )
        },
        ensure_ascii=False,
    ),
)
print(
    "1 op đầy đủ:",
    json.dumps(next(o for o in j["ops"] if o["id"].startswith("rename_field")), ensure_ascii=False),
)
pathlib.Path("plan_v2.json").write_text(json.dumps(j, ensure_ascii=False, indent=1))

for label, h, conf in [
    ("thiếu xác nhận phá hủy", p.hash, set()),
    ("mã kế hoạch cũ/khác", "deadbeefdeadbeef", set(j["destructive_op_ids"])),
]:
    try:
        P.apply(m, c2, h, conf, "agent:claude", "user:gd")
    except P.ApplyError as e:
        print(f"APPLY ({label}) ->", e)

print("\n### lock_timeout: một giao dịch khác đang giữ khóa bảng khach_hang")
holder = psycopg.connect(SU)
holder.execute("SELECT count(*) FROM xt_data.khach_hang")  # giữ ACCESS SHARE, không commit
t0 = time.time()
try:
    P.apply(
        m, c2, p.hash, set(j["destructive_op_ids"]), "agent:claude", "user:gd", lock_timeout="2s"
    )
except psycopg.Error as e:
    print(
        f"APPLY -> {type(e).__name__}: {e.diag.message_primary} sau {time.time() - t0:.1f}s; version hiện tại =",
        m.execute("SELECT max(version) FROM xt_meta.catalog_version").fetchone()[0],
    )
holder.rollback()
holder.close()

print("\n### Áp dụng với xác nhận lần 2")
t0 = time.time()
r = P.apply(m, c2, p.hash, set(j["destructive_op_ids"]), "agent:claude", "user:gd")
print(f"OK v{r['version']} tx_steps={r['tx_steps']} trong {time.time() - t0:.2f}s; post:")
for x in r["post"]:
    print("   ", "OK " if x["ok"] else "LỖI", x["sql"][:110], x.get("error", ""))
print(
    "dữ liệu sau rename:",
    m.execute(
        "SELECT ma_kh, email_lien_he, khu_vuc, ma_so_thue FROM xt_data.khach_hang WHERE ma_kh IN ('KH0005','KH0001') ORDER BY 1"
    ).fetchall(),
)
print(
    "cột còn lại:",
    [
        r[0]
        for r in m.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema='xt_data' AND table_name='khach_hang' AND ordinal_position > 10 ORDER BY ordinal_position"
        ).fetchall()
    ],
)
print(
    "constraint NOT VALID còn lại:",
    m.execute(
        "SELECT count(*) FROM pg_constraint WHERE connamespace='xt_data'::regnamespace AND NOT convalidated"
    ).fetchone()[0],
    "| index invalid:",
    m.execute(
        "SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid WHERE c.relnamespace='xt_data'::regnamespace AND NOT indisvalid"
    ).fetchone()[0],
)
print(
    "versions:",
    m.execute(
        "SELECT version, plan_hash, proposed_by, approved_by, plan->'summary' FROM xt_meta.catalog_version ORDER BY 1"
    ).fetchall(),
)

print("\n### v3: đổi tên model + xóa model + đổi kiểu (bị chặn)")
v3 = copy.deepcopy(v2)
v3["version"] = 3
v3["models"][1]["name"] = "de_xuat_chi"
v3["models"][1]["label"] = "Đề xuất chi"
v3["models"] = [x for x in v3["models"] if x["name"] != "hop_dong"]
v3["models"][0]["fields"][0]["type"] = "number"
v3["models"][0]["fields"][0].pop("regex")
c3 = Catalog.model_validate(v3)
print(P.plan(P.load_current(m), c3, m).render_vi())
v3["models"][0]["fields"][0]["type"] = "text"
v3["models"][0]["fields"][0]["regex"] = "^KH[0-9]{4}$"
c3 = Catalog.model_validate(v3)
p3 = P.plan(P.load_current(m), c3, m, rehearse=True)
print(p3.render_vi())
r = P.apply(m, c3, p3.hash, set(p3.to_json()["destructive_op_ids"]), "agent:claude", "user:gd")
print(
    "OK v3; bảng:",
    [
        x[0]
        for x in m.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname='xt_data' ORDER BY 1"
        ).fetchall()
    ],
    "| số dòng de_xuat_chi:",
    m.execute("SELECT count(*) FROM xt_data.de_xuat_chi").fetchone()[0],
    "| policy:",
    m.execute("SELECT count(*) FROM pg_policies WHERE tablename='de_xuat_chi'").fetchone()[0],
)
