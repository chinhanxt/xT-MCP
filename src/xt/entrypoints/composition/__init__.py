# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Composition root thật: nối adapter Postgres / process engine / danh tính vào 17 tool.

`XT_MCP_SERVICES=xt.entrypoints.composition:build`. Mọi cấu hình bắt buộc; thiếu thì dừng với
danh sách lỗi (không mặc định giả, không fallback in-memory):

| Biến | Ý nghĩa |
|---|---|
| `XT_DB_APP_URL` | DSN role `xt_app` (NOBYPASSRLS) cho mọi lời gọi tool |
| `XT_DB_MIGRATOR_URL` | DSN role `xt_migrator` (diễn tập kế hoạch đổi model, lệnh quản trị) |
| `XT_AUDIT_KEY_FILE`, `XT_AUDIT_KEY_ID` | Khóa HMAC audit (G-M2) + kid; DB chỉ giữ dấu vân tay |
| `XT_CURSOR_KEY_FILE` | Khóa ≥ 32 byte ký MAC cursor phân trang |
| `XT_PII_KEY_FILE` | Khóa ≥ 32 byte HMAC token `[PII:…]` |
| `XT_DESK_URL` | URL công khai của trang xác nhận (`/desk/confirm/{id}`) |
| `XT_CONFIRM_AUTHORIZATION_URL`, `XT_CONFIRM_TOKEN_URL` | Endpoint OIDC của client confirm |
| `XT_CONFIRM_*`, `XT_RECEIPT_*`, `XT_IDP_*` | Step-up + biên nhận (docs/identity.md) |
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from starlette.routing import BaseRoute

from xt.adapters.db.connection import AuditKey, Database
from xt.adapters.identity import (
    IdentityConfigError,
    ReceiptService,
    StepUpVerifier,
    build_jwks_cache,
    load_receipt_settings,
    load_step_up_settings,
)
from xt.adapters.identity.config import ReceiptSettings, StepUpSettings
from xt.adapters.workflow.bpmn.spiff import SpiffProcessEngine
from xt.application.tools import ToolDispatcher, ToolServices, ToolSettings
from xt.application.tools.handlers import HANDLERS
from xt.entrypoints.composition.confirm_web import ConfirmPages, ConfirmSettings
from xt.entrypoints.composition.pii import PostgresPiiLane
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
        humans = frozenset(
            w.strip() for w in env.get("XT_MCP_HUMAN_CLIENTS", "").split(",") if w.strip()
        )
        if step_up is not None and humans - {step_up.client_id}:
            # M6: chỉ client CONFIRM là người; client chat của Desk là agent (on_behalf_of).
            problems.append(
                "XT_MCP_HUMAN_CLIENTS chỉ được chứa client xác nhận (XT_CONFIRM_CLIENT_ID)"
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
        )


class Composition:
    """Bộ port thật + trang xác nhận. `serve` gọi `dispatcher()` rồi `http_routes()`."""

    def __init__(self, config: CompositionConfig, runtime: Runtime) -> None:
        self.config = config
        self.runtime = runtime
        self.engine = SpiffProcessEngine()
        self._jwks = build_jwks_cache(config.confirm.step_up.idp)

    def services(self, settings: ToolSettings) -> ToolServices:
        rt, key = self.runtime, self.config.cursor_key
        process = ProcessToolAdapter(rt, cursor_key=key, engine=self.engine)
        records = RecordServiceAdapter(
            rt,
            cursor_key=key,
            transitions=process.plan_transition,
            transition_commit=process.commit,
        )
        return ToolServices(
            access=ToolAccessGuard(rt),
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

        return ConfirmPages(self.runtime, dispatcher, cfg.confirm, verifier, receipts)

    def http_routes(self, dispatcher: ToolDispatcher) -> list[BaseRoute]:
        return list(self.confirm_pages(dispatcher).routes())

    def close(self) -> None:
        self.runtime.close()
        self.runtime.app_db.close()


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
