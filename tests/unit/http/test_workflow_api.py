# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""REST `/workflow/v1` trên `ToolDispatcher` thật với port giả (`tests/unit/tools/fakes.py`).

Token ký thật bằng khóa test, kiểm bằng `AccessTokenVerifier` thật qua JWKS tĩnh; audience
`xt-workflow` tách khỏi audience MCP.
"""

from __future__ import annotations

import json
import uuid
import xml.etree.ElementTree as ET
from dataclasses import replace
from typing import Any

import httpx2
import pytest
from jsonschema import Draft202012Validator
from starlette.applications import Starlette

from tests.unit.identity.factory import (
    RESOURCE,
    FakeClock,
    FakeJwks,
    access_claims,
    new_rsa,
    rs_settings,
    sign,
)
from tests.unit.tools.fakes import NOW, FakeWorkspace, later, make_dispatcher, make_world
from xt.adapters.http.workflow_api import (
    API_PREFIX,
    OPERATIONS,
    InstanceTask,
    InstanceView,
    openapi_document,
    workflow_routes,
)
from xt.adapters.identity.access_token import AccessTokenVerifier
from xt.adapters.identity.client_registry import StaticClientAllowlist
from xt.adapters.identity.jwks import JwksCache
from xt.application.tools import Actor, CallContext, ConfirmationService
from xt.application.tools.ports import Args, Data, PendingStatus
from xt.domain.autonomy import RiskLevel
from xt.domain.identity import InvalidTokenError, Principal
from xt.entrypoints.workflow_http import (
    IdentityWorkflowAuth,
    WorkflowHttpConfigError,
    workflow_resource_settings,
)

BASE = "https://xt-api.example.test"
KEY = new_rsa("wf-unit")
INSTANCE = str(uuid.uuid4())
BPMN = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" '
    'xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI" id="d" targetNamespace="x">'
    '<bpmn:process id="advance_approval" isExecutable="true"/>'
    '<bpmndi:BPMNDiagram id="dia"><bpmndi:BPMNPlane id="p" bpmnElement="advance_approval"/>'
    "</bpmndi:BPMNDiagram></bpmn:definitions>"
)
NS = {
    "bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL",
    "bpmndi": "http://www.omg.org/spec/BPMN/20100524/DI",
}


class ProcessWorkspace(FakeWorkspace):
    def inspect(self, ctx: CallContext, args: Args) -> Data:
        out = super().inspect(ctx, args)
        out["processes"] = [{"id": "advance_approval", "label": "Duyệt tạm ứng"}]
        return out


class FakeBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []

    def bpmn(self, actor: Actor, trace_id: str, process_id: str, revision: int) -> str | None:
        self.calls.append(("bpmn", actor.tenant, (process_id, revision)))
        return BPMN if actor.tenant == "acme" and process_id == "advance_approval" else None

    def instance(self, actor: Actor, trace_id: str, instance_id: str) -> InstanceView | None:
        self.calls.append(("instance", actor.tenant, instance_id))
        if actor.tenant != "acme" or instance_id != INSTANCE:
            return None
        task = InstanceTask(
            "t-1", "head", "Trưởng bộ phận duyệt", "decision", ("approve",), ("person:mai",)
        )
        return InstanceView(INSTANCE, "advance_request:tu-0042", "advance_approval", 2, "running",
                            (task,))  # fmt: skip


class Env:
    def __init__(self) -> None:
        self.world = make_world()
        self.world.services = replace(self.world.services, workspace=ProcessWorkspace())
        self.dispatcher = make_dispatcher(self.world)
        self.backend = FakeBackend()
        self.revoked: set[str] = set()
        ws = workflow_resource_settings(
            rs_settings(human_clients=frozenset({"xt-desk"})), {"XT_WORKFLOW_AUDIENCE": ""}
        )
        verifier = AccessTokenVerifier(
            ws,
            JwksCache(FakeJwks([KEY]), 300, 10, FakeClock()),
            FakeClock(),
            clients=StaticClientAllowlist(ws.allowed_clients, ws.human_clients),
        )

        def ensure_active(p: Principal, token: str, level: RiskLevel) -> None:
            if token in self.revoked:
                raise InvalidTokenError("token_revoked")

        mount = workflow_routes(
            self.dispatcher,
            IdentityWorkflowAuth(ws, verifier),
            self.backend,
            ensure_active=ensure_active,
            clock=lambda: NOW,
            max_body_bytes=4096,
            server_url=BASE,
        )
        self.app = Starlette(routes=[mount])

    def client(self, token: str | None = None) -> httpx2.AsyncClient:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=self.app), base_url=BASE, headers=headers
        )


def token(sub: str = "sub-viet", **over: Any) -> str:
    claims: dict[str, Any] = {
        "sub": sub,
        "aud": ["xt-workflow"],
        "scope": "openid xt-workflow",
        "jti": uuid.uuid4().hex,
    }
    claims.update(over)
    return sign(KEY, access_claims(**claims))


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def env() -> Env:
    return Env()


pytestmark = pytest.mark.anyio


def _err(r: Any) -> dict[str, Any]:
    body: dict[str, Any] = r.json()
    assert body["ok"] is False and body["untrusted"] is True, body
    assert body["meta"]["trace_id"].startswith("tr_")
    err: dict[str, Any] = body["error"]
    return err


# ---- OpenAPI ----------------------------------------------------------------------------------


def _schemas(node: Any) -> list[Any]:
    found: list[Any] = []
    if isinstance(node, dict):
        if "schema" in node and isinstance(node["schema"], dict):
            found.append(node["schema"])
        for v in node.values():
            found += _schemas(v)
    elif isinstance(node, list):
        for v in node:
            found += _schemas(v)
    return found


async def test_openapi_is_generated_public_and_valid(env: Env) -> None:
    async with env.client() as c:
        r = await c.get(f"{API_PREFIX}/openapi.json")
    assert r.status_code == 200
    doc = r.json()
    assert doc == json.loads(json.dumps(openapi_document(BASE)))
    assert doc["openapi"] == "3.1.0" and doc["servers"] == [{"url": BASE}]
    ops = {(m, p) for p, item in doc["paths"].items() for m in item}
    assert ops == {(o.method.lower(), API_PREFIX + o.path) for o in OPERATIONS}
    assert doc["paths"][f"{API_PREFIX}/openapi.json"]["get"]["security"] == []
    post = doc["paths"][f"{API_PREFIX}/instances"]["post"]
    assert {p["name"] for p in post["parameters"]} == {"Idempotency-Key"}
    assert post["x-xt-tools"] == ["describe_process", "create_record", "submit_request"]
    for schema in [*_schemas(doc["paths"]), *doc["components"]["schemas"].values()]:
        Draft202012Validator.check_schema(schema)
    decide = doc["paths"][f"{API_PREFIX}/tasks/{{task_id}}/decision"]["post"]
    body = decide["requestBody"]["content"]["application/json"]["schema"]
    assert body["properties"]["decision"]["enum"] == [
        "approve", "reject", "return_for_revision", "delegate", "request_info",
    ]  # fmt: skip
    assert "428" in decide["responses"]


# ---- xác thực ---------------------------------------------------------------------------------


async def test_missing_token_is_401_with_bearer_challenge(env: Env) -> None:
    async with env.client() as c:
        r = await c.get(f"{API_PREFIX}/definitions")
    assert r.status_code == 401
    assert r.headers["www-authenticate"].startswith('Bearer realm="xt-workflow"')
    assert 'scope="xt-workflow"' in r.headers["www-authenticate"]
    assert _err(r)["code"] == "unauthenticated"


async def test_mcp_audience_token_is_rejected(env: Env) -> None:
    mcp = sign(
        KEY, access_claims(sub="sub-viet", aud=[RESOURCE], scope="openid mcp-xt xt-workflow")
    )
    async with env.client(mcp) as c:
        r = await c.get(f"{API_PREFIX}/definitions")
    assert r.status_code == 401
    assert 'error="invalid_token"' in r.headers["www-authenticate"]
    assert "workflow API" in r.headers["www-authenticate"]


async def test_missing_scope_is_403(env: Env) -> None:
    async with env.client(token(scope="openid")) as c:
        r = await c.get(f"{API_PREFIX}/inbox")
    assert r.status_code == 403
    assert 'error="insufficient_scope"' in r.headers["www-authenticate"]
    assert _err(r)["details"]["reason"] == "insufficient_scope"


async def test_human_session_and_foreign_client_are_rejected(env: Env) -> None:
    human = token("sub-mai", azp="xt-desk")
    stranger = token("sub-mai", azp="evil-client")
    async with env.client(human) as c:
        r1 = await c.get(f"{API_PREFIX}/inbox")
    async with env.client(stranger) as c:
        r2 = await c.get(f"{API_PREFIX}/inbox")
    assert r1.status_code == r2.status_code == 401
    assert "people confirm on the xT confirmation page" in _err(r1)["message"]


async def test_no_or_many_organizations(env: Env) -> None:
    async with env.client(token(organization=None)) as c:
        r1 = await c.get(f"{API_PREFIX}/inbox")
    async with env.client(token(organization=["a", "b"])) as c:
        r2 = await c.get(f"{API_PREFIX}/inbox")
    assert r1.status_code == 401 and _err(r1)["code"] == "unauthenticated"
    assert r2.status_code == 403 and _err(r2)["details"]["reason"] == "tenant_ambiguous"


def test_audience_must_differ_from_mcp_resource() -> None:
    with pytest.raises(WorkflowHttpConfigError):
        workflow_resource_settings(rs_settings(), {"XT_WORKFLOW_AUDIENCE": RESOURCE})
    ws = workflow_resource_settings(rs_settings(), {"XT_WORKFLOW_ALLOWED_CLIENTS": "xt-cli"})
    assert ws.resource == "xt-workflow" and ws.allowed_clients == {"xt-cli"}
    assert ws.required_scopes == {"xt-workflow"} and not ws.dynamic_connectors


# ---- định nghĩa ---------------------------------------------------------------------------------


async def test_definitions_and_bpmn(env: Env) -> None:
    async with env.client(token()) as c:
        lst = await c.get(f"{API_PREFIX}/definitions")
        one = await c.get(f"{API_PREFIX}/definitions/advance_approval")
        x = await c.get(f"{API_PREFIX}/definitions/advance_approval/bpmn")
        cached = await c.get(
            f"{API_PREFIX}/definitions/advance_approval/bpmn",
            headers={"If-None-Match": x.headers["etag"]},
        )
        missing = await c.get(f"{API_PREFIX}/definitions/khong_co/bpmn")
        bad = await c.get(f"{API_PREFIX}/definitions/..%2Fetc/bpmn")
    assert lst.status_code == 200, lst.text
    d = lst.json()["data"]["definitions"]
    assert d == [
        {
            "id": "advance_approval",
            "label": "Duyệt tạm ứng",
            "links": {
                "self": f"{API_PREFIX}/definitions/advance_approval",
                "bpmn": f"{API_PREFIX}/definitions/advance_approval/bpmn",
            },
        }
    ]
    assert one.status_code == 200 and one.json()["data"]["process"]["revision"] == 2
    assert x.status_code == 200 and x.headers["content-type"].startswith("application/xml")
    assert x.headers["content-disposition"] == 'attachment; filename="advance_approval-v2.bpmn"'
    root = ET.fromstring(x.content)
    assert root.find("bpmn:process", NS) is not None
    assert root.find("bpmndi:BPMNDiagram", NS) is not None
    assert ("bpmn", "acme", ("advance_approval", 2)) in env.backend.calls
    assert cached.status_code == 304
    assert missing.status_code == 404 and _err(missing)["code"] == "not_found"
    assert bad.status_code == 404


# ---- instance -----------------------------------------------------------------------------------

VALUES = {"amount": "2000000", "purpose": "Mua văn phòng phẩm"}


async def test_start_instance_requires_idempotency_key_and_replays(env: Env) -> None:
    body = {"definition_id": "advance_approval", "values": VALUES, "note": "Gấp"}
    async with env.client(token()) as c:
        nokey = await c.post(f"{API_PREFIX}/instances", json=body)
        badkey = await c.post(
            f"{API_PREFIX}/instances", json=body, headers={"Idempotency-Key": "x"}
        )
        h = {"Idempotency-Key": "wf-unit-start-0001"}
        first = await c.post(f"{API_PREFIX}/instances", json=body, headers=h)
        again = await c.post(f"{API_PREFIX}/instances", json=body, headers=h)
        other = await c.post(f"{API_PREFIX}/instances", json={**body, "note": "khác"}, headers=h)
    assert nokey.status_code == 400
    assert _err(nokey)["details"]["reason"] == "idempotency_key_required"
    assert badkey.status_code == 400
    assert first.status_code == 201, first.text
    data = first.json()["data"]
    assert data["instance"] == {"id": "pi1", "href": f"{API_PREFIX}/instances/pi1"}
    assert first.headers["location"] == f"{API_PREFIX}/instances/pi1"
    assert data["request"]["state"] == "in_review"
    assert again.status_code == 200 and again.json()["data"] == data
    assert again.json()["meta"]["receipt"]["replayed"] is True
    assert other.status_code == 409 and _err(other)["code"] == "idempotency_conflict"
    assert env.world.store.commits.count("submit") == 1


async def test_start_instance_body_validation_does_not_echo_values(env: Env) -> None:
    h = {"Idempotency-Key": "wf-unit-start-0002"}
    secret = "079123456789"
    async with env.client(token()) as c:
        unknown = await c.post(
            f"{API_PREFIX}/instances",
            json={"model_id": "advance_request", "values": VALUES, "cccd": secret},
            headers=h,
        )
        both = await c.post(
            f"{API_PREFIX}/instances",
            json={"ref": "advance_request:tu-0050", "values": VALUES},
            headers=h,
        )
        dup = await c.post(
            f"{API_PREFIX}/instances",
            content=b'{"ref":"advance_request:tu-0050","ref":"advance_request:tu-0042"}',
            headers={**h, "Content-Type": "application/json"},
        )
        text = await c.post(f"{API_PREFIX}/instances", content=b"ref=1", headers=h)
        big = await c.post(
            f"{API_PREFIX}/instances",
            json={"model_id": "advance_request", "values": {"purpose": "x" * 5000}},
            headers=h,
        )
    assert unknown.status_code == 400 and secret not in unknown.text
    assert _err(unknown)["details"]["reason"] == "schema"
    assert both.status_code == 400
    assert dup.status_code == 400 and _err(dup)["details"]["reason"] == "duplicate_key"
    assert text.status_code == 400 and _err(text)["details"]["reason"] == "content_type"
    assert big.status_code == 413 and _err(big)["code"] == "resource_constraint"


async def test_rule_violation_maps_to_422(env: Env) -> None:
    async with env.client(token()) as c:
        r = await c.post(
            f"{API_PREFIX}/instances",
            json={"model_id": "advance_request", "values": {**VALUES, "amount": "90000000"}},
            headers={"Idempotency-Key": "wf-unit-start-0003"},
        )
    assert r.status_code == 422 and _err(r)["code"] == "rule_violation"


async def test_submit_existing_draft_by_ref(env: Env) -> None:
    async with env.client(token()) as c:
        r = await c.post(
            f"{API_PREFIX}/instances",
            json={"ref": "advance_request:tu-0050"},
            headers={"Idempotency-Key": "wf-unit-start-0004"},
        )
    assert r.status_code == 201, r.text
    assert r.json()["data"]["request"]["ref"] == "advance_request:tu-0050"
    assert "create" not in env.world.store.commits


async def test_get_instance_is_tenant_scoped(env: Env) -> None:
    async with env.client(token()) as c:
        ok = await c.get(f"{API_PREFIX}/instances/{INSTANCE}")
        ghost = await c.get(f"{API_PREFIX}/instances/{uuid.uuid4()}")
        junk = await c.get(f"{API_PREFIX}/instances/not-a-uuid")
    async with env.client(token(organization=["beta"])) as c:
        foreign = await c.get(f"{API_PREFIX}/instances/{INSTANCE}")
    assert ok.status_code == 200, ok.text
    inst = ok.json()["data"]["instance"]
    assert inst["status"] == "running" and inst["tasks"][0]["assignees"] == ["person:mai"]
    assert inst["links"]["decision"] == f"{API_PREFIX}/tasks/advance_request:tu-0042/decision"
    assert ok.json()["data"]["request"]["ref"] == "advance_request:tu-0042"
    for r in (ghost, junk, foreign):
        assert r.status_code == 404
        assert _err(r) == _err(ghost)
    assert ("instance", "beta", INSTANCE) in env.backend.calls


# ---- inbox + quyết định -----------------------------------------------------------------------

DECIDE = {"decision": "approve", "expected_revision": 2}
TASK = f"{API_PREFIX}/tasks/advance_request:tu-0042/decision"


async def test_inbox_links_to_decision(env: Env) -> None:
    async with env.client(token("sub-mai")) as c:
        r = await c.get(f"{API_PREFIX}/inbox", params={"kinds": "approval", "limit": "5"})
        bad = await c.get(f"{API_PREFIX}/inbox", params={"x": "1"})
    assert r.status_code == 200, r.text
    item = r.json()["data"]["items"][0]
    assert item["task_id"] == "advance_request:tu-0042"
    assert item["links"]["decision"] == TASK
    assert bad.status_code == 400


async def test_agent_decision_needs_human_confirmation(env: Env) -> None:
    h = {"Idempotency-Key": "wf-unit-dec-0001"}
    async with env.client(token("sub-mai")) as c:
        nokey = await c.post(TASK, json=DECIDE)
        info = await c.post(TASK, json={**DECIDE, "decision": "request_info"}, headers=h)
        first = await c.post(TASK, json=DECIDE, headers=h)
        e = _err(first)
        assert first.status_code == 428 and e["code"] == "confirmation_required", first.text
        url = e["details"]["confirm_url"]
        assert url == f"https://xt.example.test/desk/confirm/{e['details']['confirmation_id']}"
        assert first.headers["link"] == f'<{url}>; rel="confirm"'
        assert e["continuation"]["tool"] == "decide_request"
        assert env.world.store.commits == []
        # Người bấm trên trang Desk (backend Desk gọi ConfirmationService sau step-up).
        done = ConfirmationService(env.dispatcher).confirm(
            e["details"]["confirmation_id"], "sub-mai", later(2), "tr_desk"
        )
        assert done.status is PendingStatus.CONFIRMED
        final = await c.post(TASK, json=DECIDE, headers=h)
    assert nokey.status_code == 400
    assert info.status_code == 400 and _err(info)["details"]["reason"] == "decision_not_supported"
    assert final.status_code == 200, final.text
    assert final.json()["data"]["outcome"] == "request_approved"
    assert final.json()["meta"]["receipt"]["replayed"] is True


async def test_requester_cannot_decide_own_request(env: Env) -> None:
    async with env.client(token("sub-viet")) as c:
        r = await c.post(
            TASK,
            json={**DECIDE, "decision": "return_for_revision", "reason": "Thiếu chứng từ"},
            headers={"Idempotency-Key": "wf-unit-dec-0002"},
        )
    assert r.status_code == 403 and _err(r)["details"]["reason"] == "sod"
    assert "confirm_url" not in r.text


async def test_revoked_token_blocks_high_risk_write(env: Env) -> None:
    env.world.access.ceiling = RiskLevel.L2
    t = token("sub-mai")  # gửi duyệt nháp của người khác: L2 (rủi ro cao) -> introspection
    env.revoked.add(t)
    async with env.client(t) as c:
        r = await c.post(
            f"{API_PREFIX}/instances",
            json={"ref": "advance_request:tu-0050"},
            headers={"Idempotency-Key": "wf-unit-start-0005"},
        )
    assert r.status_code == 401 and _err(r)["code"] == "unauthenticated"
    assert env.world.store.commits == []


# ---- sau audit WA-04..WA-10 ----------------------------------------------------------------


async def test_unknown_route_and_method_use_error_envelope(env: Env) -> None:
    async with env.client(token()) as c:
        nope = await c.get(f"{API_PREFIX}/nope")
        deep = await c.get(f"{API_PREFIX}/definitions/a/b/c")
        method = await c.delete(f"{API_PREFIX}/inbox")
    for r in (nope, deep):
        assert r.status_code == 404 and _err(r)["code"] == "not_found"
    assert method.status_code == 405 and method.headers["allow"] == "GET"
    assert _err(method)["details"]["reason"] == "method_not_allowed"


async def test_if_none_match_weak_list_and_star(env: Env) -> None:
    url = f"{API_PREFIX}/definitions/advance_approval/bpmn"
    async with env.client(token()) as c:
        et = (await c.get(url)).headers["etag"]
        codes = [
            (await c.get(url, headers={"If-None-Match": v})).status_code
            for v in (f"W/{et}", f'"x", {et}', "*", '"other"')
        ]
        r304 = await c.get(url, headers={"If-None-Match": et})
    assert codes == [304, 304, 304, 200]
    assert r304.headers["cache-control"] == "private, no-cache" and r304.headers["etag"] == et


async def test_instance_tasks_use_decision_key_and_hide_unreadable(env: Env) -> None:
    env.world.access.deny_tools["get_record"] = "behalf_permission"
    async with env.client(token()) as c:
        denied = await c.get(f"{API_PREFIX}/instances/{INSTANCE}")
        ghost = await c.get(f"{API_PREFIX}/instances/{uuid.uuid4()}")
    assert denied.status_code == ghost.status_code == 404
    assert _err(denied) == _err(ghost)
    del env.world.access.deny_tools["get_record"]
    async with env.client(token()) as c:
        ok = await c.get(f"{API_PREFIX}/instances/{INSTANCE}")
    inst = ok.json()["data"]["instance"]
    assert [t["task_id"] for t in inst["tasks"]] == ["advance_request:tu-0042"]
    assert inst["links"]["withdraw"] == f"{API_PREFIX}/instances/{INSTANCE}/withdraw"


async def test_withdraw_instance_maps_to_transition_record(env: Env) -> None:
    url = f"{API_PREFIX}/instances/{INSTANCE}/withdraw"
    h = {"Idempotency-Key": "wf-unit-wd-0001"}
    async with env.client(token()) as c:
        nokey = await c.post(url, json={"expected_revision": 2})
        bad = await c.post(url, json={"expected_revision": 2, "x": 1}, headers=h)
        ghost = await c.post(
            f"{API_PREFIX}/instances/{uuid.uuid4()}/withdraw", json={"expected_revision": 2},
            headers=h,
        )  # fmt: skip
        done = await c.post(url, json={"expected_revision": 2, "reason": "Đổi kế hoạch"}, headers=h)
    assert nokey.status_code == 400 and bad.status_code == 400 and ghost.status_code == 404
    assert done.status_code == 200, done.text
    assert done.json()["data"]["record"]["state"] == "withdrawn"


async def test_definition_and_model_together_is_rejected(env: Env) -> None:
    body = {"definition_id": "advance_approval", "model_id": "advance_request", "values": VALUES}
    async with env.client(token()) as c:
        r = await c.post(
            f"{API_PREFIX}/instances", json=body, headers={"Idempotency-Key": "wf-unit-wa09-01"}
        )
    assert r.status_code == 400 and _err(r)["details"]["reason"] == "schema"


async def test_token_with_foreign_audience_is_rejected() -> None:
    from tests.unit.identity.factory import FakeJwks as Jwks

    ws = workflow_resource_settings(rs_settings(), {})
    verifier = AccessTokenVerifier(
        ws, JwksCache(Jwks([KEY]), 300, 10, FakeClock()), FakeClock(),
        clients=StaticClientAllowlist(ws.allowed_clients, ws.human_clients),
    )  # fmt: skip
    auth = IdentityWorkflowAuth(ws, verifier, frozenset({RESOURCE}))
    assert auth.verify(token()).subject == "sub-viet"
    with pytest.raises(InvalidTokenError):
        auth.verify(token(aud=[RESOURCE, "xt-workflow"], scope="openid mcp-xt xt-workflow"))


def test_openapi_declares_observed_statuses() -> None:
    paths = openapi_document()["paths"]
    inst = paths[f"{API_PREFIX}/instances"]["post"]["responses"]
    dec = paths[f"{API_PREFIX}/tasks/{{task_id}}/decision"]["post"]["responses"]
    bpmn = paths[f"{API_PREFIX}/definitions/{{definition_id}}/bpmn"]["get"]["responses"]
    assert {"413", "422", "428", "429", "405", "501"} <= set(inst) <= set(dec) | {"201"}
    assert "304" in bpmn and "content" not in bpmn["304"]
    assert f"{API_PREFIX}/instances/{{instance_id}}/withdraw" in paths
