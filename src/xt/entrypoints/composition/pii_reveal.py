# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Kênh NGƯỜI xem giá trị thật PII: tool app-only `pii_reveal_ticket` + `POST /pii/reveal`.

Hợp đồng với Desk: docs/integration-notes.md "Desk ↔ /pii/reveal" (thiết kế:
research/spike-pii-lane.md §3.7; quyết định "Tier PII của Desk chat").

1. `pii_reveal_ticket(tokens, pubkey_jwk)` (Bearer của người dùng như mọi tool; chỉ client trong
   `XT_PII_REVEAL_CLIENTS`): mọi token phải có trong vault của tenant; cấp vé 256 bit ngẫu nhiên,
   dùng một lần, sống `ttl`, gắn người được đại diện + tập token + khóa công khai P-256 của khung.
   Vé CHỈ ở `result._meta["xt/reveal"] = {ticket, url}`; envelope chỉ có số token + hạn.
2. `POST /pii/reveal` `{ticket, ts, sig}` (không cookie, không Authorization): Origin phải thuộc
   `XT_PII_REVEAL_ORIGINS` (CORS chỉ cho origin đó); lệch `ts` ≤ 30 s; chữ ký ECDSA P-256 SHA-256
   (P1363) trên `"{ticket}.{ts}"` bằng khóa đã gắn; vé đánh dấu đã dùng nguyên tử (một lần thử,
   kể cả khi chữ ký sai). Quyền xem: `basic` = người đang làm trong tenant; `sensitive` (hoặc
   không rõ mức) = policy IAM cho phép `pii.reveal` trên `xrn:<tenant>:<model>:<field>`.
   Response `Cache-Control: no-store`; mỗi lần xem ghi `xt_meta.pii_egress` (tier `human`,
   `person`, các field đã mở). Giá trị thật không bao giờ đi vào kết quả tool MCP.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from xt.adapters.db.integration import pii_ticket_tenant
from xt.application.tools import CallContext, ToolDispatcher, ToolError, ToolOutcome
from xt.application.tools.errors import access_denied, invalid_request
from xt.contracts.tools import CONTRACT_VERSION, ErrorCode, StrictValidator
from xt.contracts.tools.apps import PII_REVEAL_TICKET
from xt.domain.model.context import ModelActor
from xt.entrypoints.composition.runtime import NIL, Caller, Runtime
from xt.entrypoints.composition.security import ToolAccessGuard

log = logging.getLogger("xt.pii.reveal")
REVEAL_PATH = "/pii/reveal"
MAX_BODY = 4096
_NO_STORE = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


@dataclass(frozen=True, slots=True)
class RevealSettings:
    origin: str  # origin công khai của xT (= origin XT_DESK_URL = DESK_XT_ORIGIN)
    allowed_origins: frozenset[str]
    clients: frozenset[str]
    ttl_s: int = 60
    max_skew_s: int = 30

    @property
    def url(self) -> str:
        return f"{self.origin}{REVEAL_PATH}"


def _b64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _verify(jwk: Mapping[str, Any], message: bytes, sig_b64u: str) -> bool:
    try:
        raw = _b64u(sig_b64u)
        if len(raw) != 64:
            return False
        x = int.from_bytes(_b64u(str(jwk["x"])), "big")
        y = int.from_bytes(_b64u(str(jwk["y"])), "big")
        key = ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1()).public_key()
        der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
        key.verify(der, message, ec.ECDSA(hashes.SHA256()))
        return True
    except (InvalidSignature, ValueError, KeyError, TypeError):
        return False


def _valid_jwk(jwk: Mapping[str, Any]) -> bool:
    try:
        x = int.from_bytes(_b64u(str(jwk["x"])), "big")
        y = int.from_bytes(_b64u(str(jwk["y"])), "big")
        ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1()).public_key()
        return True
    except (ValueError, KeyError, TypeError):
        return False


