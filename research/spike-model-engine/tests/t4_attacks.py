# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xT contributors
"""Tấn công định danh/giá trị do agent cung cấp."""

import copy, uuid
import sqlglot
from pydantic import ValidationError
from common import *  # noqa
from examples import V1
from xtmodel.spec import Catalog, ModelSpec, FieldSpec
from xtmodel import compiler as C
from xtmodel.crud import Actor, Engine


def first_err(e: ValidationError):
    x = e.errors()[0]
    return f"{'.'.join(map(str, x['loc'][-3:]))}: {x['msg'][:95]}"


print("### A. Tên độc hại trong spec -> bị từ chối khi validate")
NAMES = [
    'x"; drop table xt_meta.catalog_version; --',
    "khách_hàng",
    "ｓelect",
    "select",
    "user",
    "order",
    "a" * 41,
    "a" * 300,
    "tenant_id",
    "xmin",
    "Ten",
    "ten\x00",
    "ten\n",
    "a__b",
    "1abc",
    "_x",
    "",
    "ten'--",
]
for where in ("model.name", "field.name"):
    rej = 0
    for n in NAMES:
        v = copy.deepcopy(V1)
        if where == "model.name":
            v["models"][0]["name"] = n
        else:
            v["models"][0]["fields"][1]["name"] = n
        try:
            Catalog.model_validate(v)
            print("  CHẤP NHẬN (lỗi!)", repr(n))
        except ValidationError as e:
            rej += 1
            if where == "field.name" and n in (
                'x"; drop table xt_meta.catalog_version; --',
                "select",
                "tenant_id",
                "a" * 41,
                "khách_hàng",
            ):
                print(f"  {n[:45]!r:50s} -> {first_err(e)}")
    print(f"  {where}: từ chối {rej}/{len(NAMES)}")
for label, mut in [
    (
        "role độc hại",
        lambda v: v["models"][0]["permissions"].update(
            {"admin') OR true OR ('": {"actions": ["read"], "scope": "all"}}
        ),
    ),
    (
        "ref tới model không tồn tại",
        lambda v: v["models"][1]["fields"][4].update({"ref": "pg_authid"}),
    ),
    (
        "rule tham chiếu cột hệ thống",
        lambda v: v["models"][1]["rules"].append(
            {"id": "r9", "message": "x", "check": {"field": "tenant_id", "op": "is_null"}}
        ),
    ),
    (
        "rule kiểu sai (date so với money)",
        lambda v: v["models"][1]["rules"].append(
            {
                "id": "r9",
                "message": "x",
                "check": {"field": "tu_ngay", "op": ">", "other_field": "so_tien"},
            }
        ),
    ),
    (
        "rule là SQL tự do",
        lambda v: v["models"][1]["rules"].append(
            {"id": "r9", "message": "x", "check": {"sql": "1=1; drop table x"}}
        ),
    ),
    (
        "regex backreference (ReDoS)",
        lambda v: v["models"][0]["fields"][0].update({"regex": "^(a+)+\\1$"}),
    ),
    ("id ổn định độc hại", lambda v: v["models"][0].update({"id": "m1; drop"})),
    (
        "enum trên trường số",
        lambda v: v["models"][1]["fields"][1].update({"options": [{"value": "a", "label": "a"}]}),
    ),
]:
    v = copy.deepcopy(V1)
    mut(v)
    try:
        Catalog.model_validate(v)
        print(f"  {label}: CHẤP NHẬN (lỗi!)")
    except ValidationError as e:
        print(f"  {label:34s} -> {first_err(e)}")

