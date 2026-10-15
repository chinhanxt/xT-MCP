# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Làn PII tối thiểu theo TCV1 §3.8 (research/spike-pii-lane.md).

Chế độ hiệu lực = (policy `llm_egress` của tenant, lớp client):

| llm_egress | human | local | cloud   |
|------------|-------|-------|---------|
| masked     | full  | full  | masked  |
| local_only | full  | full  | omitted |

- Field PII = field model có `pii` khác `none` (basic/sensitive); áp cho `values` và `title`
  (khi `title_field` là PII) của mọi bản ghi trong kết quả đọc/ghi.
- Token `[PII:<16 ký tự>]` = HMAC-SHA256(khóa máy chủ, tenant|epoch|giá trị): ổn định nên so bằng
  được; vault `xt_meta.pii_token` (RLS) cho phép `where` dùng token (chỉ so bằng).
- Mọi lần dữ liệu PII rời xT ghi `xt_meta.pii_egress` (có audit trigger -> hash-chain).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from collections.abc import Mapping
from typing import Any

from xt.application.tools.context import CallContext, ClientClass
from xt.application.tools.dispatcher import PII_TOKEN
from xt.application.tools.errors import invalid_request
from xt.application.tools.pii_scrub import ErrorScrubber
from xt.application.tools.ports import Args, Data, PiiMode, PiiProjection
from xt.domain.model.spec import Catalog
from xt.entrypoints.composition.runtime import Caller, Runtime


def effective_mode(llm_egress: str, client: ClientClass) -> PiiMode:
    if client is not ClientClass.CLOUD:
        return "full"
    return "omitted" if llm_egress == "local_only" else "masked"


class PostgresPiiLane:
    def __init__(self, rt: Runtime, key: bytes) -> None:
        if len(key) < 32:
            raise ValueError("PII token key must contain at least 32 bytes")
        self.rt, self.key = rt, key

    def token(self, caller: Caller, value: Any) -> str:
        raw = f"{caller.tenant.id}|{caller.profile.pii_epoch}|{value}".encode()
        mac = hmac.new(self.key, raw, hashlib.sha256).digest()
        tok = base64.b32encode(mac).decode().rstrip("=").lower()[:16]
        out = f"[PII:{tok}]"
        self.rt.store.pii_put(caller.actor, out, str(value))
        return out

    # ---- vào --------------------------------------------------------------------------------
    def inbound(self, ctx: CallContext, tool: str, args: Args) -> Args:
        caller = self.rt.caller(ctx)
        out = dict(args)
        where = args.get("where")
        if isinstance(where, Mapping):
            resolved: dict[str, Any] = {}
            for field, cond in where.items():
                resolved[field] = self._resolve_cond(caller, cond)
            out["where"] = resolved
        for k, v in out.items():
            if k != "where" and PII_TOKEN.search(repr(v)):
                raise invalid_request(
                    "Masked [PII:…] tokens are only accepted as equality values in where.",
                    "masked_value",
                )
        return out

    def _resolve_cond(self, caller: Caller, cond: Any) -> Any:
        if isinstance(cond, str) and PII_TOKEN.fullmatch(cond):
            return self._value(caller, cond)
        if isinstance(cond, Mapping):
            fixed: dict[str, Any] = {}
            for op, v in cond.items():
                if isinstance(v, str) and PII_TOKEN.fullmatch(v):
                    if op not in ("eq", "ne"):
                        raise invalid_request(
                            "PII tokens only support eq/ne comparisons.", "masked_value"
                        )
                    v = self._value(caller, v)
                fixed[op] = v
            return fixed
        return cond

    def _value(self, caller: Caller, token: str) -> str:
        value = self.rt.store.pii_resolve(caller.actor, token)
        if value is None:
            raise invalid_request("Unknown or expired PII token.", "masked_value")
        return value

    # ---- lỗi ---------------------------------------------------------------------------------
    def scrub_error(self, ctx: CallContext, tool: str, error: Data) -> Data:
        """Lưới lỗi chung `ErrorScrubber`: field `pii` của catalog + bộ nhận dạng đã kiểm."""
        caller = self.rt.caller(ctx)
        mode = effective_mode(caller.profile.llm_egress, ctx.client_class)
        if mode == "full":
            return error
        scrubber = ErrorScrubber(
            (lambda v: self.token(caller, v)) if mode == "masked" else (lambda v: "[PII]"),
            pii_fields=self._pii_fields(caller),
        )
        cleaned = scrubber.error(error)
        if scrubber.hits:
            self.rt.store.pii_egress(
                caller.actor,
                trace_id=ctx.trace_id,
                tool=tool,
                client_class=ctx.client_class.value,
                mode=mode,
                fields=["error"],
            )
        return cleaned

    def _pii_fields(self, caller: Caller) -> set[str]:
        catalogs = getattr(self.rt, "catalogs", None)
        current = catalogs.current(caller.actor) if catalogs is not None else None
        if not current:
            return set()
        cat: Catalog = current[1]
        return {f.name for m in cat.models for f in m.fields if f.pii != "none"}

    # ---- ra ---------------------------------------------------------------------------------
    def outbound(self, ctx: CallContext, tool: str, data: Data) -> PiiProjection:
        caller = self.rt.caller(ctx)
        mode = effective_mode(caller.profile.llm_egress, ctx.client_class)
        current = self.rt.catalogs.current(caller.actor)
        touched: set[str] = set()
        out = self._walk(caller, current[1] if current else None, data, mode, touched)
        if touched:
            self.rt.store.pii_egress(
                caller.actor,
                trace_id=ctx.trace_id,
                tool=tool,
                client_class=ctx.client_class.value,
                mode=mode,
                fields=sorted(touched),
            )
        fields = tuple(sorted(touched))
        return PiiProjection(
            out,
            mode,
            masked_fields=fields if mode == "masked" else (),
            omitted_fields=fields if mode == "omitted" else (),
        )

    def _walk(
        self, caller: Caller, cat: Catalog | None, node: Any, mode: PiiMode, touched: set[str]
    ) -> Any:
        if isinstance(node, list):
            return [self._walk(caller, cat, x, mode, touched) for x in node]
        if not isinstance(node, Mapping):
            return node
        out = {k: self._walk(caller, cat, v, mode, touched) for k, v in node.items()}
        model_id, values = out.get("model_id"), out.get("values")
        if cat is None or not isinstance(model_id, str) or not isinstance(values, Mapping):
            return out
        try:
            m = cat.model(model_id)
        except KeyError:
            return out
        projected = dict(values)
        for f in m.fields:
            if f.pii == "none" or f.name not in projected or projected[f.name] is None:
                continue
            name = f"{model_id}.{f.name}"
            touched.add(name)
            if mode == "masked":
                projected[f.name] = self.token(caller, projected[f.name])
            elif mode == "omitted":
                projected.pop(f.name)
        out["values"] = projected
        if m.title_field and mode != "full":
            tf = m.field(m.title_field)
            if tf.pii != "none":
                out["title"] = self.token(caller, out.get("title")) if mode == "masked" else m.label
        return out