class PiiReveal:
    def __init__(
        self,
        rt: Runtime,
        guard: ToolAccessGuard,
        settings: RevealSettings,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.rt, self.guard, self.settings, self.clock = rt, guard, settings, clock
        self._validator = StrictValidator(dict(PII_REVEAL_TICKET.input_schema))

    # ---- 1. tool app-only ---------------------------------------------------------------------
    def _error(self, d: ToolDispatcher, ctx: CallContext, e: ToolError) -> ToolOutcome:
        error = dict(d.services.pii.scrub_error(ctx, PII_REVEAL_TICKET.name, e.to_error()))
        envelope = {
            "ok": False,
            "untrusted": True,
            "error": error,
            "meta": {"contract_version": CONTRACT_VERSION, "trace_id": ctx.trace_id},
        }
        return ToolOutcome(True, envelope, PII_REVEAL_TICKET.name)

    def mint(
        self, d: ToolDispatcher, ctx: CallContext, args: Mapping[str, Any], dups: Sequence[str]
    ) -> ToolOutcome:
        try:
            if dups:
                raise invalid_request("The arguments JSON repeats an object key.", "duplicate_key")
            if next(iter(self._validator.iter_errors(dict(args))), None) is not None:
                raise invalid_request("Arguments do not match the tool input schema.", "schema")
            if ctx.actor.client_id not in self.settings.clients:
                raise access_denied(
                    "Kênh xem giá trị thật chỉ dành cho ứng dụng xT Desk.",
                    "client_not_allowed",
                    None,
                )
            caller = self.rt.caller(ctx)
            tokens = list(dict.fromkeys(str(t) for t in args["tokens"]))
            jwk = dict(args["pubkey_jwk"])
            if not _valid_jwk(jwk):
                raise invalid_request("Khóa công khai không hợp lệ (EC P-256).", "BAD_PUBKEY")
            known = self.rt.store.pii_lookup(caller.actor, tokens)
            unknown = [t for t in tokens if t not in known]
            if unknown:
                raise invalid_request(
                    "Có token PII không thuộc tổ chức này.", "PII_TOKEN_UNKNOWN", count=len(unknown)
                )
            ticket = secrets.token_urlsafe(32)
            self.rt.store.pii_ticket_put(
                caller.actor,
                ticket_hash=hashlib.sha256(ticket.encode()).digest(),
                person=caller.person.id,
                client_id=caller.client_id,
                tokens=tokens,
                pubkey={"kty": "EC", "crv": "P-256", "x": jwk["x"], "y": jwk["y"]},
                ttl_s=self.settings.ttl_s,
            )
            envelope = {
                "ok": True,
                "untrusted": True,
                "data": {"tokens": len(tokens), "expires_in_seconds": self.settings.ttl_s},
                "meta": {
                    "contract_version": CONTRACT_VERSION,
                    "trace_id": ctx.trace_id,
                    "as_of": ctx.now_iso,
                    "effective_level": "L0",
                },
            }
            return ToolOutcome(
                False,
                envelope,
                PII_REVEAL_TICKET.name,
                result_meta={"xt/reveal": {"ticket": ticket, "url": self.settings.url}},
            )
        except ToolError as e:
            return self._error(d, ctx, e)
        except Exception as e:
            log.error("pii_reveal_ticket failed trace_id=%s (%s)", ctx.trace_id, type(e).__name__)
            return self._error(
                d, ctx, ToolError(ErrorCode.INTERNAL_ERROR, "Unexpected server failure.")
            )

    # ---- 2. POST /pii/reveal ------------------------------------------------------------------
    def routes(self) -> list[Route]:
        return [Route(REVEAL_PATH, self.endpoint, methods=["POST", "OPTIONS"])]

    def _cors(self, origin: str) -> dict[str, str]:
        return {"Access-Control-Allow-Origin": origin, "Vary": "Origin"}

    def _deny(self, code: str, origin: str | None, status: int = 403) -> JSONResponse:
        headers = dict(_NO_STORE)
        if origin is not None:
            headers.update(self._cors(origin))
        return JSONResponse({"error": code}, status_code=status, headers=headers)

    async def endpoint(self, request: Request) -> Response:
        origin = request.headers.get("origin")
        allowed = origin if origin in self.settings.allowed_origins else None
        if request.method == "OPTIONS":
            if allowed is None:
                return self._deny("BAD_ORIGIN", None)
            return Response(
                status_code=204,
                headers={
                    **self._cors(allowed),
                    "Access-Control-Allow-Methods": "POST",
                    "Access-Control-Allow-Headers": "content-type",
                    "Access-Control-Max-Age": "600",
                    **_NO_STORE,
                },
            )
        if allowed is None:
            return self._deny("BAD_ORIGIN", None)
        if not request.headers.get("content-type", "").startswith("application/json"):
            return self._deny("BAD_REQUEST", allowed, 400)
        body = await request.body()
        if len(body) > MAX_BODY:
            return self._deny("BAD_REQUEST", allowed, 400)
        try:
            data = json.loads(body)
            ticket, ts, sig = data["ticket"], data["ts"], data["sig"]
            if not (
                isinstance(ticket, str)
                and 16 <= len(ticket) <= 128
                and type(ts) is int
                and isinstance(sig, str)
                and len(sig) <= 128
                and set(data) == {"ticket", "ts", "sig"}
            ):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            return self._deny("BAD_REQUEST", allowed, 400)
        status, payload = await run_in_threadpool(self._reveal, ticket, ts, sig)
        headers = {**_NO_STORE, **self._cors(allowed)}
        return JSONResponse(payload, status_code=status, headers=headers)

    def _reveal(self, ticket: str, ts: int, sig: str) -> tuple[int, dict[str, Any]]:
        if abs(self.clock() - ts) > self.settings.max_skew_s:
            return 403, {"error": "STALE_REQUEST"}
        h = hashlib.sha256(ticket.encode()).digest()
        tenant = pii_ticket_tenant(self.rt.app_db, h)
        if tenant is None:
            return 403, {"error": "TICKET_INVALID"}
        actor = ModelActor(tenant, NIL, (), client_azp="xt-pii-reveal")

        def work() -> tuple[int, dict[str, Any]]:
            row = self.rt.store.pii_ticket_lock(actor, h)
            if row is None:
                return 403, {"error": "TICKET_INVALID"}
            # Một lần thử: vé tiêu ngay cả khi chữ ký sai (chống dò).
            self.rt.store.pii_ticket_use(actor, h)
            if not _verify(row["pubkey"], f"{ticket}.{ts}".encode(), sig):
                return 403, {"error": "BAD_SIGNATURE"}
            person = self.rt.store.person(actor, row["person"])
            profile = self.rt.store.profile(actor)
            if person is None or not person.active or profile is None:
                return 403, {"error": "TICKET_INVALID"}
            caller = Caller(
                profile.alias,
                profile,
                person,
                str(row["client_id"]),
                False,
                0,
                (),
                ModelActor(tenant, person.id, tuple(person.roles)),
            )
            vault = self.rt.store.pii_lookup(actor, list(row["tokens"]))
            now = int(self.clock())
            values: dict[str, str] = {}
            denied: list[str] = []
            fields: set[str] = set()
            for tok in row["tokens"]:
                hit = vault.get(tok)
                if hit is None or not self._may_reveal(caller, hit, now):
                    denied.append(tok)
                    continue
                values[tok] = str(hit["value"])
                fields.add(f"{hit['field'] or 'unknown'}:reveal_human")
            self.rt.store.pii_egress(
                actor,
                trace_id=f"reveal:{h.hex()[:16]}",
                tool="pii_reveal",
                client_class="human",
                mode="full",
                fields=sorted(fields) or ["none"],
                person=person.id,
            )
            return 200, {"values": values, "denied": denied}

        try:
            with self.rt.uow.scope(actor):
                return self.rt.uow.run(work)
        except Exception as e:
            log.error("pii reveal failed (%s)", type(e).__name__)
            return 500, {"error": "INTERNAL"}

    def _may_reveal(self, caller: Caller, hit: Mapping[str, Any], now: int) -> bool:
        if hit.get("level") == "basic":
            return True
        field = str(hit.get("field") or "")
        model, _, name = field.partition(".") if "." in field else ("pii", "", "unknown")
        xrn = f"xrn:{caller.alias}:{model}:{name or 'unknown'}"  # tài nguyên mức field
        return self.guard.allows(caller, caller.person, "pii.reveal", xrn, now)
