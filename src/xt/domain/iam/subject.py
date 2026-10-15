# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Chủ thể mà IAM đánh giá. `Principal` là giao diện (Protocol) để lớp identity tự ánh xạ vào."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

HUMAN = "human"
AGENT = "agent"


@runtime_checkable
class Principal(Protocol):
    """Thuộc tính tối thiểu IAM cần. Vai trò/phòng ban/hạn mức lấy từ bảng tổ chức của xT,
    không từ token (architecture-v2 §5.1)."""

    @property
    def id(self) -> str: ...
    @property
    def tenant(self) -> str: ...
    @property
    def kind(self) -> str:
        """`human` hoặc `agent` (khớp `PrincipalKind`)."""
        ...

    @property
    def roles(self) -> Collection[str]: ...
    @property
    def team(self) -> str | None: ...
    @property
    def department(self) -> str | None: ...
    @property
    def limit(self) -> int | float | None: ...
    @property
    def autonomy(self) -> int:
        """Trần tự chủ của agent, 0..3 (L0..L3). Người: bỏ qua."""
        ...

    @property
    def behalf(self) -> str | None:
        """Id người mà agent đại diện."""
        ...


@dataclass(frozen=True, slots=True)
class Actor:
    """Hiện thực tham chiếu của `Principal` (dùng trong test và cho adapter chưa có kiểu riêng)."""

    id: str
    tenant: str
    kind: str = HUMAN
    roles: tuple[str, ...] = ()
    team: str | None = None
    department: str | None = None
    limit: int | float | None = None
    autonomy: int = 3
    behalf: str | None = None
