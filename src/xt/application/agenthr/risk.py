# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Cảnh báo nâng quyền khi đổi JD (kế hoạch AgentHR A5, kiểu "prover" của OpenShell): hàm thuần
`diff_risk(jd_cũ, jd_mới)` -> danh sách cờ theo LOẠI, lưu vào `jd_agent.canh_bao` để người duyệt
(IT, OTP) thấy trước khi ký. JD cũ `None` = tuyển mới (so với JD rỗng, tự chủ L0, không hạn mức).

Cờ (tên cố định theo hợp đồng dữ liệu):
- `pii_reach_expansion`: thêm thao tác bất kỳ trên model có trường PII (`pii != none`).
- `money_reach_expansion`: thêm thao tác ghi (create/update/submit) trên model có trường `money`.
- `outbound_release`: thêm quyền nộp duyệt (`submit`) trên model chưa có — kênh ra khỏi nháp.
- `capability_expansion`: thêm bất kỳ cặp (model, thao tác) nào chưa có.
- `autonomy_raise`: mức tự chủ tăng.
- `limit_removed`: bỏ hoặc nới hạn mức cứng (số việc/ngày, giá trị/phiếu, khung giờ, ngày làm).
"""

from __future__ import annotations

from collections.abc import Collection

from xt.application.agenthr.gate import JobDescription, ScheduleError, parse_days, parse_window

FLAGS = (
    "pii_reach_expansion",
    "money_reach_expansion",
    "outbound_release",
    "capability_expansion",
    "autonomy_raise",
    "limit_removed",
)
WRITE_OPS = frozenset({"create", "update", "submit"})


def _added(old: JobDescription | None, new: JobDescription) -> dict[str, frozenset[str]]:
    before = old.capabilities if old is not None else {}
    out: dict[str, frozenset[str]] = {}
    for model, ops in new.capabilities.items():
        extra = frozenset(ops) - frozenset(before.get(model, frozenset()))
        if extra:
            out[model] = extra
    return out


def _relaxed(old_limit: int, new_limit: int) -> bool:
    """Hạn mức 0 = không giới hạn: cũ có giới hạn mà mới bỏ (0) hoặc cao hơn."""
    return old_limit > 0 and (new_limit <= 0 or new_limit > old_limit)


def _schedule_relaxed(old: JobDescription, new: JobDescription) -> bool:
    try:
        ow, nw = parse_window(old.window), parse_window(new.window)
        od, nd = parse_days(old.days), parse_days(new.days)
    except ScheduleError:
        return True  # lịch mới không đọc được: coi như bỏ giới hạn (người duyệt phải xem)
    if ow is not None and (nw is None or nw[0] < ow[0] or nw[1] > ow[1]):
        return True
    return od is not None and (nd is None or not nd <= od)


def diff_risk(
    old: JobDescription | None,
    new: JobDescription,
    *,
    pii_models: Collection[str] = (),
    money_models: Collection[str] = (),
) -> list[str]:
    added = _added(old, new)
    flags: set[str] = set()
    if any(m in pii_models for m in added):
        flags.add("pii_reach_expansion")
    if any(m in money_models and ops & WRITE_OPS for m, ops in added.items()):
        flags.add("money_reach_expansion")
    if any("submit" in ops for ops in added.values()):
        flags.add("outbound_release")
    if added:
        flags.add("capability_expansion")
    if new.autonomy > (old.autonomy if old is not None else 0):
        flags.add("autonomy_raise")
    if old is not None and (
        _relaxed(old.daily_limit, new.daily_limit)
        or _relaxed(old.value_limit, new.value_limit)
        or _schedule_relaxed(old, new)
    ):
        flags.add("limit_removed")
    return [f for f in FLAGS if f in flags]
