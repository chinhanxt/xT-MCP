# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Mount the Telegram display channel in xt-api and authenticated self-service linking."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

import anyio.to_thread
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from xt.adapters.db.notify import PgNotificationRepository, PgTelegramDirectory
from xt.adapters.identity import extract_bearer
from xt.application.tools.context import Actor
from xt.domain.identity import IdentityError
from xt.entrypoints.composition import Composition
from xt.entrypoints.composition.runtime import CallerError
from xt.entrypoints.notify_runtime import telegram_sender
from xt.entrypoints.telegram_http import TelegramWebhook


class TelegramLinkCodes:
    """No person/tenant argument: the access token always identifies the code owner."""

    def __init__(self, prepared: Any, env: Mapping[str, str]) -> None:
        self.prepared = prepared
        self.clients = frozenset(
            env.get("TELEGRAM_LINK_CLIENTS", "xt-cli,xt-desk-agent").replace(",", " ").split()
        )

    def mint(self, request: Request) -> JSONResponse:
        p = self.prepared
        token = extract_bearer(request.headers.get("authorization"))
        if token is None:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        try:
            principal = p.verifier.verify(token)
            if principal.client_id not in self.clients or principal.tenant is None:
                return JSONResponse({"error": "access_denied"}, status_code=403)
            if principal.dpop_jkt is not None or (
                p.dpop is not None and p.dpop.required_for(principal.client_id)
            ):
                if p.dpop is None:
                    return JSONResponse({"error": "unauthenticated"}, status_code=401)
                jkt = p.dpop.verify(
                    request.headers.getlist("dpop"),
                    htm="POST",
                    htu=p.dpop.candidates(request.url.path),
                    bound_to=token,
                    expected_jkt=principal.dpop_jkt,
                )
                principal = replace(principal, dpop_jkt=jkt)
            actor = Actor.from_principal(principal, principal.tenant)
            rt = p.services.runtime
            caller = rt.resolve(actor, uuid.uuid4().hex)
            code = rt.run_as(
                caller,
                actor,
                lambda: PgNotificationRepository(rt.uow, caller.actor).mint_link_code(),
            )
        except (IdentityError, CallerError, PermissionError):
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        return JSONResponse(
            {"code": code, "expires_in": 600, "command": f"/link {code}"},
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )

    async def endpoint(self, request: Request) -> JSONResponse:
        return await anyio.to_thread.run_sync(self.mint, request)


def with_notifications(prepared: Any, env: Mapping[str, str], app: Starlette) -> Starlette:
    if not isinstance(prepared.services, Composition):
        return app
    sender = telegram_sender(env)
    if sender is None:
        return app
    secret = env.get("TELEGRAM_WEBHOOK_SECRET", "").strip()
    webhook = TelegramWebhook(
        secret_token=secret,
        directory=PgTelegramDirectory(prepared.services.runtime.app_db),
        sender=sender,
    )
    codes = TelegramLinkCodes(prepared, env)
    # Mount on the existing app to preserve its lifespan, mounts and exception handlers.
    app.router.routes[:0] = [
        *webhook.routes(),
        Route("/telegram/link-code", codes.endpoint, methods=["POST"]),
    ]
    return app
