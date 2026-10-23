# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Port mà dispatcher gọi cho actor agent (chỉ khi `actor.is_agent`). Hiện thực ở composition root
đọc hồ sơ/JD/giấy phép bằng ngữ cảnh workflow; chặn = ném `ToolError` (`access_denied` có
`remediation`), mọi tác dụng phụ của lần chặn (audit `agenthr.block`, sự cố, tạm dừng) được ghi ở
transaction ngoài nên còn lại dù phần ghi của lời gọi rollback."""

from __future__ import annotations

from typing import Protocol

from xt.application.tools.context import CallContext
from xt.application.tools.ports import Args, MutationPlan


class AgentHrGate(Protocol):
    def check_call(self, ctx: CallContext, tool: str) -> None:
        """Mọi tool: trạng thái hồ sơ + ca làm. Không có hồ sơ -> không làm gì (hành vi cũ)."""
        ...

    def check_write(self, ctx: CallContext, tool: str, args: Args, plan: MutationPlan) -> None:
        """Tool ghi, trong transaction của lời gọi, TRƯỚC kiểm tự chủ: JD, giá trị, hạn mức ngày,
        giấy phép nộp duyệt; qua hết thì tăng bộ đếm ngày (rollback cùng lời gọi nếu lỗi sau)."""
        ...
