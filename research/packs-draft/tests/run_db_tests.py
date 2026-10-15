# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
import json, sys, uuid
import _paths
import psycopg
from psycopg_pool import ConnectionPool
from seedlib import *
from xtmodel.crud import Engine, Actor
PACKS = ["de-xuat-phe-duyet", "giao-viec-sla", "nhan-su-vong-doi"]
cat = load_catalog(PACKS, 3)
pool = ConnectionPool("host=localhost port=15440 dbname=xt user=xt_app password=app", min_size=1, max_size=2, open=True)
eng = Engine(pool, cat)
U_ = {}
for p in PACKS: U_.update(load_users(p)[0])
A = lambda k: actor_of(U_[k])
km = {}
print("### SEED")
for p in PACKS:
    print(p, seed_pack(eng, p, km, print))
def show(t, o):
    s = json.dumps(o, ensure_ascii=False, default=str)
    print(f"- {t}: {s[:330]}")
def code(o): return o.get("error", {}).get("code", "OK") if not o["ok"] else "OK"
viet, mai, ha, long_, anh = A("NV-0006"), A("NV-0005"), A("NV-0002"), A("NV-0001"), A("NV-0007")
wf = actor_of(U_["xt-workflow"])
print("\n### PACK 1: de-xuat-phe-duyet")
base = {"loai": km[("loai_de_xuat","tam_ung")], "nguoi_de_nghi": str(viet.actor_id), "owner_id": str(viet.actor_id), "department_id": str(dept_id("KD")),
        "phong_ban": "KD", "so_tien": 8000000, "ly_do": "Công tác Đà Nẵng, trả khách sạn và vé", "ma_phieu": "TU-2026-0042"}
show("nhan_vien (Việt) tạo thẳng de_xuat qua CRUD", eng.create_record(viet, "de_xuat", base))
r = eng.create_record(wf, "de_xuat", base); show("xt_workflow tạo de_xuat cho Việt", {"ok": r["ok"], "ma": r["record"]["ma_phieu"] if r["ok"] else r})
dx = r["record"]
show("xt_workflow: duyệt cuối = chính người đề nghị (SoD CHECK)", eng.update_record(wf, "de_xuat", dx["id"], dx["revision"], {"trang_thai":"da_duyet","nguoi_duyet_cuoi":str(viet.actor_id),"ngay_duyet":"2026-10-15T10:00:00+07:00"}))
show("xt_workflow: da_duyet thiếu người duyệt (rule r2)", eng.update_record(wf, "de_xuat", dx["id"], dx["revision"], {"trang_thai":"da_duyet"}))
show("xt_workflow: da_duyet bởi Long (hợp lệ)", eng.update_record(wf, "de_xuat", dx["id"], dx["revision"], {"trang_thai":"da_duyet","nguoi_duyet_cuoi":str(long_.actor_id),"ngay_duyet":"2026-10-15T10:00:00+07:00"}))
tu = {"de_xuat": dx["id"], "owner_id": str(viet.actor_id), "department_id": str(dept_id("KD")), "ngay_lap": "2026-10-15", "so_tien": 80000000, "ngay_di": "2026-11-20", "ngay_ve": "2026-11-18",
      "thoi_han_hoan_ung": "2026-11-10", "hinh_thuc_nhan": "chuyen_khoan"}
show("tam_ung 80tr + ngày về < ngày đi + thiếu STK (storyboard S1)", eng.create_record(wf, "tam_ung", tu))
show("tam_ung hợp lệ 8tr", {"ok": eng.create_record(wf, "tam_ung", {**tu, "so_tien": 8000000, "ngay_ve": "2026-11-22", "thoi_han_hoan_ung": "2026-11-29", "so_tai_khoan": "9704000000006"})["ok"]})
show("thanh_toan 25tr không có HĐĐT", eng.create_record(wf, "thanh_toan", {"de_xuat": dx["id"], "owner_id": str(viet.actor_id), "ngay_de_nghi": "2026-10-15", "so_tien": 25000000, "noi_dung": "Mua thiết bị", "nha_cung_cap": "Cty X", "hinh_thuc_thanh_toan": "tien_mat", "so_luong_chung_tu": 2}))
hu = {"owner_id": str(viet.actor_id), "ngay_thanh_toan": "2026-11-30", "tam_ung_da_nhan": 8000000, "bang_ke_chung_tu": "3 hóa đơn"}
tu_id = km[("tam_ung", None)] if ("tam_ung", None) in km else None
show("hoan_ung thực chi > tạm ứng mà không khai chi thừa", eng.create_record(wf, "hoan_ung", {**hu, "de_xuat": dx["id"], "tam_ung": next(iter([v for (m,k),v in km.items() if m=="de_xuat"])), "thuc_chi": 9000000}))
print("\n--- cô lập dòng theo vai trò (de_xuat)")
for nm, a in [("Việt (own)", viet), ("Anh (own, cùng phòng KD)", anh), ("Mai (TBP, department)", mai), ("Hà (KTT, all)", ha), ("agent (role agent, không có quyền DB)", Actor(tenant_id(), uuid.uuid4(), ("agent",)))]:
    o = eng.search_records(a, "de_xuat", {}, limit=50) if True else None
    show(nm, {"ok": o["ok"], "total": o.get("total"), "ma": sorted(x["ma_phieu"] for x in o.get("items", []))} if o["ok"] else o["error"])
