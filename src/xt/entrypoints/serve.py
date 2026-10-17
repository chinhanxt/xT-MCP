# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""`xt serve --transport http|stdio`: composition root của MCP server.

Nối lớp danh tính (`xt.adapters.identity`) vào adapter MCP qua `HttpAuth`/callable, nạp bộ port
ứng dụng từ factory khai trong `XT_MCP_SERVICES` (`module:hàm`, nhận `env`, trả `ToolServices`).
Thiếu hoặc sai cấu hình thì dừng ngay với danh sách lỗi; không có giá trị mặc định giả.
"""

from __future__ import annotations

import argparse
import importlib
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit

from xt.adapters.identity import (
    DpopContext,
    DpopVerifier,
    IdentityConfigError,
    build_client_allowlist,
    build_dpop_verifier,
    build_jwks_cache,
    build_protected_resource_metadata,
    build_revocation_guard,
    challenge_for,
    extract_credentials,
    load_connector_registry_settings,
    load_introspection_settings,
    load_resource_server_settings,
    protected_resource_metadata_path,
)
from xt.adapters.identity.access_token import AccessTokenVerifier
from xt.adapters.identity.config import ResourceServerSettings
from xt.adapters.identity.introspection import RevocationGuard
from xt.adapters.mcp import (
    AppsBundle,
    Challenge,
    ClientClassifier,
    RequestFacts,
    XtMcpServer,
    build_http_app,
    http_caller,
    serve_stdio,
    stdio_caller,
    token_file_source,
)
from xt.application.model_engine.service import MAX_GROUPS, MAX_GROUPS_CAP
from xt.application.tools import GATED_CAPABILITIES, ToolDispatcher, ToolServices, ToolSettings
from xt.contracts.prompt_catalog import PromptError, default_prompts
from xt.contracts.tools import Profile
from xt.domain.identity import IdentityError, Principal

TRANSPORTS = ("http", "stdio")


class ServeConfigError(Exception):
    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("; ".join(problems))


@runtime_checkable
class ComposedServices(Protocol):
    """Factory thật (vd `xt.entrypoints.composition:build`): tự dựng dispatcher (bọc transaction
    theo tenant) và route HTTP phụ (trang `/desk/confirm/{id}`)."""

    def dispatcher(self, settings: ToolSettings) -> ToolDispatcher: ...

    def http_routes(self, dispatcher: ToolDispatcher) -> list[Any]: ...

    def close(self) -> None: ...


@runtime_checkable
class AppsProvider(Protocol):
    """Composition có MCP Apps (`ui://xt/*`) + tool app-only (docs/mcp-server.md)."""

    def mcp_apps(self, dispatcher: ToolDispatcher) -> AppsBundle: ...


class TokenVerifier(Protocol):
    """Hình dạng public của `AccessTokenVerifier` (test thay bằng bản giả)."""

    def verify(
        self,
        token: str,
        required_scopes: frozenset[str] = ...,
        *,
        dpop: DpopContext | None = ...,
    ) -> Principal: ...


@dataclass(frozen=True, slots=True)
class IdentityHttpAuth:
    """`HttpAuth` dựng hoàn toàn từ API public của lớp danh tính. `dpop` cho biết các origin công
    khai (htu) của server; None = không có origin nào, token gắn DPoP bị từ chối."""

    settings: ResourceServerSettings
    verifier: TokenVerifier
    dpop: DpopVerifier | None = None

    @property
    def prm_path(self) -> str:
        return protected_resource_metadata_path(self.settings.resource)

    def prm_document(self) -> Mapping[str, Any]:
        return build_protected_resource_metadata(self.settings)

    def bearer(self, authorization: str | None) -> str | None:
        cred = extract_credentials(authorization)
        return None if cred is None else cred[1]

    def verify(self, token: str, request: RequestFacts) -> Principal:
        cred = extract_credentials(request.authorization)
        ctx = DpopContext(
            scheme=cred[0] if cred else "bearer",
            proofs=request.dpop,
            method=request.method,
            htu=self.dpop.candidates(request.path) if self.dpop else frozenset(),
        )
        return self.verifier.verify(token, dpop=ctx)

    def challenge(self, error: IdentityError | None) -> Challenge:
        c = challenge_for(self.settings, error)
        return Challenge(c.status, dict(c.headers), dict(c.body))


@dataclass(frozen=True, slots=True)
class ServeConfig:
    transport: str
    profile: Profile
    services_factory: str
    tool_settings: ToolSettings
    local_clients: frozenset[str]
    host: str | None = None
    port: int | None = None
    allowed_hosts: tuple[str, ...] = ()
    allowed_origins: tuple[str, ...] = ()
    token_file: Path | None = None
    env: Mapping[str, str] = field(default_factory=dict, repr=False)


def _words(env: Mapping[str, str], name: str) -> tuple[str, ...]:
    return tuple(w.strip() for w in env.get(name, "").split(",") if w.strip())


def _flag(env: Mapping[str, str], name: str) -> bool:
    return env.get(name, "").strip().lower() in ("1", "true", "yes")


def local_tier_problems(env: Mapping[str, str], local: frozenset[str]) -> list[str]:
    """Quyết định "Tier PII của Desk chat" (docs/decisions.md, R2-M4): tier `local` (PII full)
    chỉ cho client **confidential** phía server. Client public (kể cả `xt-desk-agent`, PKCE,
    redirect loopback) tối đa masked. Người vận hành phải khai client confidential trong
    `XT_MCP_CONFIDENTIAL_CLIENTS`; chỉ profile dev mới được nới bằng `XT_MCP_DEV_LOCAL_TIER`."""
    problems: list[str] = []
    if not local:
        return problems
    allowed = frozenset(_words(env, "XT_MCP_ALLOWED_CLIENTS"))
    human = frozenset(_words(env, "XT_MCP_HUMAN_CLIENTS"))
    confidential = frozenset(_words(env, "XT_MCP_CONFIDENTIAL_CLIENTS"))
    if local - allowed:
        problems.append(
            f"XT_MCP_LOCAL_CLIENTS phải thuộc XT_MCP_ALLOWED_CLIENTS: {sorted(local - allowed)}"
        )
    if local & human:
        problems.append(f"XT_MCP_LOCAL_CLIENTS không được là client người: {sorted(local & human)}")
    public = local - confidential
    if public and not _flag(env, "XT_MCP_DEV_LOCAL_TIER"):
        problems.append(
            "XT_MCP_LOCAL_CLIENTS chỉ nhận client confidential phía server (khai trong "
            f"XT_MCP_CONFIDENTIAL_CLIENTS); client public tối đa tier masked: {sorted(public)}"
        )
    return problems


def load_serve_config(
    env: Mapping[str, str],
    transport: str,
    profile: str,
    host: str | None,
    port: int | None,
) -> ServeConfig:
    problems: list[str] = []
    if transport not in TRANSPORTS:
        problems.append(f"--transport phải là một trong {TRANSPORTS}")
    try:
        prof = Profile(profile)
    except ValueError:
        problems.append(f"--profile phải là một trong {[p.value for p in Profile]}")
        prof = Profile.FULL
    factory = env.get("XT_MCP_SERVICES", "").strip()
    mod, sep, attr = factory.partition(":")
    if not factory:
        problems.append("XT_MCP_SERVICES chưa đặt (module:hàm trả ToolServices)")
    elif not sep or not all(p.isidentifier() for p in mod.split(".")) or not attr.isidentifier():
        problems.append("XT_MCP_SERVICES phải có dạng package.module:factory")
    desk = env.get("XT_DESK_URL", "").strip()
    if not desk:
        problems.append("XT_DESK_URL chưa đặt (URL công khai của xT Desk cho confirm_url)")
    caps = frozenset(_words(env, "XT_MCP_CAPABILITIES"))
    if caps - set(GATED_CAPABILITIES):
        problems.append(
            f"XT_MCP_CAPABILITIES chỉ nhận {sorted(GATED_CAPABILITIES)}: "
            f"{sorted(caps - set(GATED_CAPABILITIES))}"
        )
    channels = _words(env, "XT_CONFIRM_CHANNELS") or ("desk",)
    ttl_raw = env.get("XT_CONFIRMATION_TTL_SECONDS", "600").strip()
    ttl = int(ttl_raw) if ttl_raw.isdigit() else -1
    if not 60 <= ttl <= 3600:
        problems.append("XT_CONFIRMATION_TTL_SECONDS phải là số nguyên 60..3600")
    groups_raw = env.get("XT_MAX_AGGREGATE_GROUPS", str(MAX_GROUPS)).strip()
    groups = int(groups_raw) if groups_raw.isdigit() else -1
    if not 1 <= groups <= MAX_GROUPS_CAP:
        problems.append(f"XT_MAX_AGGREGATE_GROUPS phải là số nguyên 1..{MAX_GROUPS_CAP}")
    tool_settings: ToolSettings | None = None
    if desk and not problems:
        try:
            tool_settings = ToolSettings(
                confirm_base_url=desk,
                confirmation_ttl=timedelta(seconds=ttl),
                channels=channels,
                enabled_capabilities=caps,
                max_aggregate_groups=groups,
            )
        except ValueError as e:
            problems.append(f"cấu hình tool: {e}")
    local = frozenset(_words(env, "XT_MCP_LOCAL_CLIENTS"))
    problems += local_tier_problems(env, local)
    token_file: Path | None = None
    allowed: tuple[str, ...] = ()
    origins: tuple[str, ...] = ()
    if transport == "http":
        if not host:
            problems.append("thiếu --host (hoặc XT_MCP_HOST)")
        if port is None or not 1 <= port <= 65535:
            problems.append("thiếu/sai --port (hoặc XT_MCP_PORT)")
        allowed = _words(env, "XT_MCP_ALLOWED_HOSTS")
        origins = _words(env, "XT_MCP_ALLOWED_ORIGINS")
        bad_origins = [o for o in origins if not o.startswith(("https://", "http://"))]
        if bad_origins:
            problems.append(f"XT_MCP_ALLOWED_ORIGINS phải là origin http(s): {bad_origins}")
    elif transport == "stdio":
        raw = env.get("XT_MCP_TOKEN_FILE", "").strip()
        if not raw:
            problems.append(
                "XT_MCP_TOKEN_FILE chưa đặt (access token client-credentials của agent)"
            )
        else:
            token_file = Path(raw)
            if not token_file.is_file():
                problems.append(f"XT_MCP_TOKEN_FILE không tồn tại: {token_file}")
    if problems:
        raise ServeConfigError(problems)
    assert tool_settings is not None
    return ServeConfig(
        transport=transport,
        profile=prof,
        services_factory=factory,
        tool_settings=tool_settings,
        local_clients=local,
        host=host,
        port=port,
        allowed_hosts=allowed,
        allowed_origins=origins,
        token_file=token_file,
        env=env,
    )


def load_services(spec: str, env: Mapping[str, str]) -> ToolServices | ComposedServices:
    module, _, attr = spec.partition(":")
    try:
        factory: Callable[[Mapping[str, str]], Any] = getattr(importlib.import_module(module), attr)
    except (ImportError, AttributeError) as e:
        raise ServeConfigError([f"XT_MCP_SERVICES={spec}: không nạp được ({e})"]) from e
    try:
        services = factory(env)
    except ValueError as e:
        problems = getattr(e, "problems", None)
        raise ServeConfigError(list(problems) if problems else [f"{spec}: {e}"]) from e
    if not isinstance(services, ToolServices | ComposedServices):
        raise ServeConfigError(
            [f"XT_MCP_SERVICES={spec}: factory phải trả ToolServices hoặc composition"]
        )
    return services


def build_server(
    cfg: ServeConfig,
    services: ToolServices | ComposedServices,
    rs: ResourceServerSettings,
    verifier: TokenVerifier,
    revocation: RevocationGuard,
    dispatcher: ToolDispatcher | None = None,
) -> XtMcpServer:
    if dispatcher is None:
        dispatcher = (
            services.dispatcher(cfg.tool_settings)
            if isinstance(services, ComposedServices)
            else ToolDispatcher(services, cfg.tool_settings)
        )
    apps = (
        services.mcp_apps(dispatcher) if isinstance(services, AppsProvider) else AppsBundle((), ())
    )
    resolver = (
        http_caller
        if cfg.transport == "http"
        else stdio_caller(token_file_source(cfg.token_file or Path()), verifier.verify)
    )
    return XtMcpServer(
        dispatcher,
        resolver,
        prompts=default_prompts(),
        profile=cfg.profile,
        classify=ClientClassifier(cfg.local_clients),
        ensure_active=revocation.ensure_active,
        ui_resources=apps.ui_resources,
        app_tools=apps.app_tools,
    )


def allowed_hosts_for(
    rs: ResourceServerSettings, extra: Sequence[str], host: str, port: int
) -> list[str]:
    public = urlsplit(rs.resource).netloc
    return sorted({public, f"{host}:{port}", *extra})


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="xt serve", description="Chạy MCP server của xT")
    p.add_argument("--transport", required=True, choices=TRANSPORTS)
    p.add_argument("--host", default=os.environ.get("XT_MCP_HOST"))
    p.add_argument("--port", type=int, default=_env_int("XT_MCP_PORT"))
    p.add_argument("--profile", default=Profile.FULL.value, choices=[x.value for x in Profile])
    return p


def _env_int(name: str) -> int | None:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw.isdigit() else None


@dataclass
class Prepared:
    """Server đã nối đủ (chưa mở cổng): `app` cho http, `server` cho stdio."""

    cfg: ServeConfig
    server: XtMcpServer
    services: ToolServices | ComposedServices
    rs: ResourceServerSettings
    verifier: AccessTokenVerifier
    dispatcher: ToolDispatcher
    dpop: DpopVerifier | None = None
    replay_db: Any = None  # pool Postgres của cache jti DPoP (XT_DPOP_REPLAY_STORE=postgres)

    def http_app(self) -> Any:
        """App Starlette: `/mcp` + PRM + `/healthz` và route phụ của composition (trang
        `/desk/confirm/{id}`), cùng lifespan của session manager MCP."""
        from starlette.applications import Starlette

        cfg = self.cfg
        assert cfg.host is not None and cfg.port is not None
        app = build_http_app(
            self.server,
            IdentityHttpAuth(self.rs, self.verifier, self.dpop),
            mcp_path=urlsplit(self.rs.resource).path or "/mcp",
            allowed_hosts=allowed_hosts_for(self.rs, cfg.allowed_hosts, cfg.host, cfg.port),
            allowed_origins=list(cfg.allowed_origins),
        )
        if not isinstance(self.services, ComposedServices):
            return app
        from xt.entrypoints.desk_llm_http import with_desk_llm
        from xt.entrypoints.files_http import with_files_api
        from xt.entrypoints.notify_http import with_notifications
        from xt.entrypoints.studio_http import with_studio
        from xt.entrypoints.workflow_http import with_workflow_api

        extra = self.services.http_routes(self.dispatcher)
        merged = Starlette(routes=[*extra, *app.routes], lifespan=app.router.lifespan_context)
        merged = with_studio(self, self.cfg.env, merged)
        # /files/v1 chỉ mount khi XT_FILES_S3_ENDPOINT đã đặt (with_files_api tự kiểm).
        return with_desk_llm(
            self,
            self.cfg.env,
            with_notifications(
                self,
                self.cfg.env,
                with_files_api(self, self.cfg.env, with_workflow_api(self, self.cfg.env, merged)),
            ),
        )

    def close(self) -> None:
        if isinstance(self.services, ComposedServices):
            self.services.close()
        if self.replay_db is not None:
            self.replay_db.close()


def prepare(env: Mapping[str, str], args: argparse.Namespace) -> Prepared:
    """Đọc cấu hình + nối mọi thành phần; lỗi -> ServeConfigError/IdentityConfigError."""
    cfg = load_serve_config(env, args.transport, args.profile, args.host, args.port)
    rs = load_resource_server_settings(env)
    revocation = build_revocation_guard(load_introspection_settings(env))
    default_prompts()  # prompt lỗi -> dừng trước khi mở cổng
    registry = load_connector_registry_settings(env) if rs.dynamic_connectors else None
    services = load_services(cfg.services_factory, env)
    dpop, replay_db = _dpop_verifier(env)
    verifier = AccessTokenVerifier(
        rs, build_jwks_cache(rs.idp), clients=build_client_allowlist(rs, registry), dpop=dpop
    )
    dispatcher = (
        services.dispatcher(cfg.tool_settings)
        if isinstance(services, ComposedServices)
        else ToolDispatcher(services, cfg.tool_settings)
    )
    server = build_server(cfg, services, rs, verifier, revocation, dispatcher)
    return Prepared(cfg, server, services, rs, verifier, dispatcher, dpop, replay_db)


def _dpop_verifier(env: Mapping[str, str]) -> tuple[DpopVerifier, Any]:
    """Cache jti DPoP: `postgres` -> bảng chung `xt_identity.dpop_jti` qua XT_DB_APP_URL (DPA-02);
    `memory` chỉ cho một tiến trình (unit test)."""
    if (env.get("XT_DPOP_REPLAY_STORE") or "").strip().lower() != "postgres":
        return build_dpop_verifier(env), None
    dsn = env.get("XT_DB_APP_URL", "").strip()
    if not dsn:
        raise IdentityConfigError(["XT_DPOP_REPLAY_STORE=postgres cần XT_DB_APP_URL (role xt_app)"])
    from xt.adapters.db.connection import Database
    from xt.adapters.db.dpop_replay import PgDpopReplayStore

    db = Database(dsn, min_size=1, max_size=4, statement_timeout_ms=3000)
    try:
        return build_dpop_verifier(env, PgDpopReplayStore(db)), db
    except Exception:
        db.close()
        raise


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    env = dict(os.environ)
    try:
        prepared = prepare(env, args)
    except (ServeConfigError, IdentityConfigError) as e:
        problems = getattr(e, "problems", [str(e)])
        print("xt serve: cấu hình không hợp lệ:", file=sys.stderr)
        for line in problems:
            print(f"  - {line}", file=sys.stderr)
        return 2
    except PromptError as e:
        print(f"xt serve: prompt lỗi: {e}", file=sys.stderr)
        return 2
    try:
        if prepared.cfg.transport == "stdio":
            import anyio

            anyio.run(serve_stdio, prepared.server)
            return 0
        import uvicorn

        cfg = prepared.cfg
        assert cfg.host is not None and cfg.port is not None
        uvicorn.run(
            prepared.http_app(), host=cfg.host, port=cfg.port, log_level="info", proxy_headers=True
        )
        return 0
    finally:
        prepared.close()


if __name__ == "__main__":
    raise SystemExit(main())
