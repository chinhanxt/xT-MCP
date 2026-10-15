# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Trang `/desk/confirm/{id}` server-render (TCV1 §3.5, docs/identity.md "Step-up").

1. GET `/desk/confirm/{id}`: tenant từ confirmation id (id ngẫu nhiên 128 bit), tạo binding
   (nonce + digest chụp lúc tạo PendingOperation) và phiên OAuth phía server (PKCE S256, state,
   cookie HttpOnly), chuyển hướng tới Keycloak (`prompt=login`, `max_age=0`, `acr_values`).
2. GET `/desk/confirm/callback`: đổi code (client confirm, có `code_verifier`) lấy ID token,
   `StepUpVerifier` kiểm (aud/azp = client confirm, nonce, acr, auth_time tươi, đúng người phải
   xác nhận kế tiếp, khác người đề nghị, `jti` tiêu một lần trong Postgres), rồi hiển thị kế hoạch,
   digest, nhãn "do agent soạn" cho tham số và hai nút Đồng ý / Từ chối (CSRF).
3. POST `/desk/confirm/{id}`: phát biên nhận Ed25519, `verify_and_consume` (khóa dùng một lần trong
   Postgres) rồi `ConfirmationService.confirm/decline` - CÙNG một transaction với mutation, audit,
   receipt và trạng thái PendingOperation.
