# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Kênh NGƯỜI xem giá trị thật PII: tool app-only `pii_reveal_ticket` + `POST /pii/reveal`.

Hợp đồng với Desk: docs/integration-notes.md "Desk ↔ /pii/reveal" (thiết kế:
research/spike-pii-lane.md §3.7; quyết định "Tier PII của Desk chat").

1. `pii_reveal_ticket(tokens, pubkey_jwk)` (Bearer của người dùng như mọi tool; chỉ client trong
   `XT_PII_REVEAL_CLIENTS`; quota `XT_PII_REVEAL_TICKETS_PER_MINUTE` vé/người/phút): chỉ token mà
   chính (người, client) này đã nhận trong kết quả tool (`pii_token_seen`); token khác vẫn vào vé
   nhưng luôn `denied`. Vé 256 bit, dùng một lần, sống `ttl`, gắn người + tập token + khóa P-256
   MỚI của khung (khóa dùng lại bị từ chối). Vé CHỈ ở `result._meta["xt/reveal"] = {ticket, url}`.
2. `POST /pii/reveal` `{ticket, ts, sig}` (không cookie, không Authorization): Origin phải thuộc
   `XT_PII_REVEAL_ORIGINS` (CORS chỉ cho origin đó); lệch `ts` ≤ 30 s; chữ ký ECDSA P-256 SHA-256
   (P1363) trên `"{ticket}.{ts}"` bằng khóa đã gắn; vé đánh dấu đã dùng nguyên tử (một lần thử,
   kể cả khi chữ ký sai). Quyền xem, xét lại lúc đổi vé: người xem phải đọc được NGAY BÂY GIỜ ít
   nhất một bản ghi nguồn của token (IAM `record.read`/`person.read` + RLS), và với `sensitive`
   (hoặc không rõ mức) còn cần `pii.reveal` trên `xrn:<tenant>:<model>:<field>`.
   Response `Cache-Control: no-store`. Mọi lần xin vé và đổi vé (kể cả thất bại sau khi suy được
   tenant) ghi `xt_meta.pii_egress`: người xem, kết quả, token, bản ghi, token bị từ chối, tier;
   không bao giờ giá trị. Giá trị thật không bao giờ đi vào kết quả tool MCP.
3. DPoP (RFC 9449, docs/decisions.md "DPoP cho Desk trước v1.0"): khi access token của phiên gắn
   khóa (`cnf.jkt` đã kiểm ở /mcp), vé gắn `jkt` đó và KHÔNG nhận `pubkey_jwk`. `POST /pii/reveal`
   khi đó là `{ticket}` + header `DPoP` (htm POST, htu = URL /pii/reveal, `ath` = base64url(SHA-256
   (vé)), jti dùng một lần) ký bằng CHÍNH khóa DPoP đó: token bị trích khỏi trình duyệt không đổi
   được vé. `XT_DPOP_REQUIRED=true`: không cấp/đổi vé khung (khóa tùy ý) nữa.
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
from xt.adapters.identity import DpopError, DpopSettings, DpopVerifier
from xt.application.tools import CallContext, ToolDispatcher, ToolError, ToolOutcome
from xt.application.tools.errors import access_denied, invalid_request
from xt.contracts.tools import CONTRACT_VERSION, ErrorCode, StrictValidator
from xt.contracts.tools.apps import PII_REVEAL_TICKET
from xt.domain.identity import IdentityProviderUnavailableError
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
    tickets_per_minute: int  # XT_PII_REVEAL_TICKETS_PER_MINUTE: quota vé mỗi người (PIIR-04)
    ttl_s: int = 60
    max_skew_s: int = 30
    dpop_required: bool = False  # XT_DPOP_REQUIRED: chỉ vé gắn khóa DPoP

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


def _canonical_coord(v: Any) -> str | None:
    """Toạ độ P-256 base64url chuẩn tắc (32 byte, không đệm, bit thừa = 0) hoặc None."""
    try:
        raw = _b64u(str(v))
    except (ValueError, TypeError):
        return None
    canon = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return canon if len(raw) == 32 and canon == v else None


def canonical_jwk(jwk: Mapping[str, Any]) -> dict[str, str] | None:
    """JWK công khai P-256 hợp lệ ở dạng chuẩn tắc, hoặc None (R2-02)."""
    x, y = _canonical_coord(jwk.get("x")), _canonical_coord(jwk.get("y"))
    if x is None or y is None:
        return None
    try:
        ec.EllipticCurvePublicNumbers(
            int.from_bytes(_b64u(x), "big"), int.from_bytes(_b64u(y), "big"), ec.SECP256R1()
        ).public_key()
    except ValueError:
        return None
    return {"crv": "P-256", "kty": "EC", "x": x, "y": y}