print("\n### B. Giá trị độc hại hợp lệ về cú pháp -> được quote thành literal")
v = copy.deepcopy(V1)
v["models"] = [v["models"][0]]
k = v["models"][0]
k["name"] = "kh_attack"
k["id"] = "m9"
k["label"] = "Khách'); DROP TABLE xt_meta.catalog_version; --"
k["fields"][3]["options"].append(
    {"value": "x'); DROP TABLE xt_meta.catalog_version; --", "label": "$$; DROP $$"}
)
k["fields"][4]["default"] = "'); DELETE FROM xt_audit.event; --"
k["fields"][2]["regex"] = "^[^']*'; DROP TABLE x; --$"
cat = Catalog.model_validate(v)
steps = C.create_model_steps(cat.models[0])
ddl = steps[0].text()
print("  CHECK enum:", [l.strip() for l in ddl.splitlines() if "ck_m9_f4_enum" in l][0])
print("  sqlglot đếm câu lệnh trong CREATE TABLE:", len(sqlglot.parse(ddl, read="postgres")))
m = mig()
with m.transaction(force_rollback=True):
    for s in steps:
        m.execute(s.sql)
    m.execute("SET LOCAL xt.tenant_id = '00000000-0000-0000-0000-00000000000a'")
    m.execute(
        """INSERT INTO xt_data.kh_attack (ma_kh, ten, hang) VALUES ('KH0001', 'ab', 'x''); DROP TABLE xt_meta.catalog_version; --')"""
    )
    print(
        "  bảng tạo được, enum độc hại lưu nguyên văn:",
        m.execute("SELECT hang, ghi_chu FROM xt_data.kh_attack").fetchone(),
    )
    print(
        "  comment:",
        m.execute("SELECT obj_description('xt_data.kh_attack'::regclass)").fetchone()[0],
    )
print(
    "  catalog_version còn nguyên:",
    m.execute("SELECT count(*) FROM xt_meta.catalog_version").fetchone()[0],
    "phiên bản",
)

print("\n### C. Bỏ qua validator (model_construct) -> Identifier vẫn quote an toàn")
evil = 'x"; DROP TABLE xt_meta.catalog_version; --'
fm = FieldSpec.model_construct(
    id="f1",
    name=evil,
    type="text",
    label="l",
    required=False,
    unique=False,
    min=None,
    max=None,
    regex=None,
    options=None,
    ref=None,
    default=None,
)
mm = ModelSpec.model_construct(
    id="m8",
    name=evil,
    label="l",
    title_field=None,
    fields=[fm],
    indexes=[],
    rules=[],
    permissions={},
)
st = C.create_model_steps(mm)
print("  ", st[0].text().splitlines()[0])
with m.transaction(force_rollback=True):
    for s in st:
        m.execute(s.sql)
    print(
        "   relname trong pg_class:",
        m.execute("SELECT relname FROM pg_class WHERE relname LIKE 'x\"%%'").fetchone()[0],
        "| sqlglot:",
        len(sqlglot.parse(st[0].text(), read="postgres")),
        "câu lệnh",
    )
print(
    "   catalog_version còn nguyên:",
    m.execute("SELECT count(*) FROM xt_meta.catalog_version").fetchone()[0],
)

print("\n### D. Runtime")
try:
    Actor(uuid.uuid4(), uuid.uuid4(), ("staff,accountant",))
except ValueError as e:
    print("  role 'staff,accountant' ->", e)
from xtmodel import planner as P

eng = Engine(app_pool(1), P.load_current(m))
a = Actor(uuid.UUID(int=0xA), uuid.UUID(int=0xA3), ("manager",))
deep = {"field": "ten", "op": "=", "value": "a"}
for _ in range(9):
    deep = {"not": deep}
for label, f in [
    ("lồng 10 cấp", deep),
    ("in 1000 phần tử", {"field": "ten", "op": "in", "value": ["a"] * 1000}),
    ("op lạ", {"field": "ten", "op": "~", "value": ".*"}),
    ("key lạ", {"field": "ten", "op": "=", "value": "a", "sql": "1=1"}),
]:
    print(
        f"  filter {label:16s} ->", eng.search_records(a, "khach_hang", f)["error"]["message"][:110]
    )
print(
    "  sort injection ->",
    eng.search_records(a, "khach_hang", sort=[{"field": "ten", "dir": "desc; drop"}])["error"][
        "code"
    ],
)
print("  limit 1e6 ->", eng.search_records(a, "khach_hang", limit=10**6)["error"]["message"])
print(
    "  json 100KB ->",
    eng.create_record(
        Actor(uuid.UUID(int=0xA), uuid.UUID(int=0xA1), ("staff",)),
        "de_xuat_chi",
        {
            "noi_dung": "abcde",
            "so_tien": 200000,
            "tu_ngay": "2026-01-01",
            "den_ngay": "2026-01-02",
            "chi_tiet": {"x": "a" * 100000},
        },
    )["error"]["message"],
)
eng.pool.close()
