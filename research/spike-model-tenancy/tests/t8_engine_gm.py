# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xT contributors
"""G-M1 (trường do workflow quản chỉ ghi qua đường transition) + G-M2 (nhãn pii trong spec/catalog/describe/audit).
Chạy trên layout B2 (schema theo tenant) — biến thể khuyến nghị."""
import copy, json
from common import *  # noqa
from psycopg import sql
from xtmodel.crud import Actor, Engine
from xtmodel.spec import Catalog

S1, S2 = 11, 12
SPEC = {"version": 1, "models": [
    {"id": "nv", "name": "nhan_vien", "label": "Nhân viên", "fields": [
        {"id": "f1", "name": "ho_ten", "type": "text", "label": "Họ tên", "required": True},
        {"id": "f2", "name": "so_cccd", "type": "text", "label": "Số CCCD", "regex": "^[0-9]{12}$", "pii": "basic"},
        {"id": "f3", "name": "so_dien_thoai", "type": "text", "label": "Số điện thoại"},
        {"id": "f4", "name": "luong", "type": "money", "label": "Lương"},
        {"id": "f5", "name": "suc_khoe", "type": "text", "label": "Tình trạng sức khỏe", "pii": "sensitive"},
        {"id": "f6", "name": "ghi_chu", "type": "text", "label": "Ghi chú"},
        {"id": "f7", "name": "phong_ban", "type": "enum", "label": "Phòng ban", "options": [{"value": "kt", "label": "Kế toán"}, {"value": "kd", "label": "Kinh doanh"}]}],
     "permissions": {"hr": {"actions": ["create", "read", "update"], "scope": "all"}}},
    {"id": "dx", "name": "de_xuat", "label": "Đề xuất", "fields": [
        {"id": "f1", "name": "noi_dung", "type": "text", "label": "Nội dung", "required": True, "pii": "none"},
        {"id": "f2", "name": "so_tien", "type": "money", "label": "Số tiền", "required": True, "pii": "none"},
        {"id": "f3", "name": "trang_thai", "type": "enum", "label": "Trạng thái", "required": True, "default": "nhap", "managed_by": "workflow",
         "options": [{"value": "nhap", "label": "Nháp"}, {"value": "cho_duyet", "label": "Chờ duyệt"}, {"value": "da_duyet", "label": "Đã duyệt"}]},
        {"id": "f4", "name": "nguoi_duyet", "type": "user", "label": "Người duyệt", "managed_by": "workflow"},
        {"id": "f5", "name": "thoi_diem_duyet", "type": "datetime", "label": "Thời điểm duyệt", "managed_by": "workflow"}],
     "permissions": {"staff": {"actions": ["create", "read", "update"], "scope": "own"}, "manager": {"actions": ["read", "update"], "scope": "all"}}}]}
m = mig(); add_tenants(m, S2)
for s in (S1, S2):
    apply_spec(m, SPEC, lay("B2", s), 100 + s)
pool = ConnectionPool(APP, min_size=1, max_size=2, open=True)
cat = Catalog.model_validate(SPEC)
e1, e2 = Engine(pool, cat, lay("B2", S1)), Engine(pool, cat, lay("B2", S2))
hr = Actor(tid(S1), uuid.UUID(int=0xE1), ("hr",))
staff = Actor(tid(S1), uuid.UUID(int=0xE2), ("staff",))
mgr = Actor(tid(S1), uuid.UUID(int=0xE3), ("manager",))
mgr2 = Actor(tid(S2), uuid.UUID(int=0xF3), ("manager",))

print("### G-M2: nhãn PII")
d = e1.describe_model(hr, "nhan_vien")["model"]
print("describe_model nhan_vien:", [(f["name"], f["pii"], "gợi ý" if f["pii_suggested"] else "khai báo") for f in d["fields"]])
stored = m.execute("SELECT spec FROM xt_meta.catalog_version WHERE tenant_seq = %s", (100 + S1,)).fetchone()[0]
print("catalog_version lưu:", {f["name"]: f["pii"] for f in stored["models"][0]["fields"]})
r = e1.create_record(hr, "nhan_vien", {"ho_ten": "Nguyễn Văn A", "so_cccd": "001203004567", "so_dien_thoai": "0912345678",
                                      "luong": 25_000_000, "suc_khoe": "tiểu đường", "phong_ban": "kt"})["record"]
