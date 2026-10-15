# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Dựng spec mẫu cho test model engine."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from xt.domain.model.spec import Catalog

ROOT = Path(__file__).resolve().parents[3]
PACKS = ("de-xuat-phe-duyet", "giao-viec-sla", "nhan-su-vong-doi")


def pack_models(*packs: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in packs:
        for f in sorted((ROOT / "packages" / p / "models").glob("*.json")):
            out.append(json.loads(f.read_text(encoding="utf-8")))
    return out


def customer(**over: Any) -> dict[str, Any]:
    m: dict[str, Any] = {
        "id": "m1",
        "name": "customer",
        "label": "Khách hàng",
        "fields": [
            {
                "id": "f1",
                "name": "code",
                "type": "text",
                "label": "Mã KH",
                "required": True,
                "unique": True,
                "regex": "^KH[0-9]{4}$",
            },
            {
                "id": "f2",
                "name": "full_name",
                "type": "text",
                "label": "Tên",
                "required": True,
                "max": 100,
            },
            {"id": "f3", "name": "email", "type": "text", "label": "Email"},
            {
                "id": "f4",
                "name": "tier",
                "type": "enum",
                "label": "Hạng",
                "default": "basic",
                "required": True,
                "options": [
                    {"value": "basic", "label": "Thường"},
                    {"value": "gold", "label": "Vàng"},
                ],
            },
            {
                "id": "f5",
                "name": "credit",
                "type": "money",
                "label": "Hạn mức",
                "min": 0,
                "max": 1000000000,
            },
            {"id": "f6", "name": "note", "type": "text", "label": "Ghi chú"},
        ],
        "indexes": [{"id": "i1", "fields": ["tier"]}],
        "rules": [],
        "permissions": {
            "sales": {"actions": ["create", "read", "update"], "scope": "own"},
            "manager": {"actions": ["read"], "scope": "all"},
        },
    }
    m.update(over)
    return m


def catalog(*models: dict[str, Any], version: int = 1) -> Catalog:
    return Catalog.model_validate(
        {"version": version, "models": [copy.deepcopy(m) for m in models]}
    )


def bump(cat: Catalog, edit: Any) -> Catalog:
    """Bản sao catalog version+1 sau khi `edit(dict)` sửa tại chỗ."""
    d = cat.dump()
    d["version"] += 1
    edit(d)
    return Catalog.model_validate(d)


def field(d: dict[str, Any], model: str, name: str) -> dict[str, Any]:
    m = next(x for x in d["models"] if x["name"] == model)
    return next(f for f in m["fields"] if f["name"] == name)


def model(d: dict[str, Any], name: str) -> dict[str, Any]:
    return next(x for x in d["models"] if x["name"] == name)
