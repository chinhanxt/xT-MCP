# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Tích hợp thật: poller timer start với 8 worker trên Postgres 17; state lưu cột `json` rồi nạp
lại vẫn chạy tiếp; BPMN của 6 pack đúng cấu trúc BPMN 2.0 + DI theo bpmn-moddle."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from tests.unit.process.support import OPS, PACK_NAMES, Clock, Services, load_pack
from xt.adapters.db.process_timers import DbApiTimerFireStore
from xt.adapters.workflow.bpmn.compiler import compile_process
from xt.application.process.runtime import ProcessRuntime
from xt.application.process.timers import TimerDefinition, TimerPoller

pytestmark = pytest.mark.integration

HCM = ZoneInfo("Asia/Ho_Chi_Minh")
NOT_BEFORE = datetime(2026, 10, 1, tzinfo=HCM)
VIEWER = Path(__file__).resolve().parent / "bpmn_viewer"


def test_poller_concurrent_workers_postgres(
    pg_dsn: str, make_rt: Callable[..., ProcessRuntime], services: Services, clock: Clock
) -> None:
    import psycopg

    digest = make_rt("ban_tin_tuan")
    with psycopg.connect(pg_dsn, autocommit=True) as c:
        c.execute("DROP TABLE IF EXISTS wf_timer_fire, wf_instance")
    DbApiTimerFireStore(psycopg.connect(pg_dsn), "postgres").ensure_schema()
    now = datetime(2026, 10, 19, 8, 3, tzinfo=HCM)
    clock.set(now)
    barrier = threading.Barrier(8)
    results: list[dict[str, Any]] = []
    errs: list[BaseException] = []

    def work() -> None:
        try:
            store = DbApiTimerFireStore(psycopg.connect(pg_dsn), "postgres")
            p = TimerPoller(store, [TimerDefinition("weekly_digest", digest, NOT_BEFORE)])
            barrier.wait()
            results.extend(p.poll(now))
        except Exception as e:  # pragma: no cover
            errs.append(e)

    ts = [threading.Thread(target=work) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errs, errs
    assert sorted(r["result"] for r in results) == ["already"] * 7 + ["fired"]
    assert len(services.calls) == 1
    with psycopg.connect(pg_dsn) as c:
        assert c.execute("SELECT count(*) FROM wf_instance").fetchone() == (1,)
        assert c.execute("SELECT count(*) FROM wf_timer_fire").fetchone() == (1,)
        typ = c.execute(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name='wf_instance' AND column_name='state'"
        ).fetchone()
        assert typ == ("json",)  # không phải jsonb (giữ thứ tự key)


def test_state_roundtrip_through_postgres_json_and_jsonb(
    pg_dsn: str, make_rt: Callable[..., ProcessRuntime], services: Services
) -> None:
    """State lưu `json` (giữ thứ tự) và cả `jsonb` (sắp lại key): `_reorder` vẫn nạp đúng
    giữa các nhánh song song."""
    import psycopg

    rt = make_rt("nv_onboarding")
    i = rt.start({"case_id": "HS-1", "employee_id": "E-15", "employee_user": "NV-0015"}, "NV-0010")
    i.act(i.inbox()[0]["task_id"], "NV-0010", "done")
    with psycopg.connect(pg_dsn, autocommit=True) as c:
        c.execute("CREATE TABLE IF NOT EXISTS it_state (k text PRIMARY KEY, j json, jb jsonb)")
        for step, who in (("manager", "NV-0005"), ("it", "NV-0013"), ("accounting", "NV-0003")):
            raw = json.dumps(i.to_state(), ensure_ascii=False)
            c.execute(
                "INSERT INTO it_state VALUES ('s', %s, %s) ON CONFLICT (k) DO UPDATE "
                "SET j = excluded.j, jb = excluded.jb",
                (raw, raw),
            )
            row = c.execute("SELECT j::text, jb::text FROM it_state WHERE k='s'").fetchone()
            assert row is not None
            j_state, jb_state = (json.loads(x) for x in row)
            assert list(jb_state["wf"]["tasks"]) != list(j_state["wf"]["tasks"])  # JSONB đổi thứ tự
            i = rt.load(jb_state)
            t = next(x for x in i.inbox() if x["step"] == step)
            i.act(t["task_id"], who, "done")
    assert i.status() == "completed"
    assert [c for c in services.calls if c[0] == "activate_employee"] == [
        ("activate_employee", {"employee_id": "E-15"})
    ]


def _node() -> str | None:
    found = shutil.which("node")
    if found:
        return found
    nvm = Path.home() / ".nvm" / "versions" / "node"
    cands = sorted(nvm.glob("*/bin/node")) if nvm.exists() else []
    return str(cands[-1]) if cands else None


def _moddle_check(tmp_path: Path, xmls: dict[str, str]) -> tuple[int, list[dict[str, Any]]]:
    node = _node()
    run_dir = os.environ.get("XT_BPMN_MODDLE_DIR")
    if not node or not run_dir or not (Path(run_dir) / "node_modules" / "bpmn-moddle").exists():
        pytest.skip(
            "cần node + XT_BPMN_MODDLE_DIR có node_modules/bpmn-moddle: "
            "cp tests/integration/process/bpmn_viewer/package*.json <dir>/ && (cd <dir> && npm ci)"
        )
    shutil.copy(VIEWER / "check.mjs", Path(run_dir) / "check.mjs")
    files = []
    for name, xml in xmls.items():
        f = tmp_path / f"{name}.bpmn"
        f.write_text(xml)
        files.append(str(f))
    r = subprocess.run(
        [node, str(Path(run_dir) / "check.mjs"), *files],
        capture_output=True, text=True, timeout=120, check=False,
    )  # fmt: skip
    return r.returncode, [json.loads(line) for line in r.stdout.splitlines()]


def test_bpmn_di_structure_valid_per_bpmn_moddle(tmp_path: Path) -> None:
    """BPMN của 6 pack: bpmn-moddle (MIT) không cảnh báo, mọi node có shape, mọi flow có edge,
    mỗi node thuộc đúng 1 lane. Mẫu cố ý làm hỏng phải bị bắt."""
    xmls = {}
    for n in PACK_NAMES:
        c = compile_process(load_pack(n), OPS)
        xmls[c.process_id] = c.bpmn
    code, res = _moddle_check(tmp_path, xmls)
    assert code == 0 and len(res) == 6, res
    assert all(x["ok"] and x["shapes"] > 3 and x["edges"] >= 3 for x in res), res
    good = xmls["proposal_approval"]
    broken = good.replace('bpmnElement="ut_ktt"', 'bpmnElement="khong_co"', 1)
    code, res = _moddle_check(tmp_path, {"broken": broken})
    assert code == 1 and not res[0]["ok"] and any("ut_ktt" in e for e in res[0]["errors"]), res