def jwk_thumbprint(jwk: Mapping[str, str]) -> bytes:
    """RFC 7638: SHA-256 của JSON thành viên bắt buộc, sắp theo tên, không khoảng trắng."""
    members = {k: jwk[k] for k in ("crv", "kty", "x", "y")}
    return hashlib.sha256(
        json.dumps(members, separators=(",", ":"), sort_keys=True).encode()
    ).digest()


class PiiReveal:
    def __init__(
        self,
        rt: Runtime,
        guard: ToolAccessGuard,
        settings: RevealSettings,
        clock: Callable[[], float] = time.time,
        dpop: DpopVerifier | None = None,
    ) -> None:
        self.rt, self.guard, self.settings, self.clock = rt, guard, settings, clock
        self.dpop = dpop or DpopVerifier(DpopSettings(required=settings.dpop_required), clock=clock)
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
            jkt = ctx.actor.dpop_jkt
            raw_jwk = args.get("pubkey_jwk")
            point: dict[str, str]
            thumb: bytes | None
            if jkt is not None:
                if raw_jwk is not None:  # vé gắn khóa DPoP của phiên, không nhận khóa tùy ý
                    raise invalid_request(
                        "Phiên gắn DPoP: vé dùng khóa DPoP của phiên, không gửi pubkey_jwk.",
                        "DPOP_BOUND",
                    )
                point, thumb = {"dpop_jkt": jkt}, None
            else:
                if self.settings.dpop_required:
                    raise access_denied(
                        "Kênh xem giá trị thật cần phiên Desk gắn khóa DPoP.",
                        "DPOP_REQUIRED",
                        None,
                    )
                jwk = canonical_jwk(dict(raw_jwk)) if isinstance(raw_jwk, Mapping) else None
                if jwk is None:
                    raise invalid_request("Khóa công khai không hợp lệ (EC P-256).", "BAD_PUBKEY")
                point = {"kty": "EC", "crv": "P-256", "x": jwk["x"], "y": jwk["y"]}
                thumb = jwk_thumbprint(jwk)
            store, person = self.rt.store, caller.person.id
            # R2-01: đếm quota + INSERT vé tuần tự theo (tenant, người) trong cùng transaction.
            store.pii_lock_person(caller.actor, person)
            if (
                store.pii_tickets_since(caller.actor, person, 60)
                >= self.settings.tickets_per_minute
            ):
                self._audit_mint(ctx, caller, "rate_limited", [], tokens)
                raise ToolError(
                    ErrorCode.RATE_LIMITED,
                    "Bạn đã xin quá nhiều lượt xem dữ liệu cá nhân trong một phút.",
                    details={"reason": "pii_reveal_rate", "retry_after_seconds": 60},
                )
            # PIIR-02: chỉ token mà CHÍNH phiên (người + client) này đã nhận trong kết quả tool;
            # token lạ/không thấy vẫn ghi vào vé để trả `denied` (không làm hỏng cả lô, PIIR-06).
            known = store.pii_lookup(caller.actor, tokens)
            seen = store.pii_seen(caller.actor, tokens, person, caller.client_id)
            usable = [t for t in tokens if t in known and t in seen]
            if not usable:
                self._audit_mint(ctx, caller, "unknown_tokens", [], tokens)
                raise invalid_request(
                    "Không có token PII nào bạn đã nhận trong phiên này.",
                    "PII_TOKEN_UNKNOWN",
                    count=len(tokens),
                )
            ticket = secrets.token_urlsafe(32)
            ok = store.pii_ticket_put(
                caller.actor,
                ticket_hash=hashlib.sha256(ticket.encode()).digest(),
                person=person,
                client_id=caller.client_id,
                tokens=tokens,
                pubkey=point,
                ttl_s=self.settings.ttl_s,
                key_thumbprint=thumb,
            )
            if not ok:  # PIIR-07: mỗi lần xem phải là khóa mới của khung Desk
                self._audit_mint(ctx, caller, "key_reused", [], tokens)
                raise invalid_request("Khóa của khung đã được dùng; tạo khóa mới.", "KEY_REUSED")
            self._audit_mint(
                ctx, caller, "ticket_issued", usable, [t for t in tokens if t not in usable]
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

    def _audit_mint(
        self,
        ctx: CallContext,
        caller: Caller,
        outcome: str,
        tokens: Sequence[str],
        denied: Sequence[str],
    ) -> None:
        self.rt.store.pii_egress(
            caller.actor,
            trace_id=ctx.trace_id,
            tool=PII_REVEAL_TICKET.name,
            client_class=ctx.client_class.value,
            mode="masked",  # chỉ token, không giá trị
            fields=["ticket"],
            person=caller.person.id,
            outcome=outcome,
            tokens=list(tokens),
            denied=list(denied),
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
                    "Access-Control-Allow-Headers": "content-type, dpop",
                    "Access-Control-Max-Age": "600",
                    **_NO_STORE,
                },
            )
        if allowed is None:
            log.warning("pii reveal refused: bad origin")  # không vé, không origin thô
            return self._deny("BAD_ORIGIN", None)
        if not request.headers.get("content-type", "").startswith("application/json"):
            return self._deny("BAD_REQUEST", allowed, 400)
        declared = request.headers.get("content-length", "")
        if not declared.isdigit() or int(declared) > MAX_BODY:  # PIIR-08: trước khi đọc body
            return self._deny("BAD_REQUEST", allowed, 400)
        chunks: list[bytes] = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_BODY:
                return self._deny("BAD_REQUEST", allowed, 400)
            chunks.append(chunk)
        body = b"".join(chunks)
        proofs = tuple(request.headers.getlist("dpop"))
        try:
            data = json.loads(body)
            if not isinstance(data, dict):
                raise ValueError
            ticket = data["ticket"]
            if not (isinstance(ticket, str) and 16 <= len(ticket) <= 128):
                raise ValueError
            ts: int | None = None
            sig: str | None = None
            if set(data) == {"ticket"}:
                if not proofs:  # dạng DPoP: proof ở header
                    raise ValueError
            elif set(data) == {"ticket", "ts", "sig"}:
                ts, sig = data["ts"], data["sig"]
                if not (type(ts) is int and isinstance(sig, str) and len(sig) <= 128):
                    raise ValueError
            else:
                raise ValueError
        except (ValueError, KeyError, TypeError):
            return self._deny("BAD_REQUEST", allowed, 400)
        status, payload = await run_in_threadpool(self._reveal, ticket, ts, sig, proofs)
        headers = {**_NO_STORE, **self._cors(allowed)}
        if status == 503:
            headers["Retry-After"] = "5"
        return JSONResponse(payload, status_code=status, headers=headers)

    def _proof_check(
        self,
        row: Mapping[str, Any],
        ticket: str,
        ts: int | None,
        sig: str | None,
        proofs: Sequence[str],
    ) -> str | None:
        """Mã lỗi hoặc None nếu request chứng minh được khóa gắn với vé. `STALE_REQUEST` không
        tiêu vé (khung ký lại trong TTL); mọi lỗi khác tiêu vé (một lần thử, chống dò)."""
        pub = row["pubkey"]
        jkt = pub.get("dpop_jkt") if isinstance(pub, Mapping) else None
        if jkt is not None:
            if sig is not None or not proofs:
                return "DPOP_REQUIRED"
            try:
                self.dpop.verify(
                    proofs,
                    htm="POST",
                    htu={self.settings.url},
                    bound_to=ticket,
                    expected_jkt=str(jkt),
                )
            except DpopError as e:
                if e.reason in ("dpop_iat_stale", "dpop_iat_future"):
                    return "STALE_REQUEST"
                return "BAD_DPOP_PROOF"
            except IdentityProviderUnavailableError:
                return "TEMPORARILY_UNAVAILABLE"  # store jti lỗi: 503, vé KHÔNG bị tiêu
            return None
        if self.settings.dpop_required:  # vé khung cấp trước khi bật cờ
            return "TICKET_INVALID"
        if ts is None or sig is None:
            return "BAD_SIGNATURE"
        if abs(self.clock() - ts) > self.settings.max_skew_s:
            return "STALE_REQUEST"
        if not _verify(pub, f"{ticket}.{ts}".encode(), sig):
            return "BAD_SIGNATURE"
        return None

    def _reveal(
        self,
        ticket: str,
        ts: int | None,
        sig: str | None,
        proofs: Sequence[str] = (),
    ) -> tuple[int, dict[str, Any]]:
        h = hashlib.sha256(ticket.encode()).digest()
        tenant = pii_ticket_tenant(self.rt.app_db, h)
        if tenant is None:
            # Không suy được tenant (vé lạ/đã dùng/hết hạn): chỉ log cấu trúc, không ghi vé.
            log.warning("pii reveal refused: ticket invalid")
            return 403, {"error": "TICKET_INVALID"}
        actor = ModelActor(tenant, NIL, (), client_azp="xt-pii-reveal")
        trace = f"reveal:{h.hex()[:16]}"

        def audit(
            outcome: str,
            person: Any = None,
            *,
            fields: Sequence[str] = (),
            tokens: Sequence[str] = (),
            records: Sequence[str] = (),
            denied: Sequence[str] = (),
        ) -> None:
            self.rt.store.pii_egress(
                actor,
                trace_id=trace,
                tool="pii_reveal",
                client_class="human",
                mode="full" if tokens else "omitted",
                fields=list(fields) or ["none"],
                person=person,
                outcome=outcome,
                tokens=list(tokens),
                records=list(records),
                denied=list(denied),
            )

        def work() -> tuple[int, dict[str, Any]]:
            row = self.rt.store.pii_ticket_lock(actor, h)
            if row is None:
                return 403, {"error": "TICKET_INVALID"}
            failure = self._proof_check(row, ticket, ts, sig, proofs)
            if failure == "TEMPORARILY_UNAVAILABLE":
                log.warning("pii reveal: DPoP replay store unavailable")
                return 503, {"error": failure}
            if failure == "STALE_REQUEST":
                # STALE không tiêu vé (khung Desk có thể ký lại trong TTL) nhưng có dấu vết.
                audit("stale", row["person"], denied=list(row["tokens"]))
                return 403, {"error": failure}
            # Một lần thử: vé tiêu ngay cả khi chữ ký/proof sai (chống dò).
            self.rt.store.pii_ticket_use(actor, h)
            if failure is not None:
                audit(failure.lower(), row["person"], denied=list(row["tokens"]))
                return 403, {"error": failure}
            person = self.rt.store.person(actor, row["person"])
            profile = self.rt.store.profile(actor)
            if person is None or not person.active or profile is None:
                audit("viewer_inactive", row["person"], denied=list(row["tokens"]))
                return 403, {"error": "TICKET_INVALID"}
            client_id = str(row["client_id"])
            viewer = Caller(
                profile.alias,
                profile,
                person,
                client_id,
                False,
                0,
                (),
                ModelActor(
                    tenant,
                    person.id,
                    tuple(person.roles),
                    department_ids=(person.unit,) if person.unit else (),
                    client_azp=client_id,
                ),
            )
            tokens = list(row["tokens"])
            vault = self.rt.store.pii_lookup(actor, tokens)
            seen = self.rt.store.pii_seen(actor, tokens, person.id, client_id)
            now = int(self.clock())
            values: dict[str, str] = {}
            denied: list[str] = []
            fields: set[str] = set()
            records: set[str] = set()
            for tok in tokens:
                hit = vault.get(tok)
                src = self._readable_source(viewer, tok, now) if hit and tok in seen else None
                if hit is None or src is None or not self._may_reveal(viewer, hit, src, now):
                    denied.append(tok)
                    continue
                values[tok] = str(hit["value"])
                fields.add(f"{src['field']}:reveal_human")
                records.add(str(src["ref"]))
            audit(
                "ok" if values else "denied",
                person.id,
                fields=sorted(fields),
                tokens=list(values),
                records=sorted(records),
                denied=denied,
            )
            return 200, {"values": values, "denied": denied}

        try:
            with self.rt.uow.scope(actor):
                return self.rt.uow.run(work)
        except Exception as e:
            log.error("pii reveal failed (%s)", type(e).__name__)
            return 500, {"error": "INTERNAL"}

    def _readable_source(self, viewer: Caller, token: str, now: int) -> dict[str, Any] | None:
        """PIIR-02: token không phải chìa khóa. Nguồn (bản ghi + field) mà người xem đọc được NGAY
        BÂY GIỜ (IAM + RLS); nhạy nhất trước. Không nguồn nào đọc được -> từ chối."""
        for src in self.rt.store.pii_sources(viewer.actor, token):
            if self.guard.can_read(viewer, str(src["ref"]), now):
                return src
        return None

    def _may_reveal(
        self, viewer: Caller, hit: Mapping[str, Any], src: Mapping[str, Any], now: int
    ) -> bool:
        level = "sensitive" if "sensitive" in (hit.get("level"), src.get("level")) else "basic"
        if hit.get("level") is None:
            level = "sensitive"
        if level == "basic":
            return True
        field = str(src.get("field") or hit.get("field") or "")
        model, _, name = field.partition(".") if "." in field else ("pii", "", "unknown")
        xrn = f"xrn:{viewer.alias}:{model}:{name or 'unknown'}"  # tài nguyên mức field
        return self.guard.allows(viewer, viewer.person, "pii.reveal", xrn, now)
