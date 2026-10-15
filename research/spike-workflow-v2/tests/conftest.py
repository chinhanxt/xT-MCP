# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
import copy
import json
import pathlib
import random
import sys
from datetime import datetime as _RealDT, timedelta

import pytest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))

import SpiffWorkflow.bpmn.specs.event_definitions.timer as TM  # noqa: E402

from xtwf.compiler import compile_process  # noqa: E402
from xtwf.org import InMemoryOrg  # noqa: E402
from xtwf.runtime import Runtime  # noqa: E402

FIX = ROOT / "fixtures"
PACKS = ROOT / "packs"

# allowlist op lấy đúng tên/tham số từ pack.json của research/packs-draft (bản chụp ở fixtures/)
OPS = {"post_to_ledger": ["amount", "requester"], "notify": ["msg"], "check_overdue_advances": ["requester"], "close_task": ["task_id"],
       "weekly_rollup": ["from_date", "to_date", "department"], "create_lifecycle_tasks": ["case_id", "kind"],
       "activate_employee": ["employee_id"], "check_unreturned_assets": ["employee_id"], "close_lifecycle_case": ["case_id"]}


class FakeDT(_RealDT):
    offset = timedelta(0)

    @classmethod
    def now(cls, tz=None):
        return _RealDT.now(tz) + cls.offset


TM.datetime = FakeDT   # đồng hồ giả cho timer SLA/timer start (giống harness của packs-draft)


@pytest.fixture
def clock():
    FakeDT.offset = timedelta(0)

    class C:
        def advance(self, **kw):
            FakeDT.offset += timedelta(**kw)

        def set(self, dt):
            FakeDT.offset = dt - _RealDT.now(dt.tzinfo)
    yield C()
    FakeDT.offset = timedelta(0)


@pytest.fixture
def org():
    return InMemoryOrg.from_seed(json.loads((FIX / "org_users.json").read_text()), json.loads((FIX / "org_nhan_vien.json").read_text()))


class Services(dict):
    def __init__(self):
        super().__init__()
        self.ledger, self.notes, self.calls, self.overdue, self.keys = [], [], [], {}, []
        self.unreturned = 0

        def post_to_ledger(amount, requester, idempotency_key):
            self.keys.append(idempotency_key)
            self.ledger.append((requester, amount))
            return {"so": f"PC-{len(self.ledger):04d}"}

        def notify(msg):
            self.notes.append(msg)
            return {"ok": 1}

        def simple(name, **ret):
            def f(**kw):
                self.calls.append((name, kw))
                return dict(ret)
            return f
        self.update({
            "post_to_ledger": post_to_ledger, "notify": notify,
            "check_overdue_advances": lambda requester: (self.calls.append(("check_overdue_advances", requester)), {"overdue": self.overdue.get(requester, 0)})[1],
            "close_task": simple("close_task", closed=True),
            "weekly_rollup": lambda from_date, to_date, department: (self.calls.append(("weekly_rollup", from_date, to_date)),
                                                                     {"code": f"BT-{to_date}", "summary": f"Tuần {from_date}..{to_date}", "id": "x"})[1],
            "create_lifecycle_tasks": simple("create_lifecycle_tasks", task_count=4),
            "activate_employee": simple("activate_employee", state="probation"),
            "check_unreturned_assets": lambda employee_id: {"unreturned": self.unreturned, "active_grants": 0},
            "close_lifecycle_case": simple("close_lifecycle_case", closed=True),
        })


@pytest.fixture
def services():
    return Services()


def load_pack(name):
    return json.loads((PACKS / f"{name}.json").read_text())


@pytest.fixture
def make_rt(org, services):
    def mk(spec, admin_role="admin", events=None):
        c = compile_process(load_pack(spec) if isinstance(spec, str) else spec, OPS)
        return Runtime(c, services, org, admin_role=admin_role, on_event=(events.append if events is not None else None))
    return mk


def jsonb_roundtrip(state, seed=0):
    """Mô phỏng JSONB: thứ tự key bị đổi (sắp xếp + xáo trộn tasks)."""
    s = json.loads(json.dumps(state, sort_keys=True))
    items = list(s["wf"]["tasks"].items())
    random.Random(seed).shuffle(items)
    s["wf"]["tasks"] = dict(items)
    return copy.deepcopy(s)


def only(inst, user=None):
    box = inst.inbox(user)
    assert len(box) == 1, box
    return box[0]
