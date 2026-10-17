# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Audit lớp MCP vòng 3 (docs/audit-mcp.md "Vòng 3"): kiểm lại R2-H1/M1-M4/L1-L3 sau các thay đổi
mới (start_process, notify, hoàn thành bước người, DPoP, aud ngoại) trên đường thật.

Hạ tầng: Postgres 17 (fixture phiên, một container, `docker rm -f -v`), IdP test (JWKS thật, RS256),
`prepare()` + uvicorn thật (ba app: full, core-10, full+XT_DPOP_REQUIRED), client `mcp` qua
Streamable HTTP và httpx thô. Tenant riêng `mr3`.

Quy ước: test khẳng định HÀNH VI AN TOÀN. Lỗi còn mở đánh `xfail(strict=True)`.

Chạy: uv run pytest -m integration --no-cov -rA tests/integration/mcp_audit_r3
"""

from __future__ import annotations

import argparse
import json
import random
import threading
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import httpx2
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from tests.integration.composition import pgfixture
from tests.integration.composition.fixtures import (
    DESK_AGENT,
    LOCAL_AGENT,
    PACK,
    PII_PACK,
    ROOT,
    StackBase,
    run_admin,
)
from tests.integration.composition.idp import AGENT_CLIENT, DESK_CLIENT, LocalIdp
from tests.integration.composition.mcphttp import RunningApp, envelope, free_port, serve
from tests.integration.composition.shared_tenants import DEPTS, PEOPLE
from tests.unit.identity.dpop_client import DpopKey
from xt.adapters.db.integration import connect, person_id, tenant_of_alias
from xt.application.tools import Actor, ClientClass, ConfirmationService
from xt.application.tools.context import CallContext
from xt.application.tools.ports import PendingStatus
from xt.contracts.tools import CATALOG
from xt.contracts.tools.apps import APP_TOOLS_BY_NAME
from xt.contracts.tools.profiles import CORE_10
from xt.domain.identity import PrincipalKind
from xt.entrypoints.serve import ServeConfigError, load_serve_config, prepare

pytestmark = pytest.mark.integration

ALIAS = "mr3"
TASK_PACK = ROOT / "packages" / "giao-viec-sla"
HR_PACK = ROOT / "packages" / "nhan-su-vong-doi"
NOTIFY_PACK = ROOT / "packages" / "thong-bao"
JSONRPC = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}

CCCD, PHONE, EMAIL = "079123456789", "0912345678", "le.van.phuc@example.vn"
RAW_PII = (CCCD, PHONE, EMAIL, "079-123-456-789", "912345678")

CORE_10_V01 = (
    "find_people",
    "search_records",
    "aggregate_records",
    "get_record",
    "get_inbox",
    "create_record",
    "create_task",
    "submit_request",
    "transition_record",
    "decide_request",
)


def _tenant(env: dict[str, str], d: Path, alias: str, packs: list[Path]) -> Any:
    rc, out = run_admin(
        ["tenant", "create", "--alias", alias, "--name", f"Audit {alias}",
         "--timezone", "Asia/Ho_Chi_Minh", "--llm-egress", "masked"],
        env,
    )  # fmt: skip
    assert rc == 0, out
    org = {
        "departments": [{"id": k, "name": n, "parent_id": None} for k, n in DEPTS],
        "positions": [],
        "employees": [
            {"id": k, "full_name": n, "email": f"{k.lower()}@{alias}.example", "department_id": u,
             "position_id": None, "manager_id": m, "active": True, "roles": r}
            for k, n, u, m, r in PEOPLE
        ],
    }  # fmt: skip
    (d / f"org-{alias}.json").write_text(json.dumps(org, ensure_ascii=False), encoding="utf-8")
    links = {
        "subjects": {k: f"kc-{alias}-{k}" for k, *_ in PEOPLE},
        "agent_grants": [
            {"person": k, "client_id": c, "autonomy": 1, "roles": ["agent"]}
            for k, *_ in PEOPLE
            for c in env["XT_MCP_ALLOWED_CLIENTS"].split(",")
        ],
    }
    (d / f"links-{alias}.json").write_text(json.dumps(links), encoding="utf-8")
    rc, out = run_admin(
        ["org", "import", "--tenant", alias, str(d / f"org-{alias}.json"), "--links",
         str(d / f"links-{alias}.json"), "--base-roles", "employee"],
        env,
    )  # fmt: skip
    assert rc == 0, out
    import re

    who = ["--proposed-by", "agent:it", "--approved-by", "person:admin"]
    for pack in packs:
        common = ["pack", "install", "--tenant", alias, str(pack)]
        rc, plan = run_admin([*common, *who], env)
        assert rc == 0 and "KHÔNG CÀI ĐƯỢC" not in plan, plan
        digest = re.search(r"\(digest\): (sha256:[0-9a-f]{64})", plan)
        assert digest, plan
        rc, done = run_admin([*common, *who, "--confirm", digest.group(1)], env)
        assert rc == 0, done
    with connect(env["XT_DB_MIGRATOR_URL"]) as c:
        return tenant_of_alias(c, alias)


@dataclass
class W:
    idp: LocalIdp
    app: RunningApp
    core: RunningApp
    strict: RunningApp
    prepared: Any
    tenant: Any
    env: dict[str, str]
    pg: pgfixture.Pg
    notes: dict[str, Any] = field(default_factory=dict)

    def sub(self, key: str) -> str:
        return f"kc-{ALIAS}-{key}"

    def pid(self, key: str) -> str:
        return str(person_id(self.tenant.id, key))

    def token(self, key: str, client: str = AGENT_CLIENT, **over: Any) -> str:
        return self.idp.access_token(self.sub(key), org=ALIAS, azp=client, **over)

    @asynccontextmanager
    async def session(
        self, token: str, app: RunningApp | None = None, auth: Any = None
    ) -> AsyncIterator[tuple[ClientSession, Any]]:
        a = app or self.app
        headers = {} if auth is not None else {"Authorization": f"Bearer {token}"}
        async with (
            httpx2.AsyncClient(headers=headers, auth=auth, timeout=60) as http,
            streamable_http_client(a.mcp_url, http_client=http) as (read, write),
            ClientSession(read, write) as s,
        ):
            init = await s.initialize()
            yield s, init

    async def call(
        self, key: str, tool: str, args: dict[str, Any], client: str = AGENT_CLIENT,
        app: RunningApp | None = None,
    ) -> dict[str, Any]:  # fmt: skip
        async with self.session(self.token(key, client), app) as (s, _):
            return envelope(await s.call_tool(tool, args))

    def post(
        self, body: bytes, token: str, app: RunningApp | None = None, **headers: str
    ) -> httpx.Response:
        a = app or self.app
        return httpx.post(
            a.mcp_url,
            content=body,
            headers={**JSONRPC, "Authorization": f"Bearer {token}", **headers},
            timeout=120,
        )

    def ctx(self, key: str, now: datetime, client: str = AGENT_CLIENT) -> CallContext:
        actor = Actor(ALIAS, self.sub(key), PrincipalKind.AGENT, client, self.sub(key))
        return CallContext(actor, ClientClass.CLOUD, f"r3-{uuid.uuid4().hex[:8]}", now)


@pytest.fixture(scope="module")
def w(
    stack_base: StackBase,
    pg: pgfixture.Pg,
    idp: LocalIdp,
    mcp_port: int,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[W]:
    env = dict(stack_base.env)
    d = tmp_path_factory.mktemp("xt-mr3")
    tenant = _tenant(env, d, ALIAS, [PACK, TASK_PACK, HR_PACK, NOTIFY_PACK, PII_PACK])
    full = prepare(env, argparse.Namespace(transport="http", host="127.0.0.1", port=mcp_port,
                                           profile="full"))  # fmt: skip
    core_port, strict_port = free_port(), free_port()
    core = prepare(env, argparse.Namespace(transport="http", host="127.0.0.1", port=core_port,
                                           profile="core-10"))  # fmt: skip
    strict_env = {**env, "XT_DPOP_REQUIRED": "true", "XT_DPOP_CLIENTS": DESK_AGENT}
    strict = prepare(strict_env, argparse.Namespace(transport="http", host="127.0.0.1",
                                                    port=strict_port, profile="full"))  # fmt: skip
    apps = [
        serve(full.http_app(), mcp_port),
        serve(core.http_app(), core_port),
        serve(strict.http_app(), strict_port),
    ]
    try:
        world = W(idp, apps[0], apps[1], apps[2], full, tenant, env, pg)
        yield world
        out = Path("/tmp/xt-audit-r3")
        out.mkdir(exist_ok=True)
        (out / "notes.json").write_text(
            json.dumps(world.notes, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
        )
    finally:
        for a in apps:
            a.stop()
        for p in (full, core, strict):
            p.close()


def _leaks(env: dict[str, Any]) -> list[str]:
    text = json.dumps(env, ensure_ascii=False)
    return [p for p in RAW_PII if p in text]


def _err(env: dict[str, Any]) -> tuple[str, Any]:
    assert not env.get("ok"), env
    e = env["error"]
    return e["code"], (e.get("details") or {}).get("reason")


# =========================================== 1. R2-H1: khóa trùng không chặn event loop ====
def _dup_body(target: int, dup: bool = True) -> bytes:
    head = (
        '{"jsonrpc":"2.0","id":7,"method":"tools/call",'
        + '"params":{"name":"get_record","arguments":{'
    )
    parts: list[str] = []
    size, i = len(head) + 4, 0
    while True:
        p = f'"k{i}":0,"k{i}":0' if dup else f'"k{i}":0'
        if size + len(p) + 1 > target:
            break
        parts.append(p)
        size += len(p) + 1
        i += 1
    return (head + ",".join(parts) + "}}}").encode()


def _probe_while(w: W, fn: Any) -> tuple[Any, list[float]]:
    """Gọi /healthz mỗi 20 ms trong lúc `fn()` chạy; trả kết quả + độ trễ từng lần."""
    lat: list[float] = []
    stop = threading.Event()

    def probe() -> None:
        with httpx.Client(base_url=w.app.base, timeout=60) as c:
            while not stop.is_set():
                t = time.perf_counter()
                c.get("/healthz")
                lat.append(time.perf_counter() - t)
                time.sleep(0.02)

    th = threading.Thread(target=probe, daemon=True)
    th.start()
    time.sleep(0.2)
    try:
        out = fn()
    finally:
        stop.set()
        th.join(10)
    return out, lat


def _parallel(n: int, fn: Any) -> list[Any]:
    out: list[Any] = [None] * n

    def run(i: int) -> None:
        out[i] = fn()

    ths = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for t in ths:
        t.start()
    for t in ths:
        t.join(300)
    return out


def _p95(lat: list[float]) -> float:
    return sorted(lat)[max(0, int(len(lat) * 0.95) - 1)]


def test_r2h1_4mib_duplicate_keys_keep_event_loop_responsive(w: W) -> None:
    """Sau R3-M1: /mcp có trần 1,5 MiB ở biên ASGI (1 MiB tệp inline + envelope), nên body 4 MiB
    bị 413 trước khi parse. Khóa trùng trong body hợp lệ vẫn ra `duplicate_key`."""
    body = _dup_body(4 * 1024 * 1024 - 64)
    tok = w.token("NV-0010")

    def big() -> tuple[int, float]:
        t = time.perf_counter()
        r = w.post(body, tok)
        return r.status_code, time.perf_counter() - t

    res, lat = _probe_while(w, lambda: _parallel(4, big))
    w.notes["dup4"] = {
        "req_s": [round(x[1], 2) for x in res],
        "healthz_max_s": round(max(lat), 3),
        "healthz_p95_s": round(_p95(lat), 3),
    }
    print("R3 dup4:", w.notes["dup4"])
    assert all(status == 413 and secs < 5 for status, secs in res), res
    assert max(lat) < 1.0, w.notes["dup4"]
    small = _dup_body(16 * 1024)
    r = w.post(small, tok)
    tool = json.loads(r.json()["result"]["content"][0]["text"])
    assert tool["error"]["details"]["reason"] == "duplicate_key"
    assert len(tool["error"]["details"]["keys"]) <= 20


def test_r2h1_4mib_unique_keys_keep_event_loop_responsive(w: W) -> None:
    """R3-M1: 16 request đồng thời. 4 MiB -> 413; ~1 MiB nhiều khóa -> 400 (trần số phần tử, trước
    SDK); ~1 MiB hợp lệ tối đa (upload_file base64) đi vào SDK. /healthz p95 < 200 ms."""
    tok = w.token("NV-0010")
    over = _dup_body(4 * 1024 * 1024 - 64, dup=False)
    many = _dup_body(1024 * 1024 - 64, dup=False)
    legal = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/call",
            "params": {
                "name": "upload_file",
                "arguments": {
                    "name": "a.bin",
                    "source": "base64",
                    "base64": "A" * 1398100,
                    "idempotency_key": "r3-big-0001",
                },
            },
        }
    ).encode()

    def run(body: bytes) -> Any:
        def one() -> tuple[int, float, int]:
            t = time.perf_counter()
            r = w.post(body, tok)
            return r.status_code, time.perf_counter() - t, len(r.content)

        return _probe_while(w, lambda: _parallel(16, one))

    notes: dict[str, Any] = {}
    for label, body, want in (("over4", over, 413), ("many1", many, 400), ("legal1", legal, 200)):
        res, lat = run(body)
        notes[label] = {
            "body_mib": round(len(body) / 2**20, 2),
            "status": sorted({x[0] for x in res}),
            "req_max_s": round(max(x[1] for x in res), 2),
            "resp_max_bytes": max(x[2] for x in res),
            "healthz_p95_s": round(_p95(lat), 3),
            "healthz_max_s": round(max(lat), 3),
        }
        print("R3", label, notes[label])
        assert all(x[0] == want for x in res), notes[label]
        assert all(x[2] < 64 * 1024 for x in res)
        assert _p95(lat) < 0.2, notes[label]
    w.notes["uniq"] = notes


def test_r2l1_duplicates_outside_tools_call_and_lone_surrogate(w: W) -> None:
    tok = w.token("NV-0010")
    r = w.post(b'{"jsonrpc":"2.0","id":1,"id":2,"method":"tools/list"}', tok)
    assert r.status_code == 400 and r.json()["error"]["code"] == -32600, r.text
    body = (
        b'{"jsonrpc":"2.0","id":"\\udfff","method":"tools/call","params":{"name":"get_record",'
        b'"arguments":{"ref":"a:b","ref":"a:c"}}}'
    )
    r = w.post(body, tok)
    assert r.status_code < 500 and "Traceback" not in r.text, r.text


# =============================================== 2. R2-M1/M2: PII trong mọi đường lỗi ====
@pytest.mark.anyio
async def test_pii_in_start_process_errors_cloud(w: W) -> None:
    cases: list[dict[str, Any]] = [
        {"process_id": "task_normal",
         "parameters": {"assignee_user": f"CCCD {CCCD}", "assigner_user": PHONE,
                        "task_id": EMAIL}},
        {"process_id": "task_normal", "parameters": {"assignee_user": [CCCD, PHONE]}},
        {"process_id": "employee_onboarding",
         "parameters": {"employee_user": f"person:{uuid.uuid4()}", "case_id": f"so_cccd:{CCCD}",
                        "employee_id": f"id-{PHONE}"}},
        {"process_id": "khong_ton_tai", "subject_ref": f"employee:{uuid.uuid4()}"},
        {"process_id": "task_normal", "subject_ref": f"task:{uuid.uuid4()}",
         "parameters": {"task_id": f"{EMAIL} {CCCD}"}},
    ]  # fmt: skip
    seen = []
    for i, args in enumerate(cases):
        out = await w.call(
            "NV-0009", "start_process", {**args, "idempotency_key": f"r3-sp-{i:04d}"}
        )
        seen.append((_err(out), out["error"]["message"][:120]))
        assert not _leaks(out), out
    w.notes["start_process_errors"] = seen
    print("R3 start_process:", seen)


@pytest.mark.anyio
async def test_pii_in_transition_record_step_errors_cloud(w: W) -> None:
    """Lỗi hoàn thành bước người (to_state sai, values sai/lạ, lý do, ref lạ) với client cloud."""
    task = await _task_with_instance(w)
    ref, rev = f"task:{task['id']}", int(task["revision"])
    probe = await w.call("NV-0010", "transition_record", {
        "ref": ref, "to_state": "khong_co", "expected_revision": rev, "reason": CCCD,
        "values": {"result_note": f"SĐT {PHONE}"}, "idempotency_key": "r3-tr-0000"})  # fmt: skip
    assert not _leaks(probe), probe
    seen = [_err(probe)]
    states = probe["error"]["details"].get("available_transitions") or []
    assert states, probe
    st = states[0]
    cases = [
        {"to_state": st, "values": {"deadline": f"{CCCD}"}},
        {"to_state": st, "values": {f"f{CCCD}": PHONE, "khong_ton_tai": EMAIL}},
        {"to_state": st, "values": {"state": PHONE}},
        {"to_state": st, "expected_revision": rev + 7, "reason": f"{EMAIL}"},
        *({"to_state": x} for x in states[1:]),
    ]
    for i, extra in enumerate(cases):
        args = {"ref": ref, "expected_revision": rev, "idempotency_key": f"r3-tr-{i + 1:04d}",
                **extra}  # fmt: skip
        out = await w.call("NV-0010", "transition_record", args)
        if out.get("ok"):
            seen.append(("ok", extra["to_state"]))
            continue
        seen.append(_err(out))
        rest = {k: v for k, v in out["error"].items() if k != "continuation"}
        assert not _leaks(rest), json.dumps(rest, ensure_ascii=False)
    out = await w.call("NV-0010", "transition_record", {
        "ref": f"task:{uuid.uuid4()}", "to_state": "done", "expected_revision": 1,
        "reason": f"{CCCD} {PHONE}", "idempotency_key": "r3-tr-9999"})  # fmt: skip
    seen.append(_err(out))
    assert not _leaks(out), out
    w.notes["transition_errors"] = seen
    print("R3 transition:", seen)


async def _task_with_instance(w: W) -> dict[str, Any]:
    """Việc KD do NV-0009 giao NV-0010 + instance task_normal gắn subject."""
    if "task" in w.notes:
        return dict(w.notes["task"])
    comp = w.prepared.services
    rt = comp.runtime

    def mk(who: str, model: str, values: dict[str, Any]) -> dict[str, Any]:
        actor = Actor(ALIAS, w.sub(who), PrincipalKind.HUMAN, DESK_CLIENT, None)
        caller = rt.resolve(actor, "r3-setup")
        out = rt.run_as(caller, actor, lambda: rt.records.create(caller.actor, model, values))
        return dict(out["record"])

    kind = mk("NV-0001", "task_type", {"code": "r3", "name": "R3"})
    task = mk("NV-0009", "task", {
        "task_no": "CV-2026-7301", "title": "Báo cáo R3", "kind": kind["id"],
        "assigner": w.pid("NV-0009"), "assignee": w.pid("NV-0010"),
        "deadline": "2099-12-31T17:00:00+07:00", "done_criteria": "Đủ số liệu"})  # fmt: skip
    out = await w.call("NV-0009", "start_process", {
        "process_id": "task_normal", "subject_ref": f"task:{task['id']}",
        "parameters": {"assignee_user": f"person:{w.pid('NV-0010')}",
                       "assigner_user": f"person:{w.pid('NV-0009')}"},
        "idempotency_key": "r3-start-task-1"})  # fmt: skip
    assert out["ok"], json.dumps(out, ensure_ascii=False)
    actor = Actor(ALIAS, w.sub("NV-0001"), PrincipalKind.HUMAN, DESK_CLIENT, None)
    caller = rt.resolve(actor, "r3-setup")
    rec = rt.run_as(caller, actor, lambda: rt.records.get(
        caller.actor, "task", uuid.UUID(str(task["id"]))))["record"]  # fmt: skip
    task = {"id": task["id"], "revision": rec["revision"]}
    w.notes["task"] = task
    return task


@pytest.mark.anyio
async def test_pii_in_notify_errors_cloud(w: W) -> None:
    cases = [
        {"recipients": [f"person:{uuid.uuid4()}"], "message": f"Gọi {PHONE}"},
        {"recipients": [f"xrn:khac:person:{uuid.uuid4()}"], "message": CCCD},
        {"recipients": [f"person:{w.pid('NV-0010')}"], "message": f"{EMAIL} {CCCD}",
         "subject_ref": f"task:{uuid.uuid4()}"},
    ]  # fmt: skip
    seen = []
    for i, args in enumerate(cases):
        out = await w.call("NV-0009", "notify", {**args, "idempotency_key": f"r3-nt-{i:04d}"})
        seen.append(_err(out))
        assert not _leaks(out), out
    # Thành công tới bước xác nhận: plan_summary/details không echo; continuation = args của agent.
    out = await w.call("NV-0009", "notify", {
        "recipients": [f"person:{w.pid('NV-0010')}"], "message": f"Nhắc {PHONE} {CCCD}",
        "idempotency_key": "r3-nt-0100"})  # fmt: skip
    code, _ = _err(out)
    seen.append((code, None))
    if code == "confirmation_required":
        rest = {k: v for k, v in out["error"].items() if k != "continuation"}
        assert not _leaks(rest), out
        again = await w.call("NV-0009", out["error"]["continuation"]["tool"],
                             out["error"]["continuation"]["arguments"])  # fmt: skip
        assert again["error"]["code"] == "confirmation_required", again  # R2-M2: gọi lại được
    w.notes["notify_errors"] = seen
    print("R3 notify:", seen)


# ============================== 3. R2-M3: confirm vs expire trên PgOperationStore thật ====
ROUNDS = 100


def _two(a: Any, b: Any) -> None:
    """Chạy hai hàm ở hai thread, xuất phát cùng một barrier."""
    gate = threading.Barrier(2)

    def go(fn: Any) -> None:
        gate.wait()
        fn()

    ths = [threading.Thread(target=go, args=(f,)) for f in (a, b)]
    for t in ths:
        t.start()
    for t in ths:
        t.join(60)


def _code(a: Any) -> str:
    if isinstance(a, dict):
        return "ok" if a.get("ok") else str(a["error"]["code"])
    return repr(a)


def _dispatcher_round(w: W, i: int, jit: tuple[float, float]) -> tuple[str, bool]:
    d = w.prepared.dispatcher
    rt = w.prepared.services.runtime
    svc = ConfirmationService(d, DESK_CLIENT)
    key = "NV-0009"
    agent = Actor(ALIAS, w.sub(key), PrincipalKind.AGENT, AGENT_CLIENT, w.sub(key))
    args = {"recipients": [f"person:{w.pid('NV-0010')}"], "message": f"race {i}",
            "idempotency_key": f"r3-race-{i:04d}-{uuid.uuid4().hex[:6]}"}  # fmt: skip
    first = d.call("notify", args, w.ctx(key, datetime.now(UTC))).envelope
    assert first["error"]["code"] == "confirmation_required", first
    det = first["error"]["details"]
    cid = det["confirmation_id"]
    exp = datetime.fromisoformat(det["expires_at"].replace("Z", "+00:00"))
    out: dict[str, Any] = {}

    def confirmer() -> None:
        trace = f"r3c-{i}"
        time.sleep(jit[0])
        try:
            out["c"] = rt.run_as(
                rt.resolve(agent, trace),
                agent,
                lambda: svc.confirm(cid, w.sub(key), exp - timedelta(milliseconds=1), trace),
            )
        except Exception as e:
            out["c"] = e

    def retry() -> None:
        time.sleep(jit[1])
        try:
            out["a"] = d.call("notify", args, w.ctx(key, exp + timedelta(seconds=1))).envelope
        except Exception as e:
            out["a"] = e

    _two(confirmer, retry)
    with w.pg.su() as su:
        row = su.execute(
            "SELECT pending_status, receipt IS NOT NULL FROM xt_meta.tool_operation"
            " WHERE confirmation_id=%s",
            (cid,),
        ).fetchone()
    assert row is not None
    status, has_receipt = row
    c, a = out.get("c"), out.get("a")
    c_status = getattr(c, "status", c)
    a_code = _code(a)
    tag = (
        f"{status}|receipt={has_receipt}|confirm={getattr(c_status, 'value', c_status)}"
        f"|agent={a_code}"
    )
    a_status = None
    if isinstance(a, dict) and a_code != "ok":
        a_status = a["error"]["details"].get("status")
    ok = (
        has_receipt == (status == "confirmed")
        and (c_status is PendingStatus.CONFIRMED) == has_receipt
        and not (has_receipt and a_code != "ok")
        and not (not has_receipt and a_code == "ok")
        and (a_code != "confirmation_declined" or a_status == "expired")
        and a_code in ("ok", "confirmation_declined")
    )
    return tag, ok


def test_r2m3_confirm_vs_expire_race_dispatcher_pg(w: W) -> None:
    """100 vòng: người xác nhận bấm ngay trước hạn (now = expires-1ms) và agent gọi lại ngay sau
    hạn (now = expires+1s), hai thread/hai connection, xuất phát cùng barrier + jitter ngẫu nhiên.
    Bất biến: có receipt <=> pending_status=confirmed <=> confirm CONFIRMED; agent không bao giờ
    nhận lỗi khi mutation đã commit, không bao giờ nhận ok khi chưa commit."""
    tally: dict[str, int] = {}
    bad: list[Any] = []
    rnd = random.Random(3)
    for i in range(ROUNDS):
        tag, ok = _dispatcher_round(w, i, (rnd.random() * 0.004, rnd.random() * 0.004))
        tally[tag] = tally.get(tag, 0) + 1
        if not ok:
            bad.append((i, tag))
    w.notes["race"] = tally
    print("R3 race:", tally)
    assert not bad, bad[:5]
    # Hai nhánh của cuộc đua đều phải xảy ra, nếu không bài kiểm không có ý nghĩa.
    branches = {"receipt=True" in k for k in tally}
    assert branches == {True, False}, tally


def _store_round(w: W, store: Any, tx: Any, scope: Any) -> tuple[str, bool]:
    from tests.unit.tools.operation_store_contract import NOW, _key, _pending, _receipt
    from xt.application.tools.ports import PendingOperation, Receipt

    k = _key(f"r3-st-{uuid.uuid4().hex[:12]}")
    op = _pending(k, f"cf_r3_{uuid.uuid4().hex}")

    def create() -> None:
        assert store.claim(k, "fp", NOW) is None
        store.save_pending(op)

    tx(create)
    out: dict[str, Any] = {}

    def confirm_body() -> str:
        cur = store.lock_pending(op.confirmation_id)
        assert cur is not None
        if not cur.status.open:
            return str(cur.status.value)
        store.save_receipt(_receipt(k))
        done = store.update_pending(replace(cur, status=PendingStatus.CONFIRMED))
        return "confirmed" if done else "update_false"

    def agent_body() -> Any:
        seen = store.claim(k, "fp", NOW + timedelta(minutes=11))
        if isinstance(seen, PendingOperation):
            if seen.status.open:
                ok = store.update_pending(replace(seen, status=PendingStatus.EXPIRED))
                return "expired" if ok else "update_false"
            return seen.status.value
        return "receipt" if isinstance(seen, Receipt) else repr(seen)

    def confirmer() -> None:
        out["c"] = tx(confirm_body)

    def agent() -> None:
        out["a"] = tx(agent_body)

    _two(confirmer, agent)
    with scope():
        final = store.get_pending(op.confirmation_id)
    with w.pg.su() as su:
        rr = su.execute(
            "SELECT receipt IS NOT NULL FROM xt_meta.tool_operation WHERE confirmation_id=%s",
            (op.confirmation_id,),
        ).fetchone()
    assert final is not None and rr is not None
    tag = f"{final.status.value}|receipt={rr[0]}|c={out.get('c')}|a={out.get('a')}"
    ok = (
        rr[0] == (final.status is PendingStatus.CONFIRMED)
        and not (out.get("a") == "expired" and rr[0])
        and "update_false" not in tag
    )
    return tag, ok


def test_r2m3_store_level_race_two_connections(w: W) -> None:
    """Cùng cuộc đua ở mức port (không qua dispatcher): `lock_pending`+receipt+CONFIRMED vs
    `claim`(sau hạn)+EXPIRED, 100 vòng, pool 2 connection, không Event đồng bộ (chỉ barrier)."""
    from xt.adapters.db.confirmations import PgOperationStore, PgUnitOfWork
    from xt.adapters.db.connection import Database
    from xt.domain.model.context import ModelActor

    tenant = w.tenant
    actor = ModelActor(tenant, uuid.UUID(int=0), ())
    db = Database(w.pg.app_dsn, min_size=2, max_size=2)
    uow = PgUnitOfWork(db)
    store = PgOperationStore(uow, lambda alias: tenant.id, lock_timeout_ms=5000)

    def tx(fn: Any) -> Any:
        with uow.scope(actor):
            return uow.run(fn)

    tally: dict[str, int] = {}
    bad: list[Any] = []
    try:
        for i in range(ROUNDS):
            tag, ok = _store_round(w, store, tx, lambda: uow.scope(actor))
            tally[tag] = tally.get(tag, 0) + 1
            if not ok:
                bad.append((i, tag))
    finally:
        db.close()
    w.notes["store_race"] = tally
    print("R3 store race:", tally)
    assert not bad, bad[:5]


# ================================================ 4. R2-M4 + DPoP: tier của Desk trên /mcp ====
class DpopAuth(httpx2.Auth):
    """Mỗi request một proof mới (htu = resource công khai, htm theo request)."""

    def __init__(self, key: DpopKey, token: str, htu: str) -> None:
        self.key, self.tok, self.htu = key, token, htu
        self.sent: list[str] = []

    def auth_flow(self, request: Any) -> Any:
        proof = self.key.proof(request.method, self.htu, token=self.tok)
        self.sent.append(proof)
        request.headers["Authorization"] = f"DPoP {self.tok}"
        request.headers["DPoP"] = proof
        yield request


def _bound(w: W, key: str, dk: DpopKey, client: str = DESK_AGENT) -> str:
    return w.token(key, client, typ="DPoP", cnf={"jkt": dk.jkt})


@pytest.mark.anyio
async def test_r2m4_desk_agent_tier_masked_confidential_full(w: W) -> None:
    from tests.integration.composition.test_e2e import advance

    acct = "9704225566778899"
    c = await w.call("NV-0010", "create_record", {
        "model_id": "advance_request",
        "values": advance(1_500_000, payout_method="bank_transfer", bank_account=acct),
        "idempotency_key": "r3-tier-0001"}, client=DESK_AGENT)  # fmt: skip
    assert c["ok"], c
    assert acct not in json.dumps(c) and c["meta"]["pii"]["mode"] == "masked", c
    ref = c["data"]["record"]["ref"]
    w.notes["desk_ref"] = ref
    local = await w.call("NV-0010", "get_record", {"ref": ref}, client=LOCAL_AGENT)
    assert local["ok"] and local["data"]["record"]["values"]["bank_account"] == acct, local
    # DPoP không nâng tier: token gắn khóa của xt-desk-agent vẫn masked.
    dk = DpopKey()
    tok = _bound(w, "NV-0010", dk)
    auth = DpopAuth(dk, tok, w.idp.resource)
    async with w.session("", auth=auth) as (s, _):
        got = envelope(await s.call_tool("get_record", {"ref": ref}))
    assert got["ok"] and acct not in json.dumps(got) and got["meta"]["pii"]["mode"] == "masked"
    # Cấu hình: client public không được vào tier local (trừ cờ dev).
    bad_env = {**w.env, "XT_MCP_LOCAL_CLIENTS": DESK_AGENT, "XT_MCP_CONFIDENTIAL_CLIENTS": ""}
    with pytest.raises(ServeConfigError):
        load_serve_config(bad_env, "http", "full", "127.0.0.1", 1)
    sneaky = {**w.env, "XT_MCP_LOCAL_CLIENTS": f"{LOCAL_AGENT},{DESK_AGENT}"}
    with pytest.raises(ServeConfigError):
        load_serve_config(sneaky, "http", "full", "127.0.0.1", 1)


def _list(w: W, headers: dict[str, str], app: RunningApp | None = None) -> httpx.Response:
    a = app or w.app
    return httpx.post(
        a.mcp_url,
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={**JSONRPC, **headers},
        timeout=30,
    )


def test_dpop_bound_tokens_on_mcp(w: W) -> None:
    dk, other = DpopKey(), DpopKey()
    tok = _bound(w, "NV-0010", dk)
    htu = w.idp.resource
    ok = _list(w, {"Authorization": f"DPoP {tok}", "DPoP": dk.proof("POST", htu, token=tok)})
    assert ok.status_code == 200, ok.text
    replay_proof = dk.proof("POST", htu, token=tok)
    first = _list(w, {"Authorization": f"DPoP {tok}", "DPoP": replay_proof})
    assert first.status_code == 200
    attacks = {
        "bound_as_bearer": {"Authorization": f"Bearer {tok}"},
        "bound_as_bearer_with_proof": {"Authorization": f"Bearer {tok}",
                                       "DPoP": dk.proof("POST", htu, token=tok)},
        "replayed_proof": {"Authorization": f"DPoP {tok}", "DPoP": replay_proof},
        "wrong_htm": {"Authorization": f"DPoP {tok}", "DPoP": dk.proof("GET", htu, token=tok)},
        "wrong_htu": {"Authorization": f"DPoP {tok}",
                      "DPoP": dk.proof("POST", htu.replace("/mcp", "/workflow/v1"), token=tok)},
        "wrong_ath": {"Authorization": f"DPoP {tok}", "DPoP": dk.proof("POST", htu, token="x")},
        "other_key": {"Authorization": f"DPoP {tok}", "DPoP": other.proof("POST", htu, token=tok)},
        "no_proof": {"Authorization": f"DPoP {tok}"},
        "two_proofs": {"Authorization": f"DPoP {tok}",
                       "DPoP": dk.proof("POST", htu, token=tok) + ","
                       + dk.proof("POST", htu, token=tok)},
        "unbound_dpop_scheme": {"Authorization": f"DPoP {w.token('NV-0010', DESK_AGENT)}",
                                "DPoP": dk.proof("POST", htu,
                                                 token=w.token("NV-0010", DESK_AGENT))},
        "stale_iat": {"Authorization": f"DPoP {tok}",
                      "DPoP": dk.proof("POST", htu, token=tok, iat=int(time.time()) - 600)},
    }  # fmt: skip
    got = {k: _list(w, h).status_code for k, h in attacks.items()}
    w.notes["dpop"] = got
    print("R3 dpop:", got)
    assert all(v == 401 for v in got.values()), got
    # XT_DPOP_REQUIRED: xt-desk-agent không gắn khóa bị từ chối; client khác không bị ép.
    req = {
        "desk_unbound": _list(w, {"Authorization": f"Bearer {w.token('NV-0010', DESK_AGENT)}"},
                              w.strict).status_code,
        "agent_unbound": _list(w, {"Authorization": f"Bearer {w.token('NV-0010')}"},
                               w.strict).status_code,
        "desk_bound": _list(w, {"Authorization": f"DPoP {tok}",
                                "DPoP": dk.proof("POST", htu, token=tok)}, w.strict).status_code,
    }  # fmt: skip
    w.notes["dpop_required"] = req
    assert req == {"desk_unbound": 401, "agent_unbound": 200, "desk_bound": 200}, req


# =========================================================== 5. aud ngoại (WA-08/FB-08) ====
def test_foreign_audiences_rejected_on_mcp(w: W) -> None:
    res = w.idp.resource
    variants: dict[str, Any] = {
        "workflow+mcp": [res, "xt-workflow"],
        "data+mcp": [res, "xt-data"],
        "files+mcp": [res, "xt-files"],
        "workflow_only": "xt-workflow",
        "files_only": ["xt-files"],
        "mcp_first_then_data": [res, "account", "xt-data"],
    }
    got = {}
    for name, aud in variants.items():
        tok = w.token("NV-0010", aud=aud)
        got[name] = _list(w, {"Authorization": f"Bearer {tok}"}).status_code
    dk = DpopKey()
    btok = w.token("NV-0010", DESK_AGENT, typ="DPoP", cnf={"jkt": dk.jkt}, aud=[res, "xt-files"])
    got["dpop_bound+files"] = _list(
        w, {"Authorization": f"DPoP {btok}", "DPoP": dk.proof("POST", res, token=btok)}
    ).status_code
    control = _list(w, {"Authorization": f"Bearer {w.token('NV-0010', aud=[res, 'account'])}"})
    w.notes["aud"] = got
    print("R3 aud:", got)
    assert control.status_code == 200
    assert all(v == 401 for v in got.values()), got


# ====================================================== 6. catalog / hồ sơ sau tool mới ====
@pytest.mark.anyio
async def test_catalog_full_and_core10_integrity(w: W) -> None:
    assert CORE_10 == CORE_10_V01
    async with w.session(w.token("NV-0010")) as (s, init):
        full = [t.name for t in (await s.list_tools()).tools]
    catalog = [c.name for c in CATALOG]
    assert full[: len(catalog)] == catalog, full
    assert set(full[len(catalog) :]) <= set(APP_TOOLS_BY_NAME), full
    assert {"start_process", "notify"} <= set(full)
    async with w.session(w.token("NV-0010"), w.core) as (s, init):
        core = [t.name for t in (await s.list_tools()).tools]
        prompts = await s.list_prompts()
        instructions = init.instructions or ""
        hidden = [n for n in catalog if n not in CORE_10] + [
            n for n in APP_TOOLS_BY_NAME if n not in core
        ]
        replies = {}
        for name in hidden:
            r = envelope(await s.call_tool(name, {"idempotency_key": "r3-hidden-0001"}))
            replies[name] = (r["error"]["code"], r["error"]["details"].get("reason"),
                             r["error"]["details"].get("tools"))  # fmt: skip
        unknown = envelope(await s.call_tool("khong_co_tool", {}))
    w.notes["core10"] = {"listed": core, "hidden_replies": {k: v[:2] for k, v in replies.items()}}
    apps_shown = [n for n in core if n in APP_TOOLS_BY_NAME]
    assert core == [n for n in catalog if n in CORE_10] + apps_shown, (core, full)
    assert all(v[:2] == ("invalid_request", "wrong_tool") for v in replies.values()), replies
    assert all(v[2] == sorted(CORE_10) for v in replies.values()), replies
    assert unknown["error"]["details"]["tools"] == sorted(CORE_10)
    w.notes["core10"]["apps_shown"] = apps_shown
    for n in hidden:
        assert f"{n}(" not in instructions and f"`{n}`" not in instructions, n
    assert prompts is not None


# ================================================ 7. echo không giới hạn ngoài tools/call ====
@pytest.mark.parametrize("method", ["prompts/get", "resources/read"])
def test_unbounded_echo_of_unknown_names(w: W, method: str) -> None:
    """R2-I1 đã cắt tên tool trong `wrong_tool` còn 64 ký tự; tên prompt/URI resource lạ không
    được echo nguyên văn (tới ~4 MiB) trong lỗi JSON-RPC."""
    name = "p" + "x" * (3 * 1024 * 1024)
    params = {"name": name, "arguments": {}} if method == "prompts/get" else {"uri": f"ui://{name}"}
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    r = w.post(body, w.token("NV-0010"))
    w.notes[f"echo_{method}"] = {"status": r.status_code, "bytes": len(r.content)}
    print("R3 echo", method, w.notes[f"echo_{method}"])
    assert len(r.content) < 64 * 1024, (r.status_code, len(r.content))