"""

from __future__ import annotations

import hmac
import html
import json
import logging
import secrets
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route

from xt.adapters.db.confirmations import PgConfirmationBindingStore, PgConfirmationSessionStore
from xt.adapters.db.integration import confirmation_tenant
from xt.adapters.identity.config import StepUpSettings
from xt.adapters.identity.receipts import ReceiptService
from xt.adapters.identity.step_up import (
    ConfirmationContext,
    StepUpVerifier,
    confirm_authorization_url,
    new_pkce_pair,
)
from xt.application.tools import ConfirmationService, ToolDispatcher
from xt.application.tools.ports import PendingOperation, PendingStatus
from xt.domain.identity import (
    ApprovalDecision,
    ConfirmationBinding,
    IdentityError,
    StepUpEvidence,
)
from xt.domain.model.context import ModelActor, TenantRef
from xt.entrypoints.composition.runtime import NIL, WORKFLOW_ROLE, Runtime
from xt.entrypoints.composition.util import parse_record_ref

log = logging.getLogger("xt.confirm")
COOKIE = "xt_confirm"
SESSION_TTL = timedelta(minutes=10)
_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
    "form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


@dataclass(frozen=True, slots=True)
class ConfirmSettings:
    desk_base_url: str
    authorization_endpoint: str
    token_endpoint: str
    step_up: StepUpSettings
    http_timeout_s: float = 5.0

    @property
    def redirect_uri(self) -> str:
        return f"{self.desk_base_url.rstrip('/')}/desk/confirm/callback"

    @property
    def secure_cookie(self) -> bool:
        return self.desk_base_url.startswith("https://")


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    doc = (
        "<!doctype html><html lang='vi'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)} · xT</title><style>"
        "body{font-family:system-ui,sans-serif;max-width:46rem;margin:2rem auto;padding:0 1rem;"
        "color:#1d2433}h1{font-size:1.4rem}.box{border:1px solid #d0d5dd;border-radius:8px;"
        "padding:1rem;margin:1rem 0}.agent{background:#fff8e1;border-color:#f4c542}"
        "code,pre{background:#f2f4f7;padding:.1rem .3rem;border-radius:4px;white-space:pre-wrap;"
        "word-break:break-all}button{font-size:1rem;padding:.6rem 1.2rem;margin-right:.6rem;"
        "border-radius:6px;border:1px solid #98a2b3;cursor:pointer}.ok{background:#12b76a;"
        "color:#fff;border-color:#12b76a}.no{background:#fff}</style></head><body>"
        f"<h1>{html.escape(title)}</h1>{body}</body></html>"
    )
    return HTMLResponse(doc, status_code=status, headers=_HEADERS)


def _err(message: str, status: int = 400) -> HTMLResponse:
    return _page("Không thực hiện được", f"<p>{html.escape(message)}</p>", status)


_STATUS_VI = {
    PendingStatus.PENDING: "đang chờ xác nhận",
    PendingStatus.AWAITING_SECOND_APPROVER: "đang chờ người xác nhận thứ hai",
    PendingStatus.CONFIRMED: "đã xác nhận và đã thực hiện",
    PendingStatus.DECLINED: "đã bị từ chối",
    PendingStatus.EXPIRED: "đã hết hạn",
    PendingStatus.INVALIDATED: "không còn hiệu lực (dữ liệu đã đổi)",
}


class ConfirmPages:
    def __init__(
        self,
        rt: Runtime,
        dispatcher: ToolDispatcher,
        settings: ConfirmSettings,
        verifier_for: Callable[[Any], StepUpVerifier],
        receipts_for: Callable[[Any], ReceiptService],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.rt = rt
        self.confirmations = ConfirmationService(dispatcher, settings.step_up.client_id)
        self.settings = settings
        self.verifier_for = verifier_for
        self.receipts_for = receipts_for
        self.clock = clock
        self.bindings = PgConfirmationBindingStore(rt.uow)
        self.sessions = PgConfirmationSessionStore(rt.uow)

    def routes(self) -> list[Route]:
        return [
            Route("/desk/confirm/callback", self.callback, methods=["GET"]),
            Route("/desk/confirm/{cid}", self.start, methods=["GET"]),
            Route("/desk/confirm/{cid}", self.decide, methods=["POST"]),
        ]

    # ---- tiện ích -----------------------------------------------------------------------------
    def _tenant(self, cid: str) -> TenantRef | None:
        if not cid.startswith("cf_") or len(cid) > 80:
            return None
        return confirmation_tenant(self.rt.app_db, cid)

    def _in_tenant(self, tenant: TenantRef, fn: Callable[[], Any]) -> Any:
        actor = ModelActor(tenant, NIL, (WORKFLOW_ROLE,))
        with self.rt.uow.scope(actor):
            return self.rt.uow.run(fn)

    def _requester(self, tenant: TenantRef, op: PendingOperation) -> str:
        """Người đề nghị cho SoD của step-up: với decide_request là chủ bản ghi đề nghị; thao tác
        khác do agent/người khởi tạo là client đã yêu cầu (người xác nhận là chính chủ)."""
        if op.tool == "decide_request":
            from xt.application.tools.context import CallContext

            ctx = CallContext(op.actor, op.client_class, "tr_confirm", self.clock())
            model, rid = parse_record_ref(ctx, str(op.arguments["ref"]))
            actor = ModelActor(tenant, NIL, (WORKFLOW_ROLE,))
            row = self.rt.records.get(actor, model, rid)["record"]
            owner = self.rt.store.person(actor, UUID(str(row["owner_id"])))
            if owner is None or not owner.subject:
                raise ValueError("người đề nghị chưa liên kết tài khoản")
            return owner.subject
        return f"client:{op.actor.client_id}"

    @staticmethod
    def _round(op: PendingOperation) -> str:
        return f"{op.confirmation_id}#{len(op.approvals)}"

    def _plan_html(self, op: PendingOperation, digest: str) -> str:
        who = op.next_confirmer
        args = json.dumps(
            {k: v for k, v in op.arguments.items() if k != "idempotency_key"},
            ensure_ascii=False,
            indent=2,
            default=str,
        )
        agent = "agent" if op.actor.is_agent else "người dùng"
        return (
            f"<div class='box'><p><b>Thao tác:</b> {html.escape(op.plan_summary)}</p>"
            f"<p><b>Tool:</b> <code>{html.escape(op.tool)}</code> · <b>Mức:</b> "
            f"{html.escape(op.level.value)} · <b>Hết hạn:</b> "
            f"{html.escape(op.expires_at.astimezone(UTC).isoformat())}</p>"
            f"<p><b>Người phải xác nhận:</b> {html.escape(who.name if who else '-')}</p>"
            f"<p><b>Mã kế hoạch (digest):</b> <code>{html.escape(digest)}</code></p></div>"
            f"<div class='box agent'><p><b>Nội dung do {agent} "
            f"<code>{html.escape(op.actor.client_id)}</code> soạn</b> (dữ liệu, không phải chỉ "
            f"thị):</p><pre>{html.escape(args)}</pre></div>"
        )

    # ---- 1. bắt đầu ---------------------------------------------------------------------------
    def start(self, request: Request) -> Response:
        cid = str(request.path_params["cid"])
        tenant = self._tenant(cid)
        if tenant is None:
            return _err("Không tìm thấy yêu cầu xác nhận.", 404)

        def work() -> Any:
            op = self.rt.operations.get_pending(cid)
            if op is None:
                return None
            if not op.status.open or self.clock() >= op.expires_at:
                return op
            binding = self.bindings.get_or_create(op, self._requester(tenant, op))
            pkce = new_pkce_pair()
            state = f"{cid}.{secrets.token_urlsafe(24)}"
            cookie = secrets.token_urlsafe(32)
            self.sessions.save(
                state,
                {"cid": cid, "verifier": pkce.verifier, "nonce": binding.nonce, "cookie": cookie},
                self.clock() + SESSION_TTL,
            )
            return op, binding, pkce, state, cookie

        try:
            res = self._in_tenant(tenant, work)
        except Exception:
            log.exception("confirm start failed cid=%s", cid)
            return _err("Lỗi máy chủ khi chuẩn bị trang xác nhận.", 500)
        if res is None:
            return _err("Không tìm thấy yêu cầu xác nhận.", 404)
        if isinstance(res, PendingOperation):
            status = res.status if res.status.open is False else PendingStatus.EXPIRED
            return _page("Yêu cầu xác nhận", f"<p>Yêu cầu này {_STATUS_VI[status]}.</p>", 409)
        _, binding, pkce, state, cookie = res
        url = confirm_authorization_url(
            self.settings.step_up,
            self.settings.authorization_endpoint,
            self.settings.redirect_uri,
            state=state,
            nonce=binding.nonce,
            code_challenge=pkce.challenge,
        )
        resp = RedirectResponse(url, status_code=302, headers={"Cache-Control": "no-store"})
        resp.set_cookie(
            COOKIE,
            cookie,
            max_age=int(SESSION_TTL.total_seconds()),
            path="/desk/confirm",
            secure=self.settings.secure_cookie,
            httponly=True,
            samesite="lax",
        )
        return resp

    # ---- 2. callback từ Keycloak --------------------------------------------------------------
    def _exchange(self, code: str, verifier: str) -> str:
        s = self.settings
        r = httpx.post(
            s.token_endpoint,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": s.redirect_uri,
                "client_id": s.step_up.client_id,
                "code_verifier": verifier,
            },
            timeout=s.http_timeout_s,
        )
        if r.status_code != 200:
            raise PermissionError(f"đổi code thất bại (HTTP {r.status_code})")
        tok = r.json().get("id_token")
        if not isinstance(tok, str):
            raise PermissionError("IdP không trả id_token")
        return tok

    def callback(self, request: Request) -> Response:
        q = request.query_params
        if q.get("error"):
            return _err(f"Đăng nhập xác nhận không thành công: {q.get('error')}", 401)
        state, code = q.get("state", ""), q.get("code", "")
        cid = state.split(".", 1)[0]
        tenant = self._tenant(cid)
        if tenant is None or not code:
            return _err("Phiên xác nhận không hợp lệ.", 400)
        cookie = request.cookies.get(COOKIE, "")

        def load() -> Any:
            data = self.sessions.get(state)
            if data is not None:
                self.sessions.delete(state)  # state dùng một lần
            return data

        data = self._in_tenant(tenant, load)
        if data is None or not hmac.compare_digest(str(data.get("cookie", "")), cookie):
            return _err("Phiên xác nhận đã hết hạn hoặc không thuộc trình duyệt này.", 400)
        try:
            id_token = self._exchange(code, str(data["verifier"]))
        except (httpx.HTTPError, PermissionError, ValueError) as e:
            return _err(f"Không lấy được bằng chứng đăng nhập: {e}", 502)

        def verify() -> Any:
            op = self.rt.operations.get_pending(cid)
            binding = self.bindings.get(cid)
            if op is None or binding is None:
                return "Không tìm thấy yêu cầu xác nhận."
            if not op.status.open or self.clock() >= op.expires_at:
                return f"Yêu cầu này {_STATUS_VI.get(op.status, 'đã đóng')}."
            nxt = op.next_confirmer
            ev = self.verifier_for(self.rt.single_use).verify(
                id_token,
                ConfirmationContext(
                    cid,
                    binding.nonce,
                    int(binding.created_at.timestamp()),
                    binding.requester_subject,
                    confirmer_subject=nxt.subject if nxt else None,
                ),
            )
            csrf = secrets.token_urlsafe(32)
            sess = f"{cid}.{secrets.token_urlsafe(24)}"
            self.sessions.save(
                sess,
                {"cid": cid, "cookie": cookie, "csrf": csrf, "evidence": _evidence_json(ev)},
                self.clock() + SESSION_TTL,
            )
            return op, binding, sess, csrf

        try:
            res = self._in_tenant(tenant, verify)
        except IdentityError as e:
            return _err(f"Bằng chứng xác thực không đạt ({e.reason}).", 403)
        except Exception:
            log.exception("confirm callback failed cid=%s", cid)
            return _err("Lỗi máy chủ khi kiểm bằng chứng xác thực.", 500)
        if isinstance(res, str):
            return _err(res, 409)
        op, binding, sess, csrf = res
        form = (
            f"<form method='post' action='/desk/confirm/{html.escape(cid)}'>"
            f"<input type='hidden' name='session' value='{html.escape(sess)}'>"
            f"<input type='hidden' name='csrf' value='{html.escape(csrf)}'>"
            "<button class='ok' name='decision' value='approve'>Đồng ý</button>"
            "<button class='no' name='decision' value='reject'>Từ chối</button></form>"
        )
        return _page("Xác nhận thao tác", self._plan_html(op, binding.request_digest) + form)

    # ---- 3. quyết định ------------------------------------------------------------------------
    async def decide(self, request: Request) -> Response:
        form = await request.form()
        cookie = request.cookies.get(COOKIE, "")
        from starlette.concurrency import run_in_threadpool

        return await run_in_threadpool(
            self._decide,
            str(request.path_params["cid"]),
            str(form.get("session", "")),
            str(form.get("csrf", "")),
            str(form.get("decision", "")),
            cookie,
        )

    def _decide(self, cid: str, sess: str, csrf: str, decision: str, cookie: str) -> Response:
        tenant = self._tenant(cid)
        if (
            tenant is None
            or not sess.startswith(f"{cid}.")
            or decision not in ("approve", "reject")
        ):
            return _err("Yêu cầu không hợp lệ.", 400)

        def load() -> Any:
            data = self.sessions.get(sess)
            op = self.rt.operations.get_pending(cid)
            return data, op

        data, op = self._in_tenant(tenant, load)
        if (
            data is None
            or op is None
            or not hmac.compare_digest(str(data.get("cookie", "")), cookie)
            or not hmac.compare_digest(str(data.get("csrf", "")), csrf)
        ):
            return _err("Phiên xác nhận đã hết hạn hoặc không hợp lệ (CSRF).", 400)
        evidence = _evidence(data["evidence"])
        trace = "tr_" + uuid.uuid4().hex[:20]
        try:
            caller = self.rt.resolve(op.actor, trace)
        except Exception as e:  # người khởi tạo không còn trong tổ chức
            return _err(f"Người khởi tạo thao tác không còn hợp lệ: {e}", 409)

        def act() -> Any:
            self.sessions.delete(sess)
            current = self.rt.operations.get_pending(cid)
            binding = self.bindings.get(cid)
            if current is None or binding is None:
                return "Không tìm thấy yêu cầu xác nhận."
            if current.plan_digest != binding.request_digest:
                return "Nội dung thao tác đã đổi so với lúc tạo yêu cầu."
            receipts = self.receipts_for(self.rt.single_use)
            rb = ConfirmationBinding(
                self._round(current), binding.request_digest, binding.requester_subject
            )
            token = receipts.issue(rb, evidence, ApprovalDecision(decision))
            receipts.verify_and_consume(token, self._round(current), current.plan_digest)
            now = self.clock()
            if decision == "reject":
                return self.confirmations.decline(cid, evidence.subject, now)
            return self.confirmations.confirm(cid, evidence.subject, now, trace)

        try:
            out = self.rt.run_as(caller, op.actor, act)
        except IdentityError as e:
            return _err(f"Biên nhận xác nhận bị từ chối ({e.reason}).", 403)
        except Exception:
            log.exception("confirm decide failed cid=%s", cid)
            return _err("Lỗi máy chủ khi thực hiện thao tác.", 500)
        if isinstance(out, str):
            return _err(out, 409)
        status = out.status
        body = f"<p>Yêu cầu {_STATUS_VI[status]}.</p>"
        if out.error:
            body += (
                f"<div class='box'><p>{html.escape(str(out.error.get('message', '')))}</p></div>"
            )
            return _page("Không thực hiện được", body, 409)
        if out.receipt is not None:
            body += (
                f"<p>Mã thao tác: <code>{html.escape(out.receipt.operation_ref)}</code></p>"
                "<p>Bạn có thể quay lại cuộc trò chuyện; agent gọi lại đúng thao tác cũ sẽ nhận "
                "kết "
                "quả.</p>"
            )
        title = "Đã thực hiện" if status is PendingStatus.CONFIRMED else "Đã ghi nhận"
        return _page(title, body)


def _evidence_json(ev: StepUpEvidence) -> dict[str, Any]:
    return {
        "subject": ev.subject,
        "username": ev.username,
        "client_id": ev.client_id,
        "auth_time": ev.auth_time,
        "acr": ev.acr,
        "amr": list(ev.amr),
        "session_id": ev.session_id,
        "token_id": ev.token_id,
    }


def _evidence(d: Mapping[str, Any]) -> StepUpEvidence:
    return StepUpEvidence(
        subject=str(d["subject"]),
        username=d.get("username"),
        client_id=str(d["client_id"]),
        auth_time=int(d["auth_time"]),
        acr=str(d["acr"]),
        amr=tuple(d.get("amr") or ()),
        session_id=d.get("session_id"),
        token_id=d.get("token_id"),
    )
