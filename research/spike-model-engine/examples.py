# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xT contributors
"""Spec mẫu do 'agent' soạn: Khách hàng + Đề xuất tạm ứng (W4)."""

V1 = {
    "version": 1,
    "models": [
        {
            "id": "m1",
            "name": "khach_hang",
            "label": "Khách hàng",
            "title_field": "ten",
            "fields": [
                {
                    "id": "f1",
                    "name": "ma_kh",
                    "type": "text",
                    "label": "Mã khách hàng",
                    "required": True,
                    "unique": True,
                    "regex": "^KH[0-9]{4}$",
                },
                {
                    "id": "f2",
                    "name": "ten",
                    "type": "text",
                    "label": "Tên khách hàng",
                    "required": True,
                    "min": 2,
                    "max": 200,
                },
                {
                    "id": "f3",
                    "name": "email",
                    "type": "text",
                    "label": "Email",
                    "regex": "^[^@ ]+@[^@ ]+\\.[a-z]{2,}$",
                },
                {
                    "id": "f4",
                    "name": "hang",
                    "type": "enum",
                    "label": "Hạng",
                    "options": [
                        {"value": "thuong", "label": "Thường"},
                        {"value": "vang", "label": "Vàng"},
                    ],
                },
                {"id": "f5", "name": "ghi_chu", "type": "text", "label": "Ghi chú"},
            ],
            "permissions": {
                "sales": {"actions": ["create", "read", "update"], "scope": "own"},
                "manager": {"actions": ["create", "read", "update", "delete"], "scope": "all"},
            },
        },
        {
            "id": "m2",
            "name": "de_xuat_tam_ung",
            "label": "Đề xuất tạm ứng",
            "title_field": "noi_dung",
            "fields": [
                {
                    "id": "f1",
                    "name": "noi_dung",
                    "type": "text",
                    "label": "Nội dung",
                    "required": True,
                    "min": 5,
                },
                {
                    "id": "f2",
                    "name": "so_tien",
                    "type": "money",
                    "label": "Số tiền (VND)",
                    "required": True,
                    "min": 100000,
                    "max": 500000000,
                },
                {
                    "id": "f3",
                    "name": "tu_ngay",
                    "type": "date",
                    "label": "Từ ngày",
                    "required": True,
                },
                {
                    "id": "f4",
                    "name": "den_ngay",
                    "type": "date",
                    "label": "Đến ngày",
                    "required": True,
                },
                {
                    "id": "f5",
                    "name": "khach_hang",
                    "type": "ref",
                    "ref": "khach_hang",
                    "label": "Khách hàng",
                },
                {
                    "id": "f6",
                    "name": "trang_thai",
                    "type": "enum",
                    "label": "Trạng thái",
                    "required": True,
                    "default": "nhap",
                    "options": [
                        {"value": "nhap", "label": "Nháp"},
                        {"value": "cho_duyet", "label": "Chờ duyệt"},
                        {"value": "da_duyet", "label": "Đã duyệt"},
                        {"value": "tu_choi", "label": "Từ chối"},
                    ],
                },
                {"id": "f7", "name": "nguoi_duyet", "type": "user", "label": "Người duyệt"},
                {"id": "f8", "name": "chung_tu", "type": "file", "label": "Chứng từ"},
                {"id": "f9", "name": "khan", "type": "bool", "label": "Khẩn", "default": False},
                {
                    "id": "f10",
                    "name": "thoi_diem_gui",
                    "type": "datetime",
                    "label": "Thời điểm gửi",
                },
                {"id": "f11", "name": "chi_tiet", "type": "json", "label": "Chi tiết"},
                {
                    "id": "f12",
                    "name": "ty_le_vat",
                    "type": "number",
                    "label": "Tỷ lệ VAT",
                    "min": 0,
                    "max": 1,
                },
            ],
            "indexes": [{"id": "i1", "fields": ["trang_thai", "tu_ngay"]}],
            "rules": [
                {
                    "id": "r1",
                    "message": "'Đến ngày' phải sau hoặc bằng 'Từ ngày'",
                    "check": {"field": "den_ngay", "op": ">=", "other_field": "tu_ngay"},
                },
                {
                    "id": "r2",
                    "message": "Đề xuất trên 50 triệu phải có người duyệt khi đã duyệt",
                    "check": {
                        "or": [
                            {"field": "so_tien", "op": "<=", "value": 50000000},
                            {"not": {"field": "trang_thai", "op": "=", "value": "da_duyet"}},
                            {"field": "nguoi_duyet", "op": "not_null"},
                        ]
                    },
                },
            ],
            "permissions": {
                "staff": {"actions": ["create", "read", "update"], "scope": "own"},
                "manager": {"actions": ["read", "update"], "scope": "department"},
                "accountant": {"actions": ["read"], "scope": "all"},
            },
        },
    ],
}
