# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""`xt org import --mapping` với seed/binh-minh thật + gói nhan-su-vong-doi: onboarding giao việc
HR / IT / Kế toán đúng người của seed (không rơi về fallback giám đốc)."""

from __future__ import annotations

import argparse
import json
import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest

from tests.integration.composition.fixtures import PACK, ROOT, StackBase, run_admin
from tests.integration.composition.idp import DESK_CLIENT
from tests.integration.composition.test_pack_ops import Ops
from xt.adapters.db.integration import connect, tenant_of_alias
from xt.application.tools.context import Actor
from xt.domain.identity import PrincipalKind
from xt.entrypoints.composition.runtime import Caller
from xt.entrypoints.serve import prepare

pytestmark = pytest.mark.integration

ALIAS = "bmseed"
SEED = ROOT / "seed" / "binh-minh"
PACKS = [PACK, ROOT / "packages" / "giao-viec-sla", ROOT / "packages" / "nhan-su-vong-doi"]


@dataclass
class SeedOps(Ops):
    def caller(self, key: str) -> tuple[Caller, Actor]:
        actor = Actor(ALIAS, f"kc-{ALIAS}-{key}", PrincipalKind.HUMAN, DESK_CLIENT, None)
        return self.rt.resolve(actor, f"it-{uuid.uuid4().hex[:8]}"), actor


def _active(hr: dict[str, Any], role: str) -> list[str]:
    return [e["id"] for e in hr["employees"] if e["active"] and role in e["roles"]]


@pytest.fixture(scope="module")
def hr() -> dict[str, Any]:
    return json.loads((SEED / "org.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def ops(
    stack_base: StackBase,
    mcp_port: int,
    hr: dict[str, Any],
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[SeedOps]:
    env = stack_base.env
    d = tmp_path_factory.mktemp("xt-seedmap")
    rc, out = run_admin(
        ["tenant", "create", "--alias", ALIAS, "--name", "Seed mapping (X)",
         "--timezone", "Asia/Ho_Chi_Minh", "--llm-egress", "masked"],
        env,
    )  # fmt: skip
    assert rc == 0, out
    (d / "links.json").write_text(
        json.dumps({"subjects": {e["id"]: f"kc-{ALIAS}-{e['id']}" for e in hr["employees"]},
                    "agent_grants": []}),
        encoding="utf-8",
    )  # fmt: skip
    rc, out = run_admin(
        ["org", "import", "--tenant", ALIAS, str(SEED / "org.json"), "--links",
         str(d / "links.json"), "--mapping", str(SEED / "mapping.json"),
         "--base-roles", "employee"],
        env,
    )  # fmt: skip
    assert rc == 0, out
    who = ["--proposed-by", "agent:it", "--approved-by", "person:admin"]
    plans: dict[str, str] = {}
    for pack in PACKS:
        common = ["pack", "install", "--tenant", ALIAS, str(pack)]
        rc, plan = run_admin([*common, *who], env)
        assert rc == 0 and "KHÔNG CÀI ĐƯỢC" not in plan, plan
        digest = re.search(r"Mã xác nhận \(digest\): (sha256:[0-9a-f]{64})", plan)
        assert digest, plan
        rc, done = run_admin([*common, *who, "--confirm", digest.group(1)], env)
        assert rc == 0, done
        plans[pack.name] = plan
    with connect(env["XT_DB_MIGRATOR_URL"]) as c:
        tenant = tenant_of_alias(c, ALIAS)
    ns = argparse.Namespace(transport="http", host="127.0.0.1", port=mcp_port, profile="full")
    prepared = prepare(env, ns)
    comp = prepared.services
    try:
        yield SeedOps(comp.runtime, comp.engine, tenant, plans)  # type: ignore[union-attr]
    finally:
        prepared.close()


def test_onboarding_assigns_seed_people_not_director(ops: SeedOps, hr: dict[str, Any]) -> None:
    hr_people = _active(hr, "hr")
    it_people = _active(hr, "it")
    acc_people = _active(hr, "accountant")
    director = _active(hr, "director")[0]
    newbie = next(
        e for e in hr["employees"] if e["active"] and e["roles"] == ["employee"] and e["manager_id"]
    )
    dept = ops.create(hr_people[0], "department", {"code": "KD", "name": "Kinh doanh"})
    emp = ops.create(
        hr_people[0], "employee",
        {"emp_no": newbie["id"], "user_id": ops.pid(newbie["id"]), "full_name": newbie["full_name"],
         "job_title": "Nhân viên", "department": dept["id"]},
    )  # fmt: skip
    case = ops.create(
        hr_people[0], "lifecycle_case",
        {"code": "ON-2026-0100", "employee": emp["id"], "kind": "onboarding",
         "effective_on": "2026-11-02", "initiated_by": ops.pid(hr_people[0])},
    )  # fmt: skip
    data = {"case_id": case["id"], "employee_id": emp["id"], "employee_user": ops.pid(newbie["id"])}
    iid, s = ops.start("employee_onboarding", data, hr_people[0])

    def cands(summary: dict[str, Any], step: str) -> set[str]:
        return set(next(t for t in summary["tasks"] if t["step"] == step)["candidates"])

    want = {"hr": hr_people, "it": it_people, "accounting": acc_people}
    assert cands(s, "hr") == {ops.pid(k) for k in hr_people}
    s = ops.act(iid, "hr", hr_people[0])
    for step in ("it", "accounting"):
        got = cands(s, step)
        assert got == {ops.pid(k) for k in want[step]}, (step, got)
        assert ops.pid(director) not in got
    ops.act(iid, "it", it_people[0])
    ops.act(iid, "accounting", acc_people[0])
    done = ops.act(iid, "manager", newbie["manager_id"])
    assert done["status"] == "completed", done