o = eng.search_records(Actor(tenant_id("ankhang"), uuid.uuid4(), ("giam_doc",)), "de_xuat", {}, limit=50)
show("giám đốc tenant ankhang xem de_xuat", {"ok": o["ok"], "total": o.get("total")})
print("\n### PACK 2: giao-viec-sla")
mk = {"ma_cv": "CV-2026-0099", "tieu_de": "Thử tự nghiệm thu", "loai": km[("loai_cong_viec","chung")], "nguoi_giao": str(mai.actor_id), "nguoi_thuc_hien": str(viet.actor_id), "owner_id": str(viet.actor_id),
      "department_id": str(dept_id("KD")), "han_chot": "2026-10-30T17:00:00+07:00", "tieu_chi_xong": "Có báo cáo gửi trưởng phòng"}
show("Việt tự cập nhật cv (không có quyền ghi)", eng.update_record(viet, "cong_viec", km[("cong_viec","CV-2026-0001")], 1, {"tien_do": 100, "trang_thai": "hoan_thanh"}))
show("Mai (TBP) tạo công việc giao cho Việt", {"ok": (r2 := eng.create_record(mai, "cong_viec", mk))["ok"]})
show("Mai tạo việc Khẩn nhưng lớp SLA thường (rule)", eng.create_record(mai, "cong_viec", {**mk, "ma_cv": "CV-2026-0098", "uu_tien": "khan"}))
show("hoàn thành mà không có kết quả (rule)", eng.update_record(wf, "cong_viec", km[("cong_viec","CV-2026-0001")], 1, {"trang_thai": "hoan_thanh"}))
show("tiến độ 150% (max 100)", eng.update_record(wf, "cong_viec", km[("cong_viec","CV-2026-0001")], 1, {"tien_do": 150}))
show("phụ thuộc tự tham chiếu (ref self) hợp lệ", eng.update_record(wf, "cong_viec", km[("cong_viec","CV-2026-0001")], 1, {"phu_thuoc": km[("cong_viec","CV-2026-0004")]}))
o = eng.search_records(viet, "cong_viec", {}, limit=50); show("Việt (own) thấy cv", sorted(x["ma_cv"] for x in o["items"]))
o = eng.search_records(anh, "cong_viec", {}, limit=50); show("Anh (own) thấy cv", sorted(x["ma_cv"] for x in o["items"]))
o = eng.search_records(mai, "cong_viec", {}, limit=50); show("Mai (department KD) thấy cv", sorted(x["ma_cv"] for x in o["items"]))
print("\n### PACK 3: nhan-su-vong-doi")
hung, linh, tam, thinh = A("NV-0011"), A("NV-0010"), A("NV-0009"), A("NV-0012")
for nm, a in [("Hùng (nhan_vien) đọc PII", hung), ("Việt đọc PII", viet), ("Linh (hr_pii) đọc PII", linh), ("Tâm (nhan_su, không hr_pii) đọc PII", tam)]:
    o = eng.search_records(a, "nhan_vien_pii", {}, limit=50)
    show(nm, {"total": o.get("total"), "luong": [x.get("luong_co_ban") for x in o.get("items", [])][:3]} if o["ok"] else o["error"])
o = eng.search_records(Actor(tenant_id(), uuid.uuid4(), ("agent",)), "nhan_vien_pii", {}, limit=5); show("agent đọc PII (DB)", o["error"] if not o["ok"] else o)
o = eng.search_records(viet, "nhan_vien", {}, limit=50); show("Việt (own) đọc nhan_vien", {"total": o["total"]})
o = eng.search_records(thinh, "nhan_vien", {}, limit=50); show("Thịnh (TBP IT, dept) đọc nhan_vien", {"total": o["total"]})
show("nhan_vien nghỉ mà thiếu ngày làm việc cuối (rule)", eng.update_record(linh, "nhan_vien", km[("nhan_vien","NV-0015")], 1, {"trang_thai": "da_nghi"}))
show("quyết toán đã chi khi còn tài sản chưa trả (rule)", eng.create_record(A("NV-0003"), "quyet_toan_nghi_viec", {"ho_so": km[("ho_so_vong_doi","ON-2026-0001")], "owner_id": str(A("NV-0015").actor_id), "tong_thanh_toan": 1000000, "tai_san_chua_tra": 1, "da_chi": True}))
print("\n### RLS thô")
with psycopg.connect("host=localhost port=15440 dbname=xt user=xt_owner password=owner") as su:
    cnt = {t: su.execute(f'select count(*) from xt_data.{t}').fetchone()[0] for t in ["loai_de_xuat","de_xuat","tam_ung","buoc_duyet","loai_cong_viec","cong_viec","cong_viec_muc","nhat_ky_sla","ban_tin_tuan","phong_ban","nhan_vien","nhan_vien_pii","ho_so_vong_doi","viec_vong_doi","tai_san_cap","cap_quyen"]}
    print("row counts:", cnt)
    print("audit hash-chain verify (n, first_bad_seq):", su.execute("select * from xt_audit.verify(%s)", (str(tenant_id()),)).fetchone())
pool.close()
