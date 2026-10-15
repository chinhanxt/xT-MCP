# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Poller timer start (G-P2), idempotent theo kỳ. xt-worker gọi `poll(now)` định kỳ (5 s).

Mỗi định nghĩa timer đang active, trong MỘT transaction:
1. `occ` = lần kích hoạt gần nhất ≤ now theo rrule + tz, và ≥ `not_before` (lúc kích hoạt);
2. `claim_period` (INSERT ... ON CONFLICT DO NOTHING). Không giành được -> worker khác đã xử lý;
3. giành được -> tính input từ biến kỳ, `runtime.start(fire=...)`, lưu instance CÙNG transaction.
   Lỗi -> rollback cả dòng kỳ, lần quét sau thử lại;
4. `catchup: latest` chỉ chạy kỳ gần nhất; `catchup: none` + trễ quá `grace` -> ghi `skipped`.
Một định nghĩa lỗi không chặn các định nghĩa khác (`result: error`).

Có `processes` (`ProcessService` của lớp ghép): instance tạo qua `ProcessService.start` (lưu vào
`InstanceStore` thật, cùng transaction của dòng kỳ); không có thì lưu qua `tx.save_instance`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from xt.application.process.ports import TimerFireStore
from xt.application.process.runtime import ProcessRuntime
from xt.domain.process.schedule import Schedule, timer_inputs
from xt.domain.process.spec import TimerTrigger, duration_seconds

if TYPE_CHECKING:
    from xt.application.process.service import ProcessService


@dataclass(frozen=True, slots=True)
class TimerDefinition:
    process_key: str
    runtime: ProcessRuntime
    not_before: datetime


def _short(e: BaseException) -> str:
    return f"{type(e).__name__}: {e}"[:300]


class TimerPoller:
    def __init__(
        self,
        store: TimerFireStore,
        definitions: Sequence[TimerDefinition],
        processes: ProcessService | None = None,
    ):
        self.store = store
        self.definitions = list(definitions)
        self.processes = processes

    def poll(self, now: datetime) -> list[dict[str, Any]]:
        if now.tzinfo is None:
            raise ValueError("now phải có múi giờ")
        return [r for d in self.definitions if (r := self._poll_one(d, now)) is not None]

    def _poll_one(self, d: TimerDefinition, now: datetime) -> dict[str, Any] | None:
        rt = d.runtime
        trig = rt.compiled.spec.trigger
        try:
            if not isinstance(trig, TimerTrigger):
                raise ValueError(f"{d.process_key} không phải quy trình định kỳ")
            sch = Schedule(trig)
            occ = sch.latest_at_or_before(now)
        except Exception as e:
            return {"process_key": d.process_key, "result": "error", "error": _short(e)}
        if occ is None or occ.timestamp() < d.not_before.timestamp():
            return None
        pk = occ.isoformat()
        late = (now - occ).total_seconds() > duration_seconds(trig.grace)
        status = "skipped" if (late and trig.catchup == "none") else "fired"
        out: dict[str, Any] = {"process_key": d.process_key, "period_key": pk}
        try:
            with self.store.transaction() as tx:
                if not tx.claim_period(
                    process_key=d.process_key, period_key=pk, status=status,
                    version=rt.compiled.version, fired_at=now,
                ):  # fmt: skip
                    return {**out, "result": "already"}
                iid = None
                if status == "fired":
                    inputs = timer_inputs(trig, sch.fire_context(occ))
                    fire = {"fire_at": pk, "period_key": pk}
                    if self.processes is not None:
                        started = self.processes.start(
                            rt.compiled.process_id, inputs, None,
                            fire=fire, version=rt.compiled.version,
                        )  # fmt: skip
                        iid = str(started["instance"])
                    else:
                        inst = rt.start(inputs, fire=fire)
                        iid = inst.id
                        tx.save_instance(
                            instance_id=iid, process_key=d.process_key,
                            version=rt.compiled.version, state=inst.to_state(),
                        )  # fmt: skip
                    tx.link_instance(process_key=d.process_key, period_key=pk, instance_id=iid)
        except Exception as e:
            return {**out, "result": "error", "error": _short(e)}
        return {**out, "result": status, "instance_id": iid}
