# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Composition root thật: nối adapter Postgres / process engine / danh tính vào 18 tool.

`XT_MCP_SERVICES=xt.entrypoints.composition:build`. Mọi cấu hình bắt buộc; thiếu thì dừng với
danh sách lỗi (không mặc định giả, không fallback in-memory):

| Biến | Ý nghĩa |
|---|---|
| `XT_DB_APP_URL` | DSN role `xt_app` (NOBYPASSRLS) cho mọi lời gọi tool |
| `XT_DB_MIGRATOR_URL` | DSN role `xt_migrator` (diễn tập kế hoạch đổi model, lệnh quản trị) |
| `XT_AUDIT_KEY_FILE`, `XT_AUDIT_KEY_ID` | Khóa HMAC audit (G-M2) + kid; DB chỉ giữ dấu vân tay |
| `XT_CURSOR_KEY_FILE` | Khóa ≥ 32 byte ký MAC cursor phân trang |
| `XT_PII_KEY_FILE` | Khóa ≥ 32 byte HMAC token `[PII:…]` |
| `XT_DESK_URL` | URL công khai của `/desk/confirm/{id}`; origin = origin xT của MCP App |
| `XT_PII_REVEAL_ORIGINS` | Origin Desk được gọi `POST /pii/reveal` (CORS) |
| `XT_PII_REVEAL_CLIENTS` | Client được xin vé xem PII (`pii_reveal_ticket`) |
| `XT_PII_REVEAL_TICKETS_PER_MINUTE` | Quota vé xem PII mỗi người mỗi phút |
| `XT_DPOP_REQUIRED` | `true`: `XT_DPOP_CLIENTS` buộc token gắn DPoP; vé PII gắn khóa DPoP |
| `XT_CONFIRM_AUTHORIZATION_URL`, `XT_CONFIRM_TOKEN_URL` | Endpoint OIDC của client confirm |
| `XT_CONFIRM_*`, `XT_RECEIPT_*`, `XT_IDP_*` | Step-up + biên nhận (docs/identity.md) |

Luồng và output thật: `docs/integration.md`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from starlette.routing import BaseRoute

from xt.adapters.db.connection import AuditKey, Database
from xt.adapters.identity import (
    DpopSettings,
    DpopVerifier,
    IdentityConfigError,
    ReceiptService,
    StepUpVerifier,
    build_jwks_cache,
    load_dpop_settings,
    load_receipt_settings,
    load_step_up_settings,
)
from xt.adapters.identity.config import ReceiptSettings, StepUpSettings
from xt.adapters.mcp import AppsBundle, register_apps
from xt.adapters.mcp.apps.registry import normalize_origin
from xt.adapters.mcp.server import AppTool
from xt.adapters.workflow.bpmn.spiff import SpiffProcessEngine
from xt.application.tools import (
    CallContext,
    ToolDispatcher,
    ToolOutcome,
    ToolServices,
    ToolSettings,
)
from xt.application.tools.handlers import HANDLERS
from xt.contracts.tools.apps import PII_REVEAL_TICKET
from xt.entrypoints.composition.confirm_web import ConfirmPages, ConfirmSettings
from xt.entrypoints.composition.model_changes import ModelChangeDecisions
from xt.entrypoints.composition.pii import PostgresPiiLane
from xt.entrypoints.composition.pii_reveal import PiiReveal, RevealSettings
from xt.entrypoints.composition.process import ProcessToolAdapter
from xt.entrypoints.composition.records import (
    ModelCatalogAdapter,
    ModelChangeServiceAdapter,
    RecordServiceAdapter,
)
from xt.entrypoints.composition.runtime import Runtime, ScopedDispatcher
from xt.entrypoints.composition.security import PeopleDirectory, ToolAccessGuard
from xt.entrypoints.composition.workspace import Files, History, Workspace

__all__ = ["Composition", "CompositionConfig", "CompositionConfigError", "build"]


class CompositionConfigError(ValueError):
    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("; ".join(problems))


def _csv(env: Mapping[str, str], name: str) -> list[str]:
    return [w.strip() for w in env.get(name, "").split(",") if w.strip()]


def _key_file(env: Mapping[str, str], name: str, problems: list[str]) -> bytes:
    raw = env.get(name, "").strip()
    if not raw:
        problems.append(f"{name} chưa đặt (tệp khóa bí mật ≥ 32 byte)")
        return b""
    try:
        data = Path(raw).read_bytes().strip()
    except OSError as e:
        problems.append(f"{name}: không đọc được tệp ({e.strerror})")
        return b""
    if len(data) < 32:
        problems.append(f"{name}: khóa phải ≥ 32 byte")
    return data


