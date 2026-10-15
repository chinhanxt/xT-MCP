# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""XRN `xrn:{tenant}:{type}:{id}` (docs/tool-contracts-v1.md): đúng 4 đoạn, không wildcard."""

from __future__ import annotations

import re
from typing import NamedTuple

_SEGMENT = re.compile(r"[A-Za-z0-9_-]{1,64}")


class Xrn(NamedTuple):
    tenant: str
    type: str
    id: str


def parse_xrn(value: str) -> Xrn | None:
    """Trả `None` nếu sai dạng (thừa/thiếu đoạn, ký tự lạ, `*`)."""
    parts = value.split(":")
    if len(parts) != 4 or parts[0] != "xrn":
        return None
    if not all(_SEGMENT.fullmatch(p) for p in parts[1:]):
        return None
    return Xrn(parts[1], parts[2], parts[3])
