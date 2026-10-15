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

import base64
import json
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
from xt.entrypoints.composition.process import ProcessContext
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
    )


def _audiences(token: str) -> list[str]:
    """`aud` của JWT đã được `AccessTokenVerifier` kiểm chữ ký (chỉ đọc lại payload)."""
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        raise InvalidTokenError("malformed") from None
    aud = claims.get("aud") if isinstance(claims, dict) else None
    if isinstance(aud, str):
        return [aud]
    return [a for a in aud if isinstance(a, str)] if isinstance(aud, list) else []


def _quote(v: str) -> str:
    return v.replace("\\", "\\\\").replace('"', '\\"')


@dataclass(frozen=True, slots=True)
class IdentityWorkflowAuth:
    """`WorkflowAuth` trên `AccessTokenVerifier`; challenge RFC 6750 (không có PRM: đây không
    phải resource MCP)."""

    settings: ResourceServerSettings
    verifier: AccessTokenVerifier
    # Audience của bề mặt khác (resource MCP): token mang thêm aud này bị từ chối, để token lộ ở
    # một bề mặt không bao giờ dùng được cả hai (WA-08), kể cả khi IdP lỡ phát token nhiều aud.
    foreign_audiences: frozenset[str] = frozenset()

    def bearer(self, authorization: str | None) -> str | None:
        return extract_bearer(authorization)

    def verify(self, token: str) -> Principal:
        principal = self.verifier.verify(token)  # chữ ký + iss/aud/exp/azp/scope đã kiểm
        if self.foreign_audiences & set(_audiences(token)):
            raise InvalidTokenError("audience", "token also issued for another resource")
        return principal

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
            ref = PgWorkflowStore(rt.uow).ref_for(instance_id)
            if ref is None:
                return None
            try:
                info = pc.service().get(instance_id)
            except DecisionError:
                return None
            tasks = tuple(
                InstanceTask(
                    task_id=str(t["task_id"]),
                    step=str(t["step"]),
                    name=str(t["name"]),
                    kind=str(t["kind"]),
                    outcomes=tuple(str(o) for o in t.get("outcomes") or ()),
                    assignees=tuple(person(u) for u in t.get("candidates") or ()),
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
        IdentityWorkflowAuth(ws, verifier, frozenset({rs.resource})),
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
