# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""E13: §9 demo through real MCP, Keycloak, Desk confirmation and product CLI.

The connector is scripted, so this scenario needs no LLM. Records and file bytes are real;
organization roles and delegated permissions are provisioned through ``xt org import``.
"""

from __future__ import annotations

import json
import re
import shutil
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from xte2e import compose, net, seed
from xte2e.config import HEADLESS_CLIENT, ROOT
from xte2e.flows import B_EMPLOYEE, DEPT_HEAD_ACCT, DIRECTOR, REQUESTER, A, B, dumps, err, key
from xte2e.report import Evidence

SCENARIO = "E13"
pytestmark = pytest.mark.e2e

SOP = (
    "SOP xuất kho: nhân viên lập phiếu ghi kho, vị trí, mặt hàng và số lượng. "
    "Quản lý kho khác người lập kiểm tra và xác nhận bằng tài khoản cá nhân. "
    "Chỉ sau khi duyệt, hệ thống ghi biến động và cập nhật tồn kho một lần; "
    "không xuất vượt tồn và không sửa trực tiếp sổ biến động."
)


class StockAgentOwnershipBug(AssertionError):
    """E13-BUG-01: agent stock ownership is compared with its client principal ID."""


def _call(
    world: Any,
    ev: Evidence,
    who: dict[str, Any],
    tool: str,
    args: dict[str, Any],
    label: str | None = None,
) -> dict[str, Any]:
    out = world.call(who, tool, args)
    ev.step(label or tool, out)
    failure = err(out)
    if (
        out.get("ok") is False
        and tool == "create_record"
        and args.get("model_id") == "stock_request"
        and args.get("values", {}).get("kind") == "issue"
        and failure.get("code") == "access_denied"
        and failure.get("details", {}).get("reason") == "implicit_deny"
        and failure.get("details", {}).get("principal") == f"agent:{seed.AGENT_CLIENT}"
    ):
        raise StockAgentOwnershipBug(
            "E13-BUG-01: create_record stock_request(kind=issue) is denied because "
            "wh.request-write compares the person's owner UUID with agent:xt-desk-agent."
        )
    assert out.get("ok") is True, f"{tool}: {dumps(out)[:1200]}"
    return dict(out["data"])


def _create(
    world: Any, ev: Evidence, who: dict[str, Any], model: str, values: dict[str, Any]
) -> dict[str, Any]:
    return dict(
        _call(
            world,
            ev,
            who,
            "create_record",
            {
                "model_id": model,
                "values": values,
                "idempotency_key": key(f"e13-{model}"),
            },
        )["record"]
    )


def _search(
    world: Any, ev: Evidence, who: dict[str, Any], args: dict[str, Any]
) -> list[dict[str, Any]]:
    """Follow every real cursor; lineage must not silently truncate its source set."""
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    page_args = {**args, "limit": 1}
    while True:
        page = _call(world, ev, who, "search_records", page_args)
        items.extend(page["items"])
        cursor = page.get("next_cursor")
        if cursor is None:
            return items
        assert cursor not in seen, "search_records repeated a cursor"
        seen.add(cursor)
        page_args = {**page_args, "cursor": cursor}


def _org_roles(world: Any, ev: Evidence, directory: Path) -> None:
    """Extend demo input data, preserving existing subjects, mappings and other grants.

    IAM checks both the person and the delegated grant. A warehouse manager delegates the
    warehouse role, rather than role:agent (which the warehouse policy explicitly denies
    for approval). L1 still requires the real person's Desk confirmation.
    """
    tenant = world.tenants[A]
    original = Path(tenant["org_file"])
    org = json.loads(original.read_text(encoding="utf-8"))
    mapping = json.loads(original.with_name("mapping.json").read_text(encoding="utf-8"))
    additions = {
        REQUESTER: ["sales"],
        DEPT_HEAD_ACCT: ["warehouse_manager"],
        # A second eligible manager gives a real destination for the first
        # manager's own slip; the creator is excluded by workflow SoD.
        DIRECTOR: ["admin", "warehouse_manager"],
    }
    for person in org["employees"]:
        if person["id"] in additions:
            source_role = f"e13_{person['id'].lower().replace('-', '_')}"
            person["roles"] = [*person.get("roles", []), source_role]
            mapping["roles"][source_role] = additions[person["id"]]
    directory.chmod(0o755)
    org_file = directory / "org.json"
    org_file.write_text(json.dumps(org, ensure_ascii=False), encoding="utf-8")
    (directory / "mapping.json").write_text(json.dumps(mapping), encoding="utf-8")
    links = json.loads(json.dumps(tenant["links"]))
    for grant in links["agent_grants"]:
        roles = additions.get(grant["person"])
        if roles:
            # File/document owner policies use the dedicated role:agent grant and
            # then intersect it with the human owner's permissions. Preserve that
            # delegation except on the warehouse approval client, whose policy
            # explicitly denies role:agent approval.
            delegated = list(grant["roles"])
            if grant["person"] == DEPT_HEAD_ACCT and grant["client_id"] == seed.AGENT_CLIENT:
                delegated = [role for role in delegated if role != "agent"]
            grant["roles"] = list(dict.fromkeys([*delegated, "employee", *roles]))
    out = seed.import_org(A, org_file, links, force=True)
    ev.step(
        "xt org import: demo roles + explicit delegated L1 scopes",
        {
            "roles": additions,
            "output": out.strip(),
        },
    )


def _pack(world: Any, ev: Evidence, action: str) -> None:
    """Operator CLI: inspect a real plan, prove SoD, then apply the exact digest."""
    target = "/packages/crm" if action == "install" else "crm"
    proposer = f"person:{REQUESTER}"
    approver = f"person:{DIRECTOR}"
    assert proposer != approver
    common = [
        "pack",
        action,
        "--tenant",
        A,
        target,
        "--proposed-by",
        proposer,
        "--approved-by",
        approver,
    ]
    mounts = [(ROOT / "packages", "/packages")] if action == "install" else []
    rc, plan = compose.xt(common, mounts=mounts)
    ev.step(f"xt pack {action} crm: plan (not applied)", {"rc": rc, "plan": plan})
    assert rc == 0, plan[-1600:]
    digest = re.search(r"sha256:[0-9a-f]{64}", plan)
    assert digest, plan[-1200:]
    # Even possession of the valid plan digest cannot authorize self approval.
    same = [
        "pack",
        action,
        "--tenant",
        A,
        target,
        "--proposed-by",
        proposer,
        "--approved-by",
        proposer,
        "--confirm",
        digest.group(),
    ]
    denied_rc, denied = compose.xt(same, mounts=mounts)
    ev.step(f"xt pack {action}: proposer cannot approve", {"rc": denied_rc, "output": denied})
    assert denied_rc != 0 and "tách nhiệm vụ" in denied, denied[-1200:]
    rc, applied = compose.xt([*common, "--confirm", digest.group()], mounts=mounts)
    ev.step(
        f"xt pack {action} crm: independent approval",
        {
            "proposed_by": proposer,
            "approved_by": approver,
            "digest": digest.group(),
            "rc": rc,
            "output": applied,
        },
    )
    assert rc == 0, applied[-1600:]


@contextmanager
def _demo_roles(world: Any, ev: Evidence, directory: Path) -> Iterator[None]:
    try:
        _org_roles(world, ev, directory)
        yield
    finally:
        tenant = world.tenants[A]
        out = seed.import_org(A, Path(tenant["org_file"]), tenant["links"], force=True)
        ev.step("Restore baseline organization through xt org import", out.strip())


def _audit_material(ev: Evidence, directory: Path) -> None:
    """Anchor outside Postgres, then verify the signature, chain and security catalog."""
    directory.mkdir(mode=0o777)
    directory.chmod(0o777)
    checkpoint_dir = directory / "cp"
    checkpoint_dir.mkdir(mode=0o777)
    checkpoint_dir.chmod(0o777)
    signing_key = Ed25519PrivateKey.generate()
    (directory / "sign.pem").write_bytes(
        signing_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    (directory / "verify.pem").write_bytes(
        signing_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    for name in ("sign.pem", "verify.pem"):
        (directory / name).chmod(0o644)
    env = {
        "XT_AUDIT_CHECKPOINT_DIR": "/audit/cp",
        "XT_AUDIT_SIGNING_KEY_FILE": "/audit/sign.pem",
        "XT_AUDIT_SIGNING_KEY_ID": "e13-demo",
        "XT_AUDIT_VERIFY_KEY_FILE": "/audit/verify.pem",
    }
    flags = ["run", "--rm", "--no-deps", "-T", "--entrypoint", "xt", "-v", f"{directory}:/audit"]
    for name in env:
        flags += ["-e", name]
    for args in (
        ["checkpoint", "--label", "e13-demo"],
        ["verify", "--against-checkpoint", "latest"],
    ):
        result = compose.dc([*flags, "xt-api", "audit", *args], env=env, check=False, timeout=300)
        output = (result.stdout or "") + (result.stderr or "")
        ev.step(f"Final xt audit {' '.join(args)}", {"rc": result.returncode, "output": output})
        assert result.returncode == 0, output[-1600:]
        if args[0] == "verify":
            assert "toàn vẹn" in output


def _audit(ev: Evidence, directory: Path) -> None:
    try:
        _audit_material(ev, directory)
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def _sop(world: Any, ev: Evidence, requester: dict[str, Any], tag: str) -> str:
    # tai_lieu is the product document register. Its file reference is backed by
    # actual uploaded bytes, not a made-up UUID or a replacement for object storage.
    token = world.token(
        requester,
        client=HEADLESS_CLIENT,
        scope="openid xt-files",
        redirect="http://127.0.0.1:41813/callback",
    )
    with net.client() as client:
        uploaded = client.post(
            f"{world.s.api}/files/v1",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": (f"sop-xuat-kho-{tag}.txt", SOP.encode(), "text/plain")},
        )
        ev.step(
            "SOP upload to real Files service",
            {
                "status": uploaded.status_code,
                "body": uploaded.json()
                if uploaded.status_code in (200, 201)
                else uploaded.text[:800],
            },
        )
        assert uploaded.status_code in (200, 201), uploaded.text[:800]
        fid = uploaded.json()["data"]["file"]["id"]
        content = client.get(
            f"{world.s.api}/files/v1/{fid}/content", headers={"Authorization": f"Bearer {token}"}
        )
        ev.step(
            "SOP download from real Files service",
            {
                "status": content.status_code,
                "bytes": len(content.content),
                "error": content.text[:800] if content.status_code != 200 else None,
            },
        )
        assert content.status_code == 200 and content.content == SOP.encode()
    title = f"SOP xuất kho {tag}"
    document = _create(
        world,
        ev,
        requester,
        "tai_lieu",
        {
            "tieu_de": title,
            "tep": f"file:{fid}",
            "loai": "khac",
            "ghi_chu": SOP,
        },
    )
    found = _search(
        world,
        ev,
        requester,
        {
            "model_id": "tai_lieu",
            "where": {"tieu_de": title},
        },
    )
    assert [r["ref"] for r in found] == [document["ref"]]
    record = _call(world, ev, requester, "get_record", {"ref": found[0]["ref"]})["record"]
    assert record["values"]["ghi_chu"] == SOP
    assert record["values"]["tep"] == f"file:{fid}"
    ev.step(
        "SOP source retained for the stock request",
        {
            "ref": document["ref"],
            "file_ref": f"file:{fid}",
            "revision": record["revision"],
            "model_revision": record["model_revision"],
            "policy": SOP,
        },
    )
    return str(document["ref"])


def _approve(
    world: Any, ev: Evidence, manager: dict[str, Any], ref: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    inbox = _call(world, ev, manager, "get_inbox", {"kinds": ["approval"]})
    item = next((row for row in inbox["items"] if row["ref"] == ref), None)
    assert item, f"manager inbox missing {ref}"
    args = {
        "ref": ref,
        "decision": "approve",
        "expected_revision": item["revision"],
        "idempotency_key": key("e13-approve"),
    }
    pending = world.call(manager, "decide_request", args)
    ev.step("Manager agent requests approval", pending)
    assert pending.get("ok") is False and err(pending).get("code") == "confirmation_required"
    url = err(pending)["details"]["confirm_url"]
    assert url.startswith(f"{world.s.desk}/desk/confirm/")
    confirmation = world.confirm(url, manager)
    ev.step("Manager confirms in Desk with Keycloak password + TOTP", confirmation)
    assert confirmation.get("stage") == "decided" and confirmation.get("status") == 200
    assert "Đã thực hiện" in confirmation.get("body", "")
    done = world.call(manager, "decide_request", args)
    ev.step("Same idempotency key retrieves confirmed decision", done)
    assert done.get("ok") is True, dumps(done)[:1200]
    assert done["meta"]["receipt"]["replayed"] is True
    approved = _call(world, ev, manager, "get_record", {"ref": ref})["record"]
    assert approved["state"] == "approved"
    return args, dict(done["data"])


def _request(world: Any, ev: Evidence, requester: dict[str, Any], values: dict[str, Any]) -> str:
    created = _create(world, ev, requester, "stock_request", values)
    return _submit(world, ev, requester, str(created["ref"]))


def _submit(world: Any, ev: Evidence, requester: dict[str, Any], ref: str) -> str:
    submitted = _call(
        world,
        ev,
        requester,
        "submit_request",
        {
            "ref": ref,
            "note": "Đã đọc SOP xuất kho",
            "idempotency_key": key("e13-submit"),
        },
    )
    assert [step["step"] for step in submitted["route"]] == ["manager"]
    return ref


def _warehouse(
    world: Any, ev: Evidence, requester: dict[str, Any], manager: dict[str, Any], sop: str, tag: str
) -> str:
    warehouse = _create(
        world,
        ev,
        manager,
        "warehouse",
        {
            "code": f"E13-{tag}",
            "name": f"Kho demo {tag}",
        },
    )
    location = _create(
        world,
        ev,
        manager,
        "warehouse_location",
        {
            "code": f"E13-{tag}",
            "name": "Kệ A",
            "warehouse": warehouse["ref"],
        },
    )
    item = _create(
        world,
        ev,
        manager,
        "stock_item",
        {
            "sku": f"E13-{tag}",
            "name": "Vật tư demo",
            "unit": "cái",
        },
    )
    today = date.today().isoformat()
    line = {
        "item": item["ref"].split(":", 1)[1],
        "location": location["ref"].split(":", 1)[1],
        "quantity_milli": 10000,
    }
    common = {
        "warehouse": warehouse["ref"],
        "doc_date": today,
        "lines": [line],
        "note": f"Nguồn quy định: {sop}",
    }
    # Keep the first stock write faithful to the staff demo: an issue draft via
    # the real agent client. E13-BUG-01 is narrowly raised at this product boundary.
    issue_draft = _create(
        world,
        ev,
        requester,
        "stock_request",
        {
            **common,
            "code": f"E13-OUT-{tag}",
            "kind": "issue",
            "lines": [{**line, "quantity_milli": 4000}],
        },
    )
    assert issue_draft["state"] == "draft"
    # A manager is normally eligible to approve stock. Their own submitted slip
    # must still hit the creator SoD guard, independently of a missing-role denial.
    self_ref = _request(
        world,
        ev,
        manager,
        {
            **common,
            "code": f"E13-SELF-{tag}",
            "kind": "issue",
        },
    )
    self_record = _call(world, ev, manager, "get_record", {"ref": self_ref})["record"]
    self_denied = world.call(
        manager,
        "decide_request",
        {
            "ref": self_ref,
            "decision": "approve",
            "expected_revision": self_record["revision"],
            "idempotency_key": key("e13-manager-self"),
        },
    )
    ev.step("Eligible warehouse manager cannot approve their own slip (SoD)", self_denied)
    assert err(self_denied).get("code") == "access_denied"
    assert err(self_denied).get("details", {}).get("reason") == "self_approval"
    assert (
        _search(
            world,
            ev,
            manager,
            {
                "model_id": "stock_movement",
                "where": {"request": self_ref},
            },
        )
        == []
    )
    received = _request(
        world, ev, requester, {**common, "code": f"E13-IN-{tag}", "kind": "receive"}
    )
    _approve(world, ev, manager, received)
    before = _search(
        world,
        ev,
        requester,
        {
            "model_id": "stock_balance",
            "where": {"warehouse": warehouse["ref"]},
        },
    )
    assert len(before) == 1 and int(before[0]["values"]["quantity_milli"]) == 10000
    issued = _submit(world, ev, requester, str(issue_draft["ref"]))
    current = _call(world, ev, requester, "get_record", {"ref": issued})["record"]
    own = world.call(
        requester,
        "decide_request",
        {
            "ref": issued,
            "decision": "approve",
            "expected_revision": current["revision"],
            "idempotency_key": key("e13-self"),
        },
    )
    ev.step("Creator cannot self approve the stock issue", own)
    assert own.get("ok") is False and err(own).get("code") == "access_denied"
    assert (
        _call(world, ev, requester, "get_record", {"ref": issued})["record"]["state"] == "in_review"
    )
    movement_query = {"model_id": "stock_movement", "where": {"request": issued}}
    assert _search(world, ev, requester, movement_query) == []
    args, confirmed_data = _approve(world, ev, manager, issued)
    movements = _search(world, ev, requester, movement_query)
    assert len(movements) == 1
    assert int(movements[0]["values"]["quantity_milli"]) == -4000
    assert movements[0]["values"]["request"] == issued
    replay = world.call(manager, "decide_request", args)
    ev.step("Replay same manager decision key again", replay)
    assert replay.get("ok") is True and replay["meta"]["receipt"]["replayed"] is True
    assert replay["data"] == confirmed_data
    assert _search(world, ev, requester, movement_query) == movements
    balances = _search(
        world,
        ev,
        requester,
        {
            "model_id": "stock_balance",
            "where": {"warehouse": warehouse["ref"]},
        },
    )
    assert len(balances) == 1 and int(balances[0]["values"]["quantity_milli"]) == 6000
    aggregate = _call(
        world,
        ev,
        requester,
        "aggregate_records",
        {
            "model_id": "stock_movement",
            "metrics": ["count", "sum:quantity_milli"],
            "where": {"warehouse": warehouse["ref"]},
            "group_by": ["warehouse"],
            "time_bucket": "month",
            "date_field": "occurred_on",
            "period": today[:7],
        },
    )
    assert aggregate["scope"] == "authorized_records"
    assert aggregate["rows"] == [
        {
            "keys": {"warehouse": warehouse["ref"], "occurred_on:month": f"{today[:7]}-01"},
            "values": {"count": 2, "sum:quantity_milli": "6000"},
        }
    ]
    explanation = _call(
        world,
        ev,
        requester,
        "get_history",
        {
            "ref": aggregate["lineage_ref"],
            "view": "explain",
        },
    )["explanation"]
    query = explanation["query"]
    assert query["replay"] == "current_data" and query["historical_snapshot"] is False
    assert query["as_of"] == aggregate["as_of"]
    assert query["model_id"] == "stock_movement" and query["group_by"] == ["warehouse"]
    assert query["metrics"] == ["count", "sum:quantity_milli"]
    sources = _search(world, ev, requester, query["search_records"])
    assert len(sources) == 2
    assert {r["values"]["request"] for r in sources} == {received, issued}
    assert sum(int(r["values"]["quantity_milli"]) for r in sources) == 6000
    assert {r["ref"] for r in movements} <= {r["ref"] for r in sources}
    draft = _create(
        world,
        ev,
        requester,
        "stock_request",
        {
            **common,
            "code": f"E13-DRAFT-{tag}",
            "kind": "issue",
            "lines": [{**line, "quantity_milli": 1000}],
        },
    )
    assert draft["state"] == "draft"
    assert (
        _search(
            world,
            ev,
            requester,
            {
                "model_id": "stock_movement",
                "where": {"request": draft["ref"]},
            },
        )
        == []
    )
    assert (
        _search(
            world,
            ev,
            requester,
            {
                "model_id": "stock_balance",
                "where": {"warehouse": warehouse["ref"]},
            },
        )
        == balances
    )
    ev.step(
        "Warehouse, dashboard aggregate and replayed lineage agree",
        {
            "stock_balance": 6000,
            "aggregate": aggregate,
            "source_refs": [r["ref"] for r in sources],
            "sop_ref": sop,
            "stock_request_ref": issued,
        },
    )
    return issued


def _crm(
    world: Any, ev: Evidence, requester: dict[str, Any], admin: dict[str, Any], tag: str
) -> str:
    _pack(world, ev, "install")
    # Re-install is also real and supported on a retained stack. Recover its lifecycle
    # state explicitly rather than assuming every test run starts with a fresh volume.
    workspace = _call(world, ev, admin, "inspect_workspace", {"include": ["apps", "models"]})
    crm = next(app for app in workspace["apps"] if app["id"] == "crm")
    if crm["status"] == "disabled":
        _pack(world, ev, "enable")
    customer = _create(
        world,
        ev,
        requester,
        "khach_hang",
        {
            "code": f"E13-{tag}",
            "name": f"Khách hàng demo {tag}",
            "kind": "doanh-nghiep",
        },
    )
    _pack(world, ev, "disable")
    try:
        unavailable = world.call(
            requester,
            "create_record",
            {
                "model_id": "khach_hang",
                "values": {"code": f"E13-OFF-{tag}", "name": "Bị chặn"},
                "idempotency_key": key("e13-crm-off"),
            },
        )
        ev.step("Disabled CRM blocks writes through MCP", unavailable)
        assert unavailable.get("ok") is False
        assert err(unavailable).get("code") == "capability_unavailable"
        assert err(unavailable).get("details", {}).get("reason") == "app_disabled"
        retained = _call(world, ev, admin, "get_record", {"ref": customer["ref"]})["record"]
        assert retained["values"] == customer["values"]
        hidden = _call(world, ev, requester, "inspect_workspace", {"include": ["apps", "models"]})
        assert "crm" not in {app["id"] for app in hidden["apps"]}
        assert "khach_hang" not in {model["id"] for model in hidden["models"]}
    finally:
        _pack(world, ev, "enable")
    restored = _call(world, ev, requester, "get_record", {"ref": customer["ref"]})["record"]
    assert restored["ref"] == customer["ref"] and restored["values"] == customer["values"]
    visible = _call(world, ev, requester, "inspect_workspace", {"include": ["apps", "models"]})
    assert "crm" in {app["id"] for app in visible["apps"]}
    assert "khach_hang" in {model["id"] for model in visible["models"]}
    return str(customer["ref"])


def _wrong_tenant(world: Any, ev: Evidence, ref: str, private_text: str) -> None:
    outsider = world.by_key(B, B_EMPLOYEE)
    denied = world.call(outsider, "get_record", {"ref": ref})
    absent = world.call(outsider, "get_record", {"ref": f"{ref.split(':', 1)[0]}:{uuid.uuid4()}"})
    ev.step("Wrong tenant cannot read the source record", denied)
    ev.step("Wrong tenant sees the same denial for an absent record", absent)
    assert denied.get("ok") is False
    assert err(denied).get("code") in {"access_denied", "not_found"}
    assert err(denied).get("code") == err(absent).get("code")
    assert err(denied).get("message") == err(absent).get("message")
    assert private_text not in dumps(denied)


@pytest.mark.xfail(
    strict=True,
    raises=StockAgentOwnershipBug,
    reason="E13-BUG-01: real agent cannot create its person's stock issue (owner IAM predicate)",
)
def test_demo_story(ev: Evidence, world: Any, tmp_path: Path) -> None:
    """A complete, reproducible §9 story with lineage, Tier 3 and final audit proof."""
    requester, manager, admin = (
        world.by_key(A, person) for person in (REQUESTER, DEPT_HEAD_ACCT, DIRECTOR)
    )
    tag = key("demo")[-8:]
    with _demo_roles(world, ev, tmp_path):
        requester_me = _call(world, ev, requester, "inspect_workspace", {"include": ["me"]})["me"]
        manager_me = _call(world, ev, manager, "inspect_workspace", {"include": ["me"]})["me"]
        assert "sales" in requester_me["roles"]
        assert "warehouse_manager" in manager_me["roles"]
        assert requester_me["ref"] != manager_me["ref"]
        sop = _sop(world, ev, requester, tag)
        issued = _warehouse(world, ev, requester, manager, sop, tag)
        _crm(world, ev, requester, admin, tag)
        _wrong_tenant(world, ev, issued, f"E13-OUT-{tag}")
        forbidden = world.call(
            requester,
            "create_record",
            {
                "model_id": "stock_movement",
                "values": {},
                "idempotency_key": key("e13-forbidden"),
            },
        )
        ev.step("Direct ledger mutation is forbidden", forbidden)
        assert forbidden.get("ok") is False and err(forbidden).get("code") == "access_denied"
        history = _call(world, ev, requester, "get_history", {"ref": issued, "view": "audit"})
        assert history["chain_verified"] is True
        created_event = next(e for e in history["events"] if e["action"] == "stock_request.created")
        approved_event = next(e for e in history["events"] if e.get("to_state") == "approved")
        assert created_event["actor"]["ref"] == requester_me["ref"]
        assert approved_event["actor"]["ref"] == manager_me["ref"]
        assert isinstance(created_event["revision"], int)
        assert approved_event["revision"] > created_event["revision"]
    _audit(ev, tmp_path / "audit")


def test_sop_document_source(ev: Evidence, world: Any, tmp_path: Path) -> None:
    """SOP proof remains independent while E13-BUG-01 blocks the stock story."""
    with _demo_roles(world, ev, tmp_path):
        _sop(world, ev, world.by_key(A, REQUESTER), key("sop")[-8:])


def test_crm_lifecycle_tenant_and_final_audit(ev: Evidence, world: Any, tmp_path: Path) -> None:
    """Tier 3 and final integrity still execute when warehouse ownership is broken."""
    tag = key("crm")[-8:]
    with _demo_roles(world, ev, tmp_path):
        requester, admin = world.by_key(A, REQUESTER), world.by_key(A, DIRECTOR)
        ref = _crm(world, ev, requester, admin, tag)
        _wrong_tenant(world, ev, ref, f"Khách hàng demo {tag}")
        history = _call(world, ev, requester, "get_history", {"ref": ref, "view": "audit"})
        assert history["chain_verified"] is True and history["events"]
    _audit(ev, tmp_path / "audit")
