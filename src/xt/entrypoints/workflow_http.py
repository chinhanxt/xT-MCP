# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Nối REST `/workflow/v1` (`xt.adapters.http.workflow_api`) vào `xt serve --transport http`.

Không sửa `composition/`: module này chỉ dùng API public của composition (`Runtime`,
`ProcessContext`) và của lớp danh tính. Lớp tích hợp gọi một dòng trong `Prepared.http_app()`:

    from xt.entrypoints.workflow_http import with_workflow_api
    app = with_workflow_api(self, self.cfg.env, app)

Audience riêng `xt-workflow` (docs/decisions.md "REST /workflow/v1"): token MCP (aud = URL /mcp)
và token data (aud `xt-data`) không dùng được ở đây, và ngược lại.

Biến môi trường:
- `XT_WORKFLOW_AUDIENCE` (mặc định `xt-workflow`): `aud` bắt buộc; phải khác `XT_MCP_RESOURCE`.
- `XT_WORKFLOW_REQUIRED_SCOPES` (mặc định `xt-workflow`): client scope optional của Keycloak.
- `XT_WORKFLOW_ALLOWED_CLIENTS` (mặc định = `XT_MCP_ALLOWED_CLIENTS`): `azp` được gọi API (agent
  đại diện một người); connector DCR không được.
- `XT_WORKFLOW_PUBLIC_URL` (tùy chọn): `servers[0].url` của OpenAPI, vd https://xt-api.freepro.online.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any
from uuid import UUID

from starlette.applications import Starlette
from starlette.routing import Mount

from xt.adapters.db.workflow_bridge import PgWorkflowStore
from xt.adapters.http.workflow_api import (
    AuthChallenge,
    InstanceTask,
    InstanceView,
    workflow_routes,
)
from xt.adapters.identity import (
    StaticClientAllowlist,
    build_jwks_cache,
    build_revocation_guard,
    extract_bearer,
    load_introspection_settings,
)
from xt.adapters.identity.access_token import AccessTokenVerifier
from xt.adapters.identity.config import ResourceServerSettings
from xt.adapters.mcp import ClientClassifier
from xt.application.tools import Actor
from xt.domain.identity import IdentityError, InsufficientScopeError, InvalidTokenError, Principal
from xt.domain.process.errors import DecisionError
from xt.entrypoints.composition import Composition
from xt.entrypoints.composition.model_changes import MODEL_PROPOSAL
from xt.entrypoints.composition.process import (
    ProcessContext,
    ProcessToolAdapter,
    bound_record,
    human_step,
)
from xt.entrypoints.composition.runtime import CallerError

DEFAULT_AUDIENCE = "xt-workflow"
REALM = "xt-workflow"
_DESCRIPTIONS = {
    "missing_token": "Authentication required",
    "expired": "The access token expired",
    "audience": "The access token was not issued for the workflow API",
    "client_not_allowed": "The client is not allowed to access the workflow API",
    "token_revoked": "The access token is no longer active",
}


class WorkflowHttpConfigError(ValueError):
    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("; ".join(problems))


def _words(env: Mapping[str, str], name: str) -> tuple[str, ...]:
    raw = env.get(name, "").replace(",", " ")
    return tuple(dict.fromkeys(w for w in raw.split() if w))


def workflow_resource_settings(
    rs: ResourceServerSettings, env: Mapping[str, str]
) -> ResourceServerSettings:
    """Settings của resource server MCP, đổi audience/scope/allowlist cho API workflow."""
    problems: list[str] = []
    audience = env.get("XT_WORKFLOW_AUDIENCE", "").strip() or DEFAULT_AUDIENCE
    if audience == rs.resource:
        problems.append("XT_WORKFLOW_AUDIENCE phải khác XT_MCP_RESOURCE (audience riêng)")
    if any(c.isspace() for c in audience):
        problems.append("XT_WORKFLOW_AUDIENCE không được có khoảng trắng")
    scopes = frozenset(_words(env, "XT_WORKFLOW_REQUIRED_SCOPES") or (DEFAULT_AUDIENCE,))
    if "offline_access" in scopes:
        problems.append("XT_WORKFLOW_REQUIRED_SCOPES không được chứa offline_access")
    clients = frozenset(_words(env, "XT_WORKFLOW_ALLOWED_CLIENTS")) or rs.allowed_clients
    if not clients:
        problems.append("XT_WORKFLOW_ALLOWED_CLIENTS rỗng (và XT_MCP_ALLOWED_CLIENTS rỗng)")
    if clients & rs.human_clients:
        problems.append("XT_WORKFLOW_ALLOWED_CLIENTS không được chứa client người")
    if problems:
        raise WorkflowHttpConfigError(problems)
    return replace(
        rs,
        resource=audience,
        allowed_clients=clients,
        required_scopes=scopes,
        scopes_supported=tuple(sorted(scopes)),
        dynamic_connectors=False,
        # Token mang thêm aud MCP (hoặc data) bị 401 ở workflow, và ngược lại (WA-08).
        foreign_audiences=frozenset(
            {rs.resource, "xt-data", env.get("XT_FILES_AUDIENCE", "").strip() or "xt-files"}
        )
        - {audience},
    )


