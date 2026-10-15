# SPDX-License-Identifier: Apache-2.0
"""Model spec rút gọn: mỗi field mang nhãn `pii: none|basic|sensitive` + `pii_kind` (để che/nhận dạng)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

PiiLevel = Literal["none", "basic", "sensitive"]
LEVEL_RANK = {"none": 0, "basic": 1, "sensitive": 2}
FieldType = Literal["text", "int", "money", "date", "enum"]


class FieldSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    type: FieldType = "text"
    label: str
    pii: PiiLevel = "none"
    # kind dùng cho bộ che gợi ý (hint) và cho log; không ảnh hưởng quyết định egress
    pii_kind: Literal["name", "phone", "national_id", "address", "salary", "health", "bank", "email", "dob", None] = None
    unique: bool = False
    required: bool = False


class ModelSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    label: str
    key_field: str          # mã nghiệp vụ không phải PII, dùng làm gốc token (vd NV-0042)
    fields: list[FieldSpec]

    def field(self, name: str) -> FieldSpec | None:
        return next((f for f in self.fields if f.name == name), None)


NHAN_VIEN = ModelSpec(
    name="nhan_vien", label="Nhân viên", key_field="ma_nv",
    fields=[
        FieldSpec(name="ma_nv", label="Mã NV", unique=True, required=True),
        FieldSpec(name="ho_ten", label="Họ tên", pii="basic", pii_kind="name", required=True),
        FieldSpec(name="phong_ban", label="Phòng ban"),
        FieldSpec(name="so_dien_thoai", label="Số điện thoại", pii="basic", pii_kind="phone"),
        FieldSpec(name="email", label="Email", pii="basic", pii_kind="email"),
        FieldSpec(name="dia_chi", label="Địa chỉ thường trú", pii="basic", pii_kind="address"),
        # Số định danh/CCCD và SĐT là DLCN CƠ BẢN (NĐ 356/2025 Đ.3 k.7); ẢNH thẻ căn cước mới là nhạy cảm (Đ.4 k.1 đ.i)
        FieldSpec(name="so_cccd", label="Số CCCD", pii="basic", pii_kind="national_id", unique=True),
        # Lương: NĐ 356 không nêu đích danh; xT mặc định xếp nhạy cảm (bí mật cá nhân, Đ.4 k.1 đ.c) [chưa kiểm chứng]
        FieldSpec(name="luong", type="money", label="Lương cơ bản", pii="sensitive", pii_kind="salary"),
        FieldSpec(name="suc_khoe", label="Tình trạng sức khỏe", pii="sensitive", pii_kind="health"),   # Đ.4 k.1 đ.d
        FieldSpec(name="so_tai_khoan", label="Số tài khoản NH", pii="sensitive", pii_kind="bank"),
        FieldSpec(name="ghi_chu", label="Ghi chú", pii="none"),
    ],
)

HOP_DONG = ModelSpec(
    name="hop_dong", label="Hợp đồng lao động", key_field="ma_hd",
    fields=[
        FieldSpec(name="ma_hd", label="Mã HĐ", unique=True, required=True),
        FieldSpec(name="ma_nv", label="Mã NV"),
        FieldSpec(name="cccd_nguoi_ky", label="CCCD người ký", pii="basic", pii_kind="national_id"),
        FieldSpec(name="sdt_lien_he", label="SĐT liên hệ", pii="basic", pii_kind="phone"),
        FieldSpec(name="muc_luong", type="money", label="Mức lương", pii="sensitive", pii_kind="salary"),
        FieldSpec(name="ngay_ky", type="date", label="Ngày ký"),
        FieldSpec(name="ghi_chu", label="Ghi chú", pii="none"),
    ],
)

CATALOG = {m.name: m for m in (NHAN_VIEN, HOP_DONG)}