@dataclass(frozen=True)
class CompositionConfig:
    app_dsn: str = field(repr=False)
    migrator_dsn: str = field(repr=False)
    cursor_key: bytes = field(repr=False)
    pii_key: bytes = field(repr=False)
    audit_key: AuditKey = field(repr=False)
    confirm: ConfirmSettings
    receipts: ReceiptSettings
    human_clients: frozenset[str]
    reveal: RevealSettings
    dpop: DpopSettings = field(default_factory=DpopSettings)

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> CompositionConfig:
        problems: list[str] = []
        app = env.get("XT_DB_APP_URL", "").strip()
        mig = env.get("XT_DB_MIGRATOR_URL", "").strip()
        if not app:
            problems.append("XT_DB_APP_URL chưa đặt (DSN role xt_app)")
        if not mig:
            problems.append("XT_DB_MIGRATOR_URL chưa đặt (DSN role xt_migrator)")
        cursor = _key_file(env, "XT_CURSOR_KEY_FILE", problems)
        pii = _key_file(env, "XT_PII_KEY_FILE", problems)
        audit = _key_file(env, "XT_AUDIT_KEY_FILE", problems)
        kid_raw = env.get("XT_AUDIT_KEY_ID", "").strip()
        if not kid_raw.isdigit() or int(kid_raw) < 1:
            problems.append("XT_AUDIT_KEY_ID phải là số nguyên ≥ 1 (kid của khóa audit đang dùng)")
        desk = env.get("XT_DESK_URL", "").strip()
        authz = env.get("XT_CONFIRM_AUTHORIZATION_URL", "").strip()
        token = env.get("XT_CONFIRM_TOKEN_URL", "").strip()
        allow_http = env.get("XT_IDP_ALLOW_HTTP", "").strip().lower() in ("1", "true", "yes")
        for name, url in (
            ("XT_DESK_URL", desk),
            ("XT_CONFIRM_AUTHORIZATION_URL", authz),
            ("XT_CONFIRM_TOKEN_URL", token),
        ):
            if not url:
                problems.append(f"{name} chưa đặt")
            elif not (url.startswith("https://") or (allow_http and url.startswith("http://"))):
                problems.append(f"{name} phải là URL https:// (http chỉ khi XT_IDP_ALLOW_HTTP)")
        step_up: StepUpSettings | None = None
        receipts: ReceiptSettings | None = None
        try:
            step_up = load_step_up_settings(env)
        except IdentityConfigError as e:
            problems += e.problems
        try:
            receipts = load_receipt_settings(env)
        except IdentityConfigError as e:
            problems += e.problems
        dpop = DpopSettings()
        try:
            dpop = load_dpop_settings(env)
        except IdentityConfigError as e:
            problems += e.problems
        humans = frozenset(
            w.strip() for w in env.get("XT_MCP_HUMAN_CLIENTS", "").split(",") if w.strip()
        )
        if step_up is not None and humans - {step_up.client_id}:
            # M6: chỉ client CONFIRM là người; client chat của Desk là agent (on_behalf_of).
            problems.append(
                "XT_MCP_HUMAN_CLIENTS chỉ được chứa client xác nhận (XT_CONFIRM_CLIENT_ID)"
            )
        reveal_origins: list[str] = []
        for o in _csv(env, "XT_PII_REVEAL_ORIGINS"):
            try:
                reveal_origins.append(normalize_origin(o))
            except ValueError as e:
                problems.append(f"XT_PII_REVEAL_ORIGINS: {e}")
        if not reveal_origins:
            problems.append(
                "XT_PII_REVEAL_ORIGINS chưa đặt (origin của xT Desk được gọi POST /pii/reveal)"
            )
        reveal_clients = frozenset(_csv(env, "XT_PII_REVEAL_CLIENTS"))
        if not reveal_clients:
            problems.append(
                "XT_PII_REVEAL_CLIENTS chưa đặt (client của Desk được xin vé xem PII, vd"
                " xt-desk-agent)"
            )
        desk_origin = ""
        if desk:
            try:
                desk_origin = normalize_origin(desk)
            except ValueError as e:
                problems.append(f"XT_DESK_URL: {e}")
        stray = set(reveal_origins) - {desk_origin}
        if desk_origin and stray:
            # PIIR-07: chỉ trang Desk (origin của XT_DESK_URL) là kênh người; origin sandbox của
            # MCP App hay host khác không bao giờ được đổi vé.
            problems.append(
                f"XT_PII_REVEAL_ORIGINS chỉ được là origin của XT_DESK_URL ({desk_origin}):"
                f" {sorted(stray)}"
            )
        rate_raw = env.get("XT_PII_REVEAL_TICKETS_PER_MINUTE", "").strip()
        if not rate_raw.isdigit() or not 1 <= int(rate_raw) <= 600:
            problems.append(
                "XT_PII_REVEAL_TICKETS_PER_MINUTE phải là số nguyên 1..600 (quota vé xem PII"
                " mỗi người mỗi phút)"
            )
        if problems:
            raise CompositionConfigError(problems)
        assert step_up is not None and receipts is not None
        return cls(
            app,
            mig,
            cursor,
            pii,
            AuditKey(int(kid_raw), audit),
            ConfirmSettings(desk, authz, token, step_up, step_up.idp.http_timeout_s),
            receipts,
            humans,
            RevealSettings(
                desk_origin,
                frozenset(reveal_origins),
                reveal_clients,
                int(rate_raw),
                dpop_required=dpop.required,
            ),
            dpop,
        )