def _quote(v: str) -> str:
    return v.replace("\\", "\\\\").replace('"', '\\"')


@dataclass(frozen=True, slots=True)
class IdentityWorkflowAuth:
    """`WorkflowAuth` trên `AccessTokenVerifier`; challenge RFC 6750 (không có PRM: đây không
    phải resource MCP)."""

    settings: ResourceServerSettings
    verifier: AccessTokenVerifier

    def bearer(self, authorization: str | None) -> str | None:
        return extract_bearer(authorization)

    def verify(self, token: str) -> Principal:
        return self.verifier.verify(token)  # aud lạ: settings.foreign_audiences (WA-08)

    def challenge(self, error: IdentityError | None) -> AuthChallenge:
        scope = " ".join(sorted(self.settings.required_scopes))
        if isinstance(error, InsufficientScopeError):
            need = " ".join(sorted(set(error.required) | self.settings.required_scopes))
            value = f'Bearer realm="{REALM}", error="insufficient_scope", scope="{_quote(need)}"'
            return AuthChallenge(403, {"WWW-Authenticate": value})
        parts = [f'realm="{REALM}"']
        if isinstance(error, InvalidTokenError):
            desc = _DESCRIPTIONS.get(error.reason, "The access token is invalid")
            parts += ['error="invalid_token"', f'error_description="{_quote(desc)}"']
        parts.append(f'scope="{_quote(scope)}"')
        return AuthChallenge(401, {"WWW-Authenticate": "Bearer " + ", ".join(parts)})


