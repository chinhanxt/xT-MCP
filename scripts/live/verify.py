# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Read-only live-run verification; offline fixtures never constitute live evidence."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{0,62}\Z")
LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}\Z")


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str


def parse_time(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Timestamp requires an explicit timezone")
    return result.astimezone(UTC)


def _require(condition: bool) -> None:
    if not condition:
        raise ValueError("Missing or invalid evidence contract")


def _list(value: Any, *, nonempty: bool = False) -> list[Any]:
    _require(isinstance(value, list) and (bool(value) or not nonempty))
    return list(value)


def _dict(value: Any) -> dict[str, Any]:
    _require(isinstance(value, dict))
    return dict(value)


def _text(value: Any) -> str:
    _require(isinstance(value, str) and bool(value) and len(value) <= 300)
    return str(value)


def _integer(value: Any, *, minimum: int | None = None) -> int:
    _require(type(value) is int and (minimum is None or value >= minimum))
    return int(value)


def validate_plan(value: Any) -> dict[str, Any]:
    plan = _dict(value)
    try:
        _require(plan["format"] == "xt-live-plan/1")
        run = _text(plan["run_id"])
        _require(LABEL.fullmatch(run) is not None and len(run) >= 12)
        records = _list(plan["records"], nonempty=True)
        labels: dict[str, Any] = {}
        for raw in records:
            spec = _dict(raw)
            label, model = _text(spec["label"]), _text(spec["model"])
            _require(LABEL.fullmatch(label) is not None and label not in labels)
            _require(IDENTIFIER.fullmatch(model) is not None)
            labels[label] = spec
            _integer(spec["count"], minimum=0)
            _require(type(spec["in_window"]) is bool)
            match, fields = _dict(spec["match"]), _dict(spec["fields"])
            _require(bool(match))
            for name, item in {**match, **fields}.items():
                _require(IDENTIFIER.fullmatch(name) is not None)
                _require(item is None or type(item) in (str, int, bool))
            if spec["in_window"]:
                _require(
                    any(isinstance(v, str) and v.startswith(run + "-") for v in match.values())
                )
                _require(bool(spec.get("audit")))
            for op, count in _dict(spec.get("audit", {})).items():
                _require(op in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"))
                _integer(count, minimum=0)
        keys = set()
        for raw in _list(plan["operations"], nonempty=True):
            operation = _dict(raw)
            _require(operation["record"] in labels)
            key = tuple(_text(operation[name]) for name in ("actor_key", "tool", "key"))
            _require(key not in keys and key[2].startswith(run + "-"))
            _require(key[1] in ("create_record", "submit_request", "decide_request"))
            keys.add(key)
        wh = _dict(plan["warehouse"])
        # `also` lists further approved slips of the same run (e.g. a receive plus an issue).
        entries = [wh, *(_dict(x) for x in _list(wh.get("also", [])))]
        _require(len({e["request"] for e in entries}) == len(entries))
        for entry in entries:
            _require(
                entry["request"] in labels and labels[entry["request"]]["model"] == "stock_request"
            )
            for name in ("requester", "approver"):
                entry[name] = str(UUID(_text(entry[name])))
            _require(entry["requester"] != entry["approver"])
            _text(entry["approver_subject"])
            _require(labels[entry["request"]]["fields"].get("state") == "approved")
        # Optional cases are asserted only when the plan lists them (i.e. they were run).
        if "self_request" in plan:
            self_label = _text(plan["self_request"])
            _require(self_label in labels and labels[self_label]["model"] == "stock_request")
            _require(labels[self_label]["fields"].get("state") == "in_review")
        if "foreign" in plan:
            foreign = _dict(plan["foreign"])
            _require(
                re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", _text(foreign["tenant"])) is not None
            )
            foreign_model, foreign_id = _text(foreign["ref"]).split(":")
            _require(IDENTIFIER.fullmatch(foreign_model) is not None)
            UUID(foreign_id)
            _integer(foreign["revision"], minimum=1)
            foreign["ref"] = f"{foreign_model}:{UUID(foreign_id)}"
        for label, spec in labels.items():
            if spec["model"] != "stock_request" or not spec["count"]:
                continue
            _require(spec["count"] == 1)
            state = spec["fields"].get("state")
            _require(state in ("draft", "in_review", "approved"))
            # `baseline` slips were created and submitted before the run (shared fixture);
            # only the operations this run performed are required.
            required = set() if spec.get("baseline") is True else {"create_record"}
            if state in ("in_review", "approved") and spec.get("baseline") is not True:
                required.add("submit_request")
            if state == "approved":
                required.add("decide_request")
            actual = [o["tool"] for o in plan["operations"] if o["record"] == label]
            _require(set(actual) == required and len(actual) == len(required))
        for entry in entries:
            for line in _list(entry["lines"], nonempty=True):
                for name in ("item", "location", "warehouse"):
                    line[name] = str(UUID(_text(line[name])))
                # receive adds stock (+), issue removes it (-); `kind` is in the plan fields.
                kind = labels[entry["request"]]["fields"].get("kind", "issue")
                _require(kind in ("receive", "issue"))
                quantity = _integer(line["quantity_milli"])
                _require(quantity > 0 if kind == "receive" else quantity < 0)
        for balance in _list(wh["balances"], nonempty=True):
            for name in ("balance", "level"):
                _require(balance[name] in labels)
            for name in ("item", "location", "warehouse"):
                balance[name] = str(UUID(_text(balance[name])))
            for name in ("before", "level_before"):
                _integer(balance[name], minimum=0)
        if "disabled_pack" in plan:
            _text(plan["disabled_pack"])
        if "pii" in plan:
            pii = _dict(plan["pii"])
            _text(pii["trace_id"])
            _require(pii["tool"] in ("get_record", "search_records", "find_people"))
            for field in _list(pii["fields"], nonempty=True):
                _text(field)
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("Incomplete verification plan") from exc
    return plan


def _inside(value: Any, since: datetime, until: datetime) -> bool:
    return isinstance(value, str) and since <= parse_time(value) <= until


def _matches(row: dict[str, Any], fields: dict[str, Any]) -> bool:
    return all(
        name in row and type(row[name]) is type(value) and row[name] == value
        for name, value in fields.items()
    )


def _evaluate(
    plan: dict[str, Any],
    snapshot: dict[str, Any],
    since: datetime,
    until: datetime,
) -> list[Check]:
    """Evaluate independently collected state; no response/host claims are inferred."""
    _require(since < until and snapshot["format"] == "xt-live-snapshot/1")
    _require(parse_time(snapshot["since"]) == since and parse_time(snapshot["until"]) == until)
    _require(parse_time(snapshot["captured_at"]) >= until)
    UUID(_text(snapshot["tenant_id"]))
    for name in ("operations", "movements", "decisions", "workflow", "packs", "pii", "audit"):
        _list(snapshot.get(name))
    records = _dict(snapshot.get("records"))
    record_audit = _dict(snapshot.get("record_audit"))
    stock_changes = _list(snapshot.get("stock_changes"))
    status = _dict(snapshot.get("audit_status"))
    checks: list[Check] = []

    def check(name: str, passed: bool, detail: str) -> None:
        checks.append(Check(name, bool(passed), detail))

    specs = {spec["label"]: spec for spec in plan["records"]}
    for label, spec in specs.items():
        rows = _list(records.get(label))
        valid = len(rows) == spec["count"]
        for row in rows:
            UUID(_text(row["id"]))
            _integer(row["revision"], minimum=1)
            valid &= _matches(row, {**spec["match"], **spec["fields"]})
            valid &= parse_time(row["updated_at"]) <= until
            if spec["in_window"]:
                valid &= _inside(row["created_at"], since, until)
                valid &= _inside(row["updated_at"], since, until)
        check(f"record/{label}", valid, f"count={len(rows)}, expected={spec['count']}")
        if "audit" in spec:
            events = _list(record_audit.get(label))
            counts = {
                op: sum(e["op"] == op for e in events)
                for op in ("INSERT", "UPDATE", "DELETE", "TRUNCATE")
            }
            expected = dict.fromkeys(counts, 0) | spec["audit"]
            check(
                f"audit/{label}",
                counts == expected
                and all(
                    e["model"] == spec["model"] and _inside(e["at"], since, until) for e in events
                ),
                "exact mutation counts within window",
            )
    for index, spec in enumerate(plan["operations"]):
        rows = [
            op
            for op in snapshot["operations"]
            if _matches(
                op,
                {
                    "actor_key": spec["actor_key"],
                    "tool": spec["tool"],
                    "idempotency_key": spec["key"],
                },
            )
        ]
        target = records[spec["record"]]
        valid = len(rows) == 1 and len(target) == 1
        if valid:
            receipt = _dict(rows[0].get("receipt"))
            data = _dict(receipt.get("data"))
            valid = rows[0]["state"] == "done" and bool(rows[0].get("fingerprint"))
            result_ref = (
                _dict(data.get("record")).get("ref")
                if spec["tool"] == "create_record"
                else _dict(data.get("request")).get("ref")
            )
            valid &= result_ref == f"{specs[spec['record']]['model']}:{target[0]['id']}"
            valid &= receipt.get("commit_state") == "committed"
            valid &= _inside(receipt.get("committed_at"), since, until)
        check(f"operation/{index}", valid, "one committed receipt per actor/tool/key/ref")
    wh = plan["warehouse"]
    entries = [wh, *wh.get("also", [])]
    all_lines = [line for e in entries for line in e["lines"]]
    posted: list[tuple[str, dict[str, Any], int]] = []  # (posted_at, line, delta) by movement
    for entry in entries:
        sfx = "" if entry is wh else f":{entry['request']}"
        issue = records[entry["request"]]
        rid = issue[0]["id"] if len(issue) == 1 else ""
        ref = f"stock_request:{rid}"
        movements = [m for m in snapshot["movements"] if m["request"] == rid]
        wanted = [
            {**line, "request": rid, "key": f"{rid}:{i}", "approved_by": entry["approver"]}
            for i, line in enumerate(entry["lines"])
        ]
        valid = len(movements) == len(wanted)
        for expected in wanted:
            matched = [m for m in movements if _matches(m, expected)]
            valid &= len(matched) == 1 and _inside(matched[0]["posted_at"], since, until)
            if len(matched) == 1:
                posted.append((matched[0]["posted_at"], expected, expected["quantity_milli"]))
        check(
            f"warehouse/movements{sfx}",
            valid,
            "one signed movement per line, correct principal and time",
        )
        decisions = [d for d in snapshot["decisions"] if d["ref"] == ref]
        valid = len(decisions) == 1
        if valid:
            d = decisions[0]
            valid = _matches(
                d,
                {
                    "requester": entry["requester"],
                    "approver": entry["approver"],
                    "decision": "approve",
                },
            )
            people = _list(snapshot.get("people"), nonempty=True)
            approvers = [p for p in people if p["id"] == entry["approver"] and p["active"] is True]
            requesters = [p for p in people if p["id"] == entry["requester"]]
            valid &= len(approvers) == len(requesters) == 1
            valid &= bool(approvers) and approvers[0]["subject"] == entry["approver_subject"]
            confirmed = _list(d["confirmed_by"], nonempty=True)
            valid &= entry["approver_subject"] in confirmed
            valid &= bool(requesters) and requesters[0]["subject"] not in confirmed
            valid &= _inside(d["decided_at"], since, until)
            valid &= len(issue) == 1 and issue[0]["owner_id"] == entry["requester"]
        check(
            f"warehouse/decision{sfx}", valid, "one approval by distinct, confirmed human approver"
        )
        workflows = [w for w in snapshot["workflow"] if w["ref"] == ref]
        check(
            f"warehouse/workflow{sfx}",
            len(workflows) == 1
            and _matches(workflows[0], {"status": "completed", "candidates": []}),
            "completed workflow without candidates",
        )
    posted.sort(key=lambda item: item[0])
    balances_valid = True
    covered = {(b["item"], b["location"], b["warehouse"]) for b in wh["balances"]}
    balances_valid &= covered == {
        (line["item"], line["location"], line["warehouse"]) for line in all_lines
    }
    for balance in wh["balances"]:
        loc_keys = ("item", "location", "warehouse")
        lvl_keys = ("item", "warehouse")
        for label, model, field, base, keys, key in (
            (
                balance["balance"],
                "stock_balance",
                "quantity_milli",
                balance["before"],
                loc_keys,
                f"{balance['item']}:{balance['location']}",
            ),
            (
                balance["level"],
                "stock_level",
                "on_hand_milli",
                balance["level_before"],
                lvl_keys,
                f"{balance['item']}:{balance['warehouse']}",
            ),
        ):
            steps = [d for _, line, d in posted if all(line[k] == balance[k] for k in keys)]
            total = base + sum(steps)
            rows = records[label]
            balances_valid &= specs[label]["model"] == model and len(rows) == 1
            balances_valid &= len(rows) == 1 and _matches(rows[0], {"key": key, field: total})
            balances_valid &= total >= 0
            changes = [
                c
                for c in stock_changes
                if len(rows) == 1 and c["record_id"] == rows[0]["id"] and c["model"] == model
            ]
            if (
                base == 0
                and changes
                and _matches(changes[0], {"op": "INSERT", "after": 0})
                and changes[0].get("before") is None
            ):
                changes = changes[1:]  # row created by the first posting
            running = base
            balances_valid &= len(changes) == len(steps)
            for change, delta in zip(changes, steps, strict=False):
                balances_valid &= _matches(
                    change, {"op": "UPDATE", "before": running, "after": running + delta}
                ) and _inside(change["at"], since, until)
                running += delta
    check("warehouse/balances", balances_valid, "location and warehouse totals = baseline + ledger")
    draft_ids = {
        r["id"]
        for label, spec in specs.items()
        if spec["model"] == "stock_request" and spec["fields"].get("state") == "draft"
        for r in records[label]
    }
    if draft_ids:
        check(
            "warehouse/draft",
            not any(m["request"] in draft_ids for m in snapshot["movements"])
            and not any(
                d["ref"] in {f"stock_request:{i}" for i in draft_ids}
                for d in snapshot["decisions"] + snapshot["workflow"]
            ),
            "AI draft has no posting, decision or workflow",
        )
    if "self_request" in plan:
        self_rows = records[plan["self_request"]]
        self_id = self_rows[0]["id"] if len(self_rows) == 1 else ""
        self_ref = f"stock_request:{self_id}"
        check(
            "negative/self",
            len(self_rows) == 1
            and self_rows[0]["owner_id"] == wh["approver"]
            and not any(m["request"] == self_id for m in snapshot["movements"])
            and not any(d["ref"] == self_ref for d in snapshot["decisions"])
            and not any(
                w["ref"] == self_ref and w["status"] == "completed" for w in snapshot["workflow"]
            ),
            "self-owned probe remains in review without decision or posting; response separate",
        )
    if "foreign" in plan:
        foreign = _dict(snapshot.get("foreign"))
        UUID(_text(foreign["tenant_id"]))
        expected_foreign = plan["foreign"]
        foreign_rows = _list(foreign.get("records"))
        foreign_valid = foreign.get("tenant") == expected_foreign["tenant"] != snapshot["tenant"]
        foreign_valid &= foreign.get("tenant_id") != snapshot["tenant_id"]
        foreign_valid &= foreign.get("ref") == expected_foreign["ref"]
        foreign_valid &= len(foreign_rows) == 1 and _matches(
            foreign_rows[0],
            {
                "id": expected_foreign["ref"].split(":")[1],
                "revision": expected_foreign["revision"],
            },
        )
        foreign_valid &= type(foreign.get("changes")) is int and foreign["changes"] == 0
        check(
            "negative/foreign",
            foreign_valid,
            "real foreign record keeps baseline revision with no mutations in window",
        )
    check(
        "audit/chain",
        type(status.get("n")) is int
        and status["n"] > 0
        and "first_bad_seq" in status
        and status["first_bad_seq"] is None,
        "DB xt_audit.verify: whole-tenant chain consistency; external anchor separate",
    )
    if "disabled_pack" in plan:
        packs = [p for p in snapshot["packs"] if p["pack"] == plan["disabled_pack"]]
        probes = [s for s in specs.values() if s["count"] == 0]
        check(
            "disabled/app",
            len(packs) == 1
            and packs[0]["status"] == "disabled"
            and any(s["model"] in packs[0]["models"] for s in probes),
            "disabled pack owns absent probe model; denial requires captured response",
        )
    if "pii" not in plan:
        return checks
    pii = plan["pii"]
    matched_pii = [
        p
        for p in snapshot["pii"]
        if _matches(
            p,
            {
                "trace_id": pii["trace_id"],
                "tool": pii["tool"],
                "mode": "masked",
            },
        )
        and set(pii["fields"]) <= set(p["fields"])
        and _inside(p["at"], since, until)
    ]
    all_masked = all(
        p["trace_id"] == pii["trace_id"] and p["mode"] == "masked" and p["client_class"] == "cloud"
        for p in snapshot["pii"]
    )
    check(
        "pii/egress",
        bool(matched_pii) and all_masked,
        "all cloud egress on trace masked; required tool/fields present",
    )
    return checks


def evaluate(
    plan: dict[str, Any],
    snapshot: dict[str, Any],
    since: datetime,
    until: datetime,
) -> list[Check]:
    try:
        return _evaluate(plan, snapshot, since, until)
    except (KeyError, TypeError, AttributeError, IndexError) as exc:
        raise ValueError("Incomplete verification snapshot") from exc


def read_json(path: Path) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            _require(key not in result)
            result[key] = value
        return result

    return _dict(json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--since", required=True)
    parser.add_argument("--until", required=True)
    parser.add_argument("--dsn-env", default="XT_LIVE_VERIFY_DSN")
    parser.add_argument(
        "--snapshot", type=Path, help="Offline fixture/export; no server connection"
    )
    args = parser.parse_args(argv)
    try:
        plan = validate_plan(read_json(args.plan))
        since, until = parse_time(args.since), parse_time(args.until)
        _require(
            since < until and re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", args.tenant) is not None
        )
        if args.snapshot:
            snapshot = read_json(args.snapshot)
            print("OFFLINE / FIXTURE — not LIVE server evidence")
        else:
            _require(until <= datetime.now(UTC))
            _require(re.fullmatch(r"[A-Z_][A-Z0-9_]*", args.dsn_env) is not None)
            dsn = os.environ.get(args.dsn_env)
            _require(bool(dsn))
            # Import only for online collection; offline evaluation needs no DB driver.
            if __package__ in (None, ""):
                sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
            from scripts.live.db import collect

            snapshot = collect(plan, args.tenant, since, until, str(dsn))
            print("SERVER STATE — one READ ONLY PostgreSQL snapshot")
        _require(snapshot["tenant"] == args.tenant)
        checks = evaluate(plan, snapshot, since, until)
    except Exception:
        # DB errors, JSON excerpts and DSNs can contain secrets or PII.
        print(
            "FAIL | input/collection | invalid or unavailable evidence; details withheld",
            file=sys.stderr,
        )
        return 2
    print("RESULT | CHECK | EVIDENCE")
    for item in checks:
        print(f"{'PASS' if item.passed else 'FAIL'} | {item.name} | {item.detail}")
    print("State checks only. Denial, replay, sources and UI need captured host evidence.")
    return 0 if all(item.passed for item in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
