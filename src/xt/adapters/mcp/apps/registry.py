# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Dựng `UiResource` (HTML + CSP) và `AppTool` (tool app-only) từ contract."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from html import escape
from importlib import resources
from typing import Any
from urllib.parse import urlsplit

from xt.adapters.mcp.server import AppTool, UiResource
from xt.application.tools import CallContext, ToolDispatcher, ToolError, ToolOutcome
from xt.application.tools.errors import access_denied, invalid_request, not_found
from xt.application.tools.ports import AccessCheck, PersonDetails
from xt.application.tools.safe_log import log_failure
from xt.contracts.tools import (
    APPROVAL_CARD_URI,
    CONTRACT_VERSION,
    ORGCHART_URI,
    RECORD_TABLE_URI,
    UI_VIEWS,
    ErrorCode,
    StrictValidator,
    success_envelope,
)
from xt.contracts.tools.apps import ORG_PERSON_DETAILS, RECORDS_TABLE_PAGE, AppToolContract

log = logging.getLogger("xt.mcp.apps")

# Ghép bridge theo nhu cầu: thẻ phê duyệt KHÔNG có đường gọi tool (chỉ ui/open-link).
_FILES = {
    ORGCHART_URI: ("orgchart.html", ("bridge-core.js", "bridge-tools.js")),
    APPROVAL_CARD_URI: ("approval-card.html", ("bridge-core.js", "bridge-open-link.js")),
    RECORD_TABLE_URI: ("record-table.html", ("bridge-core.js", "bridge-tools.js")),
}


@cache
def _asset(name: str) -> str:
    return resources.files("xt.adapters.mcp.apps").joinpath("ui", name).read_text("utf-8")


def normalize_origin(xt_origin: str) -> str:
    """Chỉ nhận origin (scheme://host[:port]); http chỉ cho loopback (dev/test)."""
    u = urlsplit(xt_origin)
    loopback = u.hostname in ("localhost", "127.0.0.1", "::1")
    if (
        not u.hostname
        or u.scheme not in ("https", "http")
        or (u.scheme == "http" and not loopback)
        or u.path not in ("", "/")
        or u.query
        or u.fragment
        or u.username
        or u.password
    ):
        raise ValueError(f"origin xT không hợp lệ: {xt_origin!r}")
    return f"{u.scheme}://{u.netloc}"


def render_html(uri: str, xt_origin: str) -> str:
    page, scripts = _FILES[uri]
    bridge = "\n".join(_asset(s) for s in scripts)
    if "</script" in bridge.lower():
        raise ValueError("bridge chứa </script>")
    return (
        _asset(page).replace("/*BRIDGE*/", bridge).replace("__XT_ORIGIN__", escape(xt_origin, True))
    )


def ui_csp(xt_origin: str) -> dict[str, list[str]]:
    """`_meta.ui.csp`: App chỉ được nối tới origin xT (kênh /pii/reveal); không tài nguyên ngoài."""
    return {
        "connectDomains": [xt_origin],
        "resourceDomains": [],
        "frameDomains": [],
        "baseUriDomains": [],
    }


@dataclass(frozen=True, slots=True)
class AppsBundle:
    ui_resources: tuple[UiResource, ...]
    app_tools: tuple[AppTool, ...]


def _outcome(
    ctx: CallContext, contract: AppToolContract, e: ToolError, d: ToolDispatcher
) -> ToolOutcome:
    error = dict(d.services.pii.scrub_error(ctx, contract.name, e.to_error()))
    envelope = {
        "ok": False,
        "untrusted": True,
        "error": error,
        "meta": {"contract_version": CONTRACT_VERSION, "trace_id": ctx.trace_id},
    }
    return ToolOutcome(True, envelope, contract.name)


def _guard(
    d: ToolDispatcher,
    contract: AppToolContract,
    ctx: CallContext,
    args: Mapping[str, Any],
    dups: Sequence[str],
) -> ToolError | None:
    if dups:
        return invalid_request(
            "The arguments JSON repeats an object key.", "duplicate_key", keys=list(dups)
        )
    issues = [
        {"path": "/" + "/".join(map(str, e.absolute_path)), "message": e.message[:200]}
        for e in StrictValidator(dict(contract.input_schema)).iter_errors(dict(args))
    ][:10]
    if issues:
        return invalid_request(
            "Arguments do not match the tool input schema.", "schema", errors=issues
        )
    ref = args.get("ref")
    decision = d.services.access.check(
        ctx,
        AccessCheck(
            contract.name, contract.iam[0], ref if isinstance(ref, str) else None, dict(args)
        ),
    )
    if not decision.allowed:
        return access_denied(
            decision.message or "Not allowed.",
            decision.reason,
            decision.explain_ref,
            **dict(decision.details),
        )
    return None


def _org_person_details(d: ToolDispatcher, people: PersonDetails) -> AppTool:
    contract = ORG_PERSON_DETAILS
    validator = StrictValidator(success_envelope(contract.output_schema))

    def run(ctx: CallContext, args: Mapping[str, Any], dups: Sequence[str]) -> ToolOutcome:
        try:
            if (err := _guard(d, contract, ctx, args, dups)) is not None:
                return _outcome(ctx, contract, err, d)
            data = people.person_details(ctx, str(args["ref"]))
            if data is None:
                raise not_found(str(args["ref"]))
            # Cùng hình dạng một phần tử people[] của find_people: làn PII che email/điện thoại.
            projected = d.services.pii.outbound(ctx, "find_people", dict(data))
            envelope = {
                "ok": True,
                "untrusted": True,
                "data": projected.data,
                "meta": {
                    "contract_version": CONTRACT_VERSION,
                    "trace_id": ctx.trace_id,
                    "as_of": ctx.now_iso,
                    "effective_level": "L0",
                    "pii": projected.meta(),
                },
            }
            if next(iter(validator.iter_errors(envelope)), None) is not None:
                raise RuntimeError("output contract violation")
            return ToolOutcome(False, envelope, contract.name)
        except ToolError as e:
            return _outcome(ctx, contract, e, d)
        except Exception as e:
            log_failure(log, "app tool failure", e, trace_id=ctx.trace_id, tool=contract.name)
            return _outcome(
                ctx, contract, ToolError(ErrorCode.INTERNAL_ERROR, "Unexpected server failure."), d
            )

    return AppTool(contract, run)


def _records_table_page(d: ToolDispatcher) -> AppTool:
    contract = RECORDS_TABLE_PAGE
    assert contract.delegates_to is not None

    def run(ctx: CallContext, args: Mapping[str, Any], dups: Sequence[str]) -> ToolOutcome:
        # Toàn bộ pipeline của search_records (schema, IAM, làn PII, cursor ký) áp dụng nguyên.
        return d.call(contract.delegates_to or "", args, ctx, duplicate_keys=dups)

    return AppTool(contract, run)


def register_apps(
    *, xt_origin: str, dispatcher: ToolDispatcher, people: PersonDetails
) -> AppsBundle:
    """Đăng ký 3 MCP App + 2 tool app-only. `xt_origin` = origin công khai của xT (Desk/confirm)."""
    origin = normalize_origin(xt_origin)
    csp = ui_csp(origin)
    ui = tuple(
        UiResource(
            v.uri, v.name, render_html(v.uri, origin), v.description, csp=csp, prefers_border=True
        )
        for v in UI_VIEWS
    )
    return AppsBundle(
        ui, (_org_person_details(dispatcher, people), _records_table_page(dispatcher))
    )