class CompositionWorkflowBackend:
    """`WorkflowBackend` trên runtime của composition: mỗi lời gọi = một transaction RLS của
    tenant trong token (như `ScopedDispatcher`)."""

    def __init__(self, composition: Composition) -> None:
        self.c = composition

    def _pc(self, actor: Actor, trace_id: str) -> tuple[Any, ProcessContext]:
        rt = self.c.runtime
        caller = rt.resolve(actor, trace_id)
        return caller, ProcessContext(rt, self.c.engine, caller.workflow(), caller.profile.timezone)

    def bpmn(self, actor: Actor, trace_id: str, process_id: str, revision: int) -> str | None:
        rt = self.c.runtime
        try:
            caller, pc = self._pc(actor, trace_id)
        except CallerError:
            return None

        def body() -> str | None:
            d = rt.store.definition(caller.actor, process_id, revision)
            return None if d is None or not d.active else pc.compiled(d).bpmn

        out: str | None = rt.run_as(caller, actor, body)
        return out

    def instance_ref(self, actor: Actor, trace_id: str, instance_id: str) -> str | None:
        rt = self.c.runtime
        try:
            caller = rt.resolve(actor, trace_id)
        except CallerError:
            return None
        out: str | None = rt.run_as(
            caller, actor, lambda: PgWorkflowStore(rt.uow).ref_for(instance_id)
        )
        return out

    def instance(self, actor: Actor, trace_id: str, instance_id: str) -> InstanceView | None:
        rt = self.c.runtime
        try:
            caller, pc = self._pc(actor, trace_id)
        except CallerError:
            return None

        def person(uid: Any) -> str:
            try:
                return f"person:{UUID(str(uid))}"
            except ValueError:
                return "person:unknown"

        def body() -> InstanceView | None:
            store = PgWorkflowStore(rt.uow)
            ref = store.ref_for(instance_id)
            rec = store.get_for_update(instance_id)
            if rec is None:
                return None
            if ref is None:
                # Không gắn bản ghi: chỉ người tham gia (khởi tạo, đang/đã giữ bước) thấy; người
                # khác nhận đúng câu trả lời của id không tồn tại. Kiểm trên meta TRƯỚC khi nạp
                # engine (giảm chênh lệch thời gian với id ma, O3).
                me = str(caller.person.id)
                meta = rec.state.get("meta") or {}
                involved = {str(meta.get("requester") or "")}
                involved |= {str(h.get("actor")) for h in meta.get("history") or ()}
                involved |= {
                    str(u) for a in (meta.get("assign") or {}).values() for u in a["candidates"]
                }
                if me not in involved:
                    return None
            try:
                info = pc.service().get(instance_id)
            except DecisionError:
                return None
            prt = pc.get(rec.process_id, rec.version)
            data = dict(prt.load(rec.state).data()) if prt is not None else {}

            def bound(step: str) -> str | None:
                st = human_step(prt.compiled.spec, step) if prt is not None else None
                if st is None or st.complete_via is None:
                    return None
                row = bound_record(pc, st.complete_via, data)
                return f"{st.complete_via.model}:{row['id']}" if row else None

            tasks = tuple(
                InstanceTask(
                    task_id=str(t["task_id"]),
                    step=str(t["step"]),
                    name=str(t["name"]),
                    kind=str(t["kind"]),
                    outcomes=tuple(str(o) for o in t.get("outcomes") or ()),
                    assignees=tuple(person(u) for u in t.get("candidates") or ()),
                    ref=bound(str(t["step"])) if t["kind"] in ("decision", "task") else None,
                )
                for t in info["tasks"]
            )
            return InstanceView(
                instance_id=instance_id,
                ref=ref,
                process_id=str(info["process_id"]),
                version=int(info["version"]),
                status=str(info["status"]),
                tasks=tasks,
            )

        out: InstanceView | None = rt.run_as(caller, actor, body)
        return out

    def step_state(
        self, actor: Actor, trace_id: str, ref: str, decision: str, idempotency_key: str = ""
    ) -> str | None:
        rt = self.c.runtime
        try:
            caller, pc = self._pc(actor, trace_id)
        except CallerError:
            return decision
        model, _, raw = ref.partition(":")
        try:
            rid = UUID(raw)
        except ValueError:
            return decision
        adapter = ProcessToolAdapter(rt, cursor_key=self.c.config.cursor_key, engine=self.c.engine)

        def body() -> str | None:
            binding = rt.store.bindings(caller.actor).get(model)
            if model == MODEL_PROPOSAL or (binding is not None and binding.primitive == "request"):
                return None
            if idempotency_key:
                # Gửi lại cùng Idempotency-Key (sau khi người xác nhận): bước đã xong, lấy đúng
                # to_state của lần đầu để dispatcher trả lại receipt/pending cũ.
                with rt.uow.tenant_tx(caller.actor) as cur:
                    prev = cur.execute(
                        "SELECT pending, receipt FROM xt_meta.tool_operation WHERE tenant_id=%s"
                        " AND actor_key=%s AND tool='transition_record' AND idempotency_key=%s",
                        (caller.tenant.id, actor.key, idempotency_key),
                    ).fetchone()
                if prev is not None:
                    if prev["receipt"] and prev["receipt"].get("data", {}).get("to_state"):
                        return str(prev["receipt"]["data"]["to_state"])
                    if prev["pending"]:
                        return str(prev["pending"]["arguments"].get("to_state") or "") or None
            # Không phải đề nghị: LUÔN đi transition_record (PSA-06). Không suy ra được to_state
            # (bản ghi ma, bước của người khác, quyết định không hợp lệ) -> gửi tên quyết định để
            # tool trả đúng lỗi chuẩn (not_found / not_assigned / transition_not_allowed).
            try:
                row = rt.records.get(caller.workflow(), model, rid)["record"]
            except Exception:
                return decision
            me = str(caller.person.id)
            for s in adapter.open_steps(caller, pc, model, rid, row):
                if me not in s.task.get("candidates", ()):
                    continue
                want = decision
                if decision == "approve" and "approve" not in s.step.outcomes:
                    want = "done"  # bước công việc: "approve" của REST = hoàn thành bước
                for to_state, outcome in s.via.states.items():
                    if outcome == want:
                        return to_state
            return decision

        out: str | None = rt.run_as(caller, actor, body)
        return out


def workflow_mount(prepared: Any, env: Mapping[str, str]) -> Mount:
    """`Mount("/workflow/v1")` từ `xt.entrypoints.serve.Prepared` (composition thật)."""
    services = prepared.services
    if not isinstance(services, Composition):
        raise WorkflowHttpConfigError(
            ["/workflow/v1 cần XT_MCP_SERVICES=xt.entrypoints.composition:build"]
        )
    rs: ResourceServerSettings = prepared.rs
    ws = workflow_resource_settings(rs, env)
    verifier = AccessTokenVerifier(
        ws,
        build_jwks_cache(ws.idp),
        clients=StaticClientAllowlist(ws.allowed_clients, ws.human_clients),
    )
    revocation = build_revocation_guard(load_introspection_settings(env))
    return workflow_routes(
        prepared.dispatcher,
        IdentityWorkflowAuth(ws, verifier),
        CompositionWorkflowBackend(services),
        classify=ClientClassifier(prepared.cfg.local_clients),
        ensure_active=revocation.ensure_active,
        server_url=env.get("XT_WORKFLOW_PUBLIC_URL", "").strip() or None,
    )


def with_workflow_api(prepared: Any, env: Mapping[str, str], app: Starlette) -> Starlette:
    """App của `Prepared.http_app()` + `/workflow/v1` (giữ lifespan của session manager MCP)."""
    return Starlette(
        routes=[workflow_mount(prepared, env), *app.routes],
        lifespan=app.router.lifespan_context,
    )
