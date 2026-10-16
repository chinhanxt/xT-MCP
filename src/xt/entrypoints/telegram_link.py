# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Create Telegram linking capabilities only after the owner's fresh OIDC OTP proof.

A delegated Desk/CLI token may initiate the flow, but receives only an authorization URL.
The linking code is displayed to the freshly authenticated owner in server-rendered HTML.
"""

from __future__ import annotations

import hmac
import html
import re
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from xt.adapters.db.confirmations import PgConfirmationSessionStore
from xt.adapters.db.integration import tenant_by_id
from xt.adapters.db.notify import PgNotificationRepository
from xt.adapters.identity import StepUpVerifier, build_jwks_cache
from xt.adapters.identity.step_up import (
    ConfirmationContext,
    confirm_authorization_url,
    new_pkce_pair,
)
from xt.domain.identity import IdentityError
from xt.domain.model.context import ModelActor, TenantRef
from xt.entrypoints.composition.confirm_web import ConfirmSettings
from xt.entrypoints.composition.runtime import NIL, WORKFLOW_ROLE, Caller, Runtime

COOKIE = "xt_telegram_link"
SESSION_TTL = timedelta(minutes=10)
_STATE = re.compile(r"tl_([a-f0-9-]{36})\.([A-Za-z0-9_-]{32})")
_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


def _page(message: str, *, code: str | None = None, status: int = 200) -> HTMLResponse:
    body = f"<h1>Liên kết Telegram</h1><p>{html.escape(message)}</p>"
    if code is not None:
        body += f"<pre>{html.escape('/link ' + code)}</pre>"
    response = HTMLResponse(
        "<!doctype html><html lang='vi'><meta charset='utf-8'>"
        "<title>Liên kết Telegram · xT</title>" + body + "</html>",
        status_code=status,
        headers=_HEADERS,
    )
    response.delete_cookie(COOKIE, path="/desk/telegram")
    return response


class TelegramLinkPages:
    def __init__(
        self,
        rt: Runtime,
        settings: ConfirmSettings,
        verifier_for: Callable[[Any], StepUpVerifier] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.rt, self.settings, self.clock = rt, settings, clock
        self.sessions = PgConfirmationSessionStore(rt.uow)
        if verifier_for is None:
            jwks = build_jwks_cache(settings.step_up.idp)
            self.verifier_for: Callable[[Any], StepUpVerifier] = lambda store: StepUpVerifier(
                settings.step_up, jwks, store=store
            )
        else:
            self.verifier_for = verifier_for

    @property
    def redirect_uri(self) -> str:
        return f"{self.settings.desk_base_url.rstrip('/')}/desk/telegram/callback"

    def routes(self) -> list[Route]:
        return [
            Route("/desk/telegram/callback", self.callback, methods=["GET"]),
            Route("/desk/telegram/start/{state}", self.start, methods=["GET"]),
        ]

    def _in_tenant(self, tenant: TenantRef, fn: Callable[[], Any]) -> Any:
        actor = ModelActor(tenant, NIL, (WORKFLOW_ROLE,), client_azp="xt-telegram-link")
        with self.rt.uow.scope(actor):
            return self.rt.uow.run(fn)

    def begin(self, caller: Caller, request: Request) -> JSONResponse:
        """Called only after the composition's bearer/DPoP verifier resolved the caller."""
        if not caller.person.subject or not caller.person.active:
            return JSONResponse({"error": "access_denied"}, status_code=403, headers=_HEADERS)
        now = self.clock()
        pkce = new_pkce_pair()
        state = f"tl_{caller.tenant.id}.{secrets.token_urlsafe(24)}"
        cookie, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        self._in_tenant(
            caller.tenant,
            lambda: self.sessions.save(
                state,
                {
                    "purpose": "telegram-link",
                    "owner_id": str(caller.person.id),
                    "owner_subject": caller.person.subject,
                    "created_at": int(now.timestamp()),
                    "verifier": pkce.verifier,
                    "challenge": pkce.challenge,
                    "nonce": nonce,
                    "cookie": cookie,
                },
                now + SESSION_TTL,
            ),
        )
        return JSONResponse(
            {
                "authorize_url": (
                    f"{self.settings.desk_base_url.rstrip('/')}/desk/telegram/start/{state}"
                ),
                "expires_in": int(SESSION_TTL.total_seconds()),
            },
            headers=_HEADERS,
        )

    def _tenant_for_state(self, state: str) -> TenantRef | None:
        matched = _STATE.fullmatch(state)
        if matched is None:
            return None
        try:
            return tenant_by_id(self.rt.app_db, UUID(matched[1]))
        except ValueError:
            return None

    def start(self, request: Request) -> Response:
        """Set the browser cookie on Desk's origin, including when CLI initiated the flow."""
        state = str(request.path_params.get("state", ""))
        tenant = self._tenant_for_state(state)
        if tenant is None:
            return _page("Phiên liên kết không hợp lệ.", status=400)
        data = self._in_tenant(tenant, lambda: self.sessions.get(state))
        if (
            data is None
            or data.get("purpose") != "telegram-link"
            or int(data.get("created_at", 0)) + SESSION_TTL.total_seconds()
            <= self.clock().timestamp()
        ):
            return _page("Phiên liên kết đã hết hạn.", status=400)
        url = confirm_authorization_url(
            self.settings.step_up,
            self.settings.authorization_endpoint,
            self.redirect_uri,
            state=state,
            nonce=str(data["nonce"]),
            code_challenge=str(data["challenge"]),
        )
        response = RedirectResponse(url, status_code=302, headers=_HEADERS)
        response.set_cookie(
            COOKIE,
            str(data["cookie"]),
            max_age=int(SESSION_TTL.total_seconds()),
            path="/desk/telegram",
            secure=self.settings.secure_cookie,
            httponly=True,
            samesite="lax",
        )
        return response

    def _exchange(self, code: str, verifier: str) -> str:
        response = httpx.post(
            self.settings.token_endpoint,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri,
                "client_id": self.settings.step_up.client_id,
                "code_verifier": verifier,
            },
            timeout=self.settings.http_timeout_s,
        )
        if response.status_code != 200:
            raise PermissionError("OIDC code exchange failed")
        token = response.json().get("id_token")
        if not isinstance(token, str) or not token:
            raise PermissionError("OIDC response omitted ID token")
        return token

    def callback(self, request: Request) -> Response:
        state, code = request.query_params.get("state", ""), request.query_params.get("code", "")
        tenant = self._tenant_for_state(state)
        if not code or len(code) > 4096 or request.query_params.get("error"):
            return _page("Phiên liên kết không hợp lệ.", status=400)
        if tenant is None:
            return _page("Phiên liên kết không hợp lệ.", status=400)

        def consume() -> Any:
            data = self.sessions.get(state)
            if data is not None:
                self.sessions.delete(state)
            return data

        data = self._in_tenant(tenant, consume)
        cookie = request.cookies.get(COOKIE, "")
        if (
            not data
            or data.get("purpose") != "telegram-link"
            or not cookie
            or not hmac.compare_digest(str(data.get("cookie", "")), cookie)
            or int(data.get("created_at", 0)) + SESSION_TTL.total_seconds()
            <= self.clock().timestamp()
        ):
            return _page("Phiên liên kết hết hạn hoặc không thuộc trình duyệt này.", status=400)
        try:
            id_token = self._exchange(code, str(data["verifier"]))
        except (httpx.HTTPError, PermissionError, ValueError):
            return _page("Không lấy được bằng chứng đăng nhập. Hãy bắt đầu lại.", status=502)

        def verify_and_mint() -> str:
            self.verifier_for(self.rt.single_use).verify(
                id_token,
                ConfirmationContext(
                    state,
                    str(data["nonce"]),
                    int(data["created_at"]),
                    requester_subject="client:telegram-link",
                    confirmer_subject=str(data["owner_subject"]),
                ),
            )
            service_actor = ModelActor(tenant, NIL, (WORKFLOW_ROLE,))
            person = self.rt.store.person(service_actor, UUID(str(data["owner_id"])))
            if person is None or not person.active or person.subject != str(data["owner_subject"]):
                raise PermissionError("Link owner is no longer active")
            owner_actor = ModelActor(tenant, person.id, (), client_azp="xt-telegram-link")
            return PgNotificationRepository(self.rt.uow, owner_actor).mint_link_code()

        try:
            link_code = self._in_tenant(tenant, verify_and_mint)
        except (IdentityError, PermissionError, ValueError):
            return _page(
                "Bằng chứng xác thực không đạt. Hãy đăng nhập lại bằng tài khoản của bạn.",
                status=403,
            )
        except Exception:
            return _page("Không thể tạo mã liên kết. Hãy bắt đầu lại.", status=500)
        return _page(
            "Gửi lệnh dưới đây trong cuộc trò chuyện riêng với bot xT trong vòng 10 phút.",
            code=link_code,
        )