u = e1.update_record(hr, "nhan_vien", r["id"], 1, {"luong": 27_000_000, "ghi_chu": "tăng lương"})
with su() as x:
    ev = x.execute("SELECT op, diff FROM xt_audit.event WHERE record_id = %s ORDER BY seq", (r["id"],)).fetchall()
    for op, diff in ev:
        side = diff.get("new") or {}
        print(f"  audit {op:6s}:", {k: side.get(k) for k in ("ho_ten", "so_cccd", "luong", "suc_khoe", "ghi_chu") if k in side}, "| old:", (diff.get("old") or {}).get("luong"))
    print("  verify chuỗi audit tenant:", x.execute("SELECT * FROM xt_audit.verify(%s)", (tid(S1),)).fetchone())
v2 = copy.deepcopy(SPEC); v2["version"] = 2
for f in v2["models"][0]["fields"]:
    if f["name"] == "luong": f["pii"] = "basic"
    if f["name"] == "ghi_chu": f["pii"] = "basic"
with using(lay("B2", S1)):
    print(P.plan(P.load_current(m, 100 + S1), Catalog.model_validate(v2), m).render_vi())

print("\n### G-M1: trường do workflow quản")
d = e1.describe_model(staff, "de_xuat")["model"]
print("describe_model de_xuat managed_by:", {f["name"]: f["managed_by"] for f in d["fields"]})
print("values_schema update (CRUD):", sorted(d["values_schema"]["update"]["properties"]))
print("create kèm trang_thai=da_duyet ->", e1.create_record(staff, "de_xuat", {"noi_dung": "Mua máy in", "so_tien": 5_000_000, "trang_thai": "da_duyet"})["error"])
rec = e1.create_record(staff, "de_xuat", {"noi_dung": "Mua máy in", "so_tien": 5_000_000})["record"]
print("create hợp lệ -> trang_thai =", rec["trang_thai"], "revision", rec["revision"])
for patch in ({"trang_thai": "da_duyet"}, {"nguoi_duyet": str(staff.actor_id)}, {"so_tien": 4_500_000, "thoi_diem_duyet": "2026-10-15T10:00:00+07:00"}):
    res = e1.update_record(staff, "de_xuat", rec["id"], 1, patch)
    print(f"update_record {list(patch)} ->", res.get("error", {}).get("code"), res.get("error", {}).get("message"))
ok = e1.update_record(staff, "de_xuat", rec["id"], 1, {"so_tien": 4_500_000})
print("update_record so_tien (trường thường) ->", ok["ok"], "revision", ok["record"]["revision"])
T = sql.Identifier(f"xt_t{S1}", "de_xuat")
with psycopg.connect(APP, autocommit=True) as c:
    for q, a in ((sql.SQL("UPDATE {} SET trang_thai = 'da_duyet' WHERE id = %s").format(T), [rec["id"]]),
                 (sql.SQL("INSERT INTO {} (noi_dung, so_tien, trang_thai) VALUES ('x', 1, 'da_duyet')").format(T), []),
                 (sql.SQL("UPDATE {} SET noi_dung = 'sửa SQL thô' WHERE id = %s").format(T), [rec["id"]])):
        try:
            with c.transaction():
                ctx(c, S1, actor=staff.actor_id, roles="staff")
                n = c.execute(q, a).rowcount
            print(f"  SQL thô xt_app: {q.as_string()[:60]:60s} -> OK ({n} dòng)")
        except psycopg.Error as ex:
            print(f"  SQL thô xt_app: {q.as_string()[:60]:60s} -> {type(ex).__name__}: {ex.diag.message_primary}")
cur = [x for x in e1.search_records(mgr, "de_xuat")["items"] if str(x["id"]) == str(rec["id"])][0]
w = e1.workflow_write(mgr, "de_xuat", rec["id"], cur["revision"], {"trang_thai": "da_duyet", "nguoi_duyet": str(mgr.actor_id), "thoi_diem_duyet": "2026-10-15T10:00:00+07:00"})
print("workflow_write raw:", w) if not w["ok"] else None
print("workflow_write (đường transition, SET LOCAL ROLE xt_workflow) ->", w["ok"], w["record"]["trang_thai"], "rev", w["record"]["revision"])
print("workflow_write của tenant 12 vào bảng tenant 11 (lỗi routing) ->",
      Engine(pool, cat, lay("B2", S1)).workflow_write(mgr2, "de_xuat", rec["id"], w["record"]["revision"], {"trang_thai": "nhap"})["error"]["code"])
v2 = copy.deepcopy(SPEC); v2["version"] = 2
v2["models"][1]["fields"][2].pop("managed_by")
with using(lay("B2", S1)):
    p = P.plan(P.load_current(m, 100 + S1), Catalog.model_validate(v2), m)
print([o.message for o in p.ops if o.kind == "grants"], [o.cls for o in p.ops if o.kind == "grants"])
pool.close()
