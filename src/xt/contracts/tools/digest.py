# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""`tool_count` và `catalog_digest` cho metadata skill (architecture-v2 §4.3, REA §4)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable

from xt.contracts.tools.catalog import CATALOG
from xt.contracts.tools.contract import ToolContract


def catalog_digest(contracts: Iterable[ToolContract] = CATALOG) -> str:
    """sha256 của dạng docs (tên, schema, effects, lỗi...) đã chuẩn hóa, theo thứ tự catalog."""
    doc = [c.doc_view() for c in contracts]
    raw = json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def tool_count(contracts: Iterable[ToolContract] = CATALOG) -> int:
    return sum(1 for _ in contracts)
