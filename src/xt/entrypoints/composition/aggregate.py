# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""`aggregate_records`: count/sum per group on the server, same read path as search_records.

Filter, permission, RLS and scope come from the model engine exactly as for search; only
non-PII fields can be grouped or summed. Numbers come from one SQL statement; `as_of` is that
transaction's timestamp. Named metrics and avg/min/max are not in this release.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from xt.application.model_engine.ports import AggregateQuery
from xt.application.model_engine.service import jsonable
from xt.application.tools.context import CallContext, iso
from xt.application.tools.errors import invalid_request, not_found
from xt.application.tools.ports import Args, Data
from xt.domain.model.values import with_system_fields
from xt.entrypoints.composition.util import engine_call, signed_page, unavailable

if TYPE_CHECKING:
    from xt.entrypoints.composition.records import RecordServiceAdapter

_QUARTER = re.compile(r"^(\d{4})-Q([1-4])$")


def period_bounds(period: str) -> tuple[date, date]:
    """[start, end) of '2026', '2026-11', '2026-Q4' or '2026-11-01..2026-11-30'."""
    try:
        if ".." in period:
            lo, hi = (date.fromisoformat(p) for p in period.split("..", 1))
            if hi < lo:
                raise ValueError(period)
            return lo, date.fromordinal(hi.toordinal() + 1)
        if q := _QUARTER.match(period):
            year, quarter = int(q[1]), int(q[2])
            start = date(year, 3 * quarter - 2, 1)
            return start, date(year + 1, 1, 1) if quarter == 4 else date(year, 3 * quarter + 1, 1)
        if len(period) == 4:
            return date(int(period), 1, 1), date(int(period) + 1, 1, 1)
        year, month = (int(p) for p in period.split("-"))
        return date(year, month, 1), date(year + month // 12, month % 12 + 1, 1)
    except ValueError as exc:
        raise invalid_request(f"period không hợp lệ: {period}", "validation") from exc


def _metrics(args: Args) -> tuple[list[str], tuple[str, ...]]:
    if "metric" in args:
        unavailable("named metrics (aggregate_records metric=...)")
    names = list(dict.fromkeys(args.get("metrics") or ["count"]))
    sums: list[str] = []
    for name in names:
        op, _, field = name.partition(":")
        if op == "sum" and field:
            sums.append(field)
        elif name != "count":
            raise invalid_request(
                f"Chưa hỗ trợ metric '{name}'; dùng 'count' hoặc 'sum:<trường>'.", "validation"
            )
    return names, tuple(sums)


def _number(v: Any) -> str:
    return format(Decimal(str(v)).normalize(), "f") if v is not None else "0"


def aggregate_records(adapter: RecordServiceAdapter, ctx: CallContext, args: Args) -> Data:
    if "workspace" in args:
        unavailable("workspace-scoped aggregate")
    caller = adapter.rt.caller(ctx)
    version, model = adapter._model(caller.actor, str(args["model_id"]))
    if not any(
        r in model.permissions and "read" in model.permissions[r].actions
        for r in caller.actor.roles
    ):
        raise not_found(f"model:{model.name}")
    names, sums = _metrics(args)
    tz = caller.profile.timezone
    date_field = str(args.get("date_field", "created_at"))
    where = adapter._where(caller, ctx, model, args)
    if args.get("period"):
        lo, hi = period_bounds(str(args["period"]))
        field = model.by_name().get(date_field)
        if field is not None and field.type == "date":
            bounds: list[Any] = [lo.isoformat(), hi.isoformat()]
        else:  # datetime / system timestamps: tenant-local midnight
            bounds = [datetime.combine(d, time(), ZoneInfo(tz)).isoformat() for d in (lo, hi)]
        clauses = [
            {"field": date_field, "op": ">=", "value": bounds[0]},
            {"field": date_field, "op": "<", "value": bounds[1]},
        ]
        where = {"and": [*(where or {"and": []})["and"], *clauses]}
    group_by = tuple(str(g) for g in args.get("group_by", ()))
    bucket = (str(args["time_bucket"]), date_field, tz) if args.get("time_bucket") else None
    query = AggregateQuery(group_by, sums, bucket)
    out = engine_call(lambda: adapter.rt.records.aggregate(caller.actor, model.name, where, query))
    key_names = [*group_by, *([f"{date_field}:{bucket[0]}"] if bucket else [])]
    fields = with_system_fields(model)

    def key_value(name: str, v: Any) -> Any:
        f = fields.get(name)
        v = jsonable(v)
        if v is None or f is None or f.type not in ("ref", "user"):
            return v
        return f"{f.ref}:{v}" if f.type == "ref" else f"person:{v}"

    rows: list[Data] = []
    totals: dict[str, Any] = {"count": 0, **{f"sum:{f}": Decimal(0) for f in sums}}
    for keys, count, values in out["rows"]:
        metric = {"count": count, **{f"sum:{f}": v for f, v in zip(sums, values, strict=True)}}
        totals["count"] += count
        for f, v in zip(sums, values, strict=True):
            totals[f"sum:{f}"] += Decimal(str(v)) if v is not None else 0
        rows.append(
            {
                "keys": {n: key_value(n, k) for n, k in zip(key_names, keys, strict=True)},
                "values": {n: metric[n] if n == "count" else _number(metric[n]) for n in names},
            }
        )
    if args.get("sort"):
        sort = str(args["sort"])
        col = sort.removeprefix("-")
        if col not in key_names and col not in names:
            raise invalid_request(f"Không sắp xếp được theo '{col}'", "validation")
        part = "values" if col in names else "keys"

        def by(row: Data) -> tuple[bool, Any]:
            v = row[part][col]
            return (v is None, Decimal(v) if isinstance(v, str) and part == "values" else v)

        rows.sort(key=by, reverse=sort.startswith("-"))
    limit = int(args.get("limit", 20))
    page = signed_page(adapter.cursor_key, ctx, args)
    offset = page.offset()
    nxt = offset + limit
    total_values = {n: totals[n] if n == "count" else _number(totals[n]) for n in names}
    filters = {k: args[k] for k in ("where", "period", "group_by", "time_bucket") if k in args}
    return {
        "model_id": model.name,
        "model_revision": version,
        "as_of": iso(out["as_of"]),
        "scope": "authorized_records",
        "rows": rows[offset:nxt],
        "totals": total_values,
        "summary": (
            f"{model.label}: {len(rows)} nhóm, "
            + ", ".join(f"{n} = {v}" for n, v in total_values.items())
            + f"; bộ lọc {filters or 'không có'}; số liệu tại {iso(out['as_of'])}."
        ),
        "lineage_ref": f"explain:{ctx.trace_id}",
        "next_cursor": page.next(nxt) if nxt < len(rows) else None,
    }