class Composition:
    """Bộ port thật + trang xác nhận. `serve` gọi `dispatcher()` rồi `http_routes()`."""

    def __init__(self, config: CompositionConfig, runtime: Runtime) -> None:
        self.config = config
        self.runtime = runtime
        self.engine = SpiffProcessEngine()
        self._jwks = build_jwks_cache(config.confirm.step_up.idp)
        self._reveal: PiiReveal | None = None

    def services(self, settings: ToolSettings) -> ToolServices:
        rt, key = self.runtime, self.config.cursor_key
        guard = ToolAccessGuard(rt)
        model_changes = ModelChangeDecisions(rt, guard)
        process = ProcessToolAdapter(
            rt, cursor_key=key, engine=self.engine, model_changes=model_changes, access=guard
        )
        records = RecordServiceAdapter(
            rt,
            cursor_key=key,
            transitions=process.plan_transition,
            transition_commit=process.commit,
            model_changes=model_changes,
            step_transitions=process.available_steps,
        )
        process.record_view = records
        return ToolServices(
            access=guard,
            workspace=Workspace(rt, settings),
            models=ModelCatalogAdapter(rt),
            directory=PeopleDirectory(rt, key),
            records=records,
            processes=process,
            inbox=process,
            history=History(rt, key, self.config.human_clients),
            files=Files(),
            model_changes=ModelChangeServiceAdapter(rt),
            pii=PostgresPiiLane(rt, self.config.pii_key),
            operations=rt.operations,
            transactions=rt.uow,
            ids=_Ids(),
        )

    def dispatcher(self, settings: ToolSettings) -> ToolDispatcher:
        return ScopedDispatcher(self.runtime, self.services(settings), settings, HANDLERS)

    def confirm_pages(self, dispatcher: ToolDispatcher) -> ConfirmPages:
        cfg = self.config

        def verifier(store: Any) -> StepUpVerifier:
            return StepUpVerifier(cfg.confirm.step_up, self._jwks, store=store)

        def receipts(store: Any) -> ReceiptService:
            return ReceiptService.from_settings(
                cfg.receipts, store, max_evidence_age_s=cfg.confirm.step_up.max_auth_age_s
            )

        decisions = ModelChangeDecisions(self.runtime, ToolAccessGuard(self.runtime))
        return ConfirmPages(
            self.runtime, dispatcher, cfg.confirm, verifier, receipts, model_changes=decisions
        )

    def _dpop_replay(self) -> Any:
        """Cùng bảng `xt_identity.dpop_jti` với /mcp khi `XT_DPOP_REPLAY_STORE=postgres`."""
        if self.config.dpop.replay_store != "postgres":
            return None
        from xt.adapters.db.dpop_replay import PgDpopReplayStore

        return PgDpopReplayStore(self.runtime.app_db)

    def reveal(self) -> PiiReveal:
        if self._reveal is None:
            self._reveal = PiiReveal(
                self.runtime,
                ToolAccessGuard(self.runtime),
                self.config.reveal,
                dpop=DpopVerifier(self.config.dpop, self._dpop_replay()),
            )
        return self._reveal

    def http_routes(self, dispatcher: ToolDispatcher) -> list[BaseRoute]:
        return [*self.confirm_pages(dispatcher).routes(), *self.reveal().routes()]

    def mcp_apps(self, dispatcher: ToolDispatcher) -> AppsBundle:
        """3 MCP App + tool app-only (`org_person_details`, `records_table_page`,
        `pii_reveal_ticket`), mỗi lời gọi tool app-only chạy trong một transaction tenant."""
        services = dispatcher.services
        people = services.directory
        assert isinstance(people, PeopleDirectory)
        bundle = register_apps(
            xt_origin=self.config.reveal.origin, dispatcher=dispatcher, people=people
        )
        reveal = self.reveal()
        tools = [
            *bundle.app_tools,
            AppTool(PII_REVEAL_TICKET, lambda ctx, a, d: reveal.mint(dispatcher, ctx, a, d)),
        ]
        if isinstance(dispatcher, ScopedDispatcher):
            tools = [_scoped(dispatcher, t) for t in tools]
        return AppsBundle(bundle.ui_resources, tuple(tools))

    def close(self) -> None:
        self.runtime.close()
        self.runtime.app_db.close()


def _scoped(d: ScopedDispatcher, tool: AppTool) -> AppTool:
    run = tool.run

    def scoped(ctx: CallContext, args: Mapping[str, Any], dups: Sequence[str]) -> ToolOutcome:
        return d.scoped(tool.contract.name, ctx, lambda: run(ctx, args, dups))

    return AppTool(tool.contract, scoped)


class _Ids:
    def new_id(self, prefix: str) -> str:
        import secrets

        return f"{prefix}_{secrets.token_hex(16)}"


def build(env: Mapping[str, str]) -> Composition:
    cfg = CompositionConfig.from_env(env)
    app_db = Database(cfg.app_dsn, min_size=1, max_size=16, audit_key=cfg.audit_key)
    return Composition(
        cfg, Runtime(app_db=app_db, migrator_dsn=cfg.migrator_dsn, audit_key=cfg.audit_key)
    )
