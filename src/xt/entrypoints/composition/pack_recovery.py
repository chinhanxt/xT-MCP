# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Retry recorded process operations as xt_app, with one transaction per incident.

The caller holds the tenant pack session lock, and has committed the enabled status.
A failed retry rolls back every business write before its attempt/audit is recorded.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from xt.adapters.db.connection import AuditKey, Database
from xt.adapters.db.integration import TenantProfile
from xt.adapters.db.process_incidents import app_incidents_for
from xt.adapters.db.workflow_bridge import PgWorkflowStore
from xt.adapters.workflow.bpmn.spiff import SpiffProcessEngine
from xt.domain.model.context import ModelActor, TenantRef
from xt.domain.model.errors import ModelError
from xt.entrypoints.composition.process import ProcessContext, ProcessToolAdapter
from xt.entrypoints.composition.runtime import NIL, WORKFLOW_ROLE, Caller, Runtime
from xt.entrypoints.composition.security import ToolAccessGuard


def _authorize_replay(
    rt: Runtime, actor: ModelActor, profile: TenantProfile, iid: str, replay: Mapping[str, Any]
) -> None:
    """Revalidate current human IAM before replaying a recorded tool action."""
    if replay.get("operation") != "act":
        return
    denied = ModelError(
        "access_denied",
        "Current permission does not allow incident replay",
        details={"reason": "retry_permission_denied"},
    )
    person = rt.store.person(actor, actor.actor_id)
    if person is None or not person.active:
        raise denied
    targets: list[tuple[str, str]] = []
    if "decision" in replay:
        decision = replay["decision"]
        if not isinstance(decision, Mapping) or not isinstance(decision.get("ref"), str):
            raise denied
        targets.append(("request.decide", decision["ref"]))
    if "record_patch" in replay:
        patch = replay["record_patch"]
        if not isinstance(patch, Mapping) or not all(
            isinstance(patch.get(key), str) and patch[key] for key in ("model", "record_id")
        ):
            raise denied
        targets.append(("record.transition", f"{patch['model']}:{patch['record_id']}"))
    if not targets:
        ref = PgWorkflowStore(rt.uow).ref_for(iid)
        if ref is not None:
            binding = rt.store.bindings(actor).get(ref.split(":", 1)[0])
            if binding is not None and binding.primitive == "request":
                targets.append(("request.decide", ref))
    if not targets:
        return  # Internal service actions retain the runtime's assignment and SoD checks.
    caller = Caller(profile.alias, profile, person, "xt-pack-enable", False, 0, (), actor)
    guard = ToolAccessGuard(rt)
    now = int(datetime.now(UTC).timestamp())
    for action, ref in targets:
        parts = ref.split(":")
        if len(parts) == 4 and parts[:2] == ["xrn", profile.alias]:
            parts = parts[2:]
        if len(parts) != 2:
            raise denied
        model, raw_id = parts
        try:
            rid = UUID(raw_id)
        except ValueError:
            raise denied from None
        resource = guard._record_attrs(caller, model, rid)
        if not guard.allows(
            caller, person, action, f"xrn:{profile.alias}:{model}:{rid}", now, resource
        ):
            raise denied


def _sync_request(
    rt: Runtime,
    actor: ModelActor,
    iid: str,
    result: Mapping[str, Any],
    *,
    expected_revision: int | None = None,
) -> None:
    """Mirror the engine outcome to its bound request inside the retry transaction."""
    ref = PgWorkflowStore(rt.uow).ref_for(iid)
    if ref is None:
        return
    model, raw_id = ref.split(":", 1)
    binding = rt.store.bindings(actor).get(model)
    if binding is None or binding.primitive != "request":
        return
    rid = UUID(raw_id)
    field = str(binding.config.get("state_field", "state"))
    state = ProcessToolAdapter._state_of(result, list(result["tasks"]))
    row = rt.records.get(actor, model, rid)["record"]
    if row.get(field) != state or expected_revision is not None:
        revision = int(row["revision"]) if expected_revision is None else expected_revision
        rt.records.workflow_write(actor, model, rid, revision, {field: state})


def _check_patch_revision(rt: Runtime, actor: ModelActor, replay: Mapping[str, Any]) -> None:
    for key in ("record_patch", "request_revision"):
        patch = replay.get(key)
        if patch:
            row = rt.records.get(actor, patch["model"], UUID(patch["record_id"]))["record"]
            if int(row["revision"]) != patch["expected_revision"]:
                raise ModelError(
                    "revision_conflict", "record revision changed since the failed step"
                )


def _finish_replay(rt: Runtime, actor: ModelActor, replay: Mapping[str, Any]) -> None:
    decision = replay.get("decision")
    if decision:
        rt.store.record_decision(
            actor,
            **{
                **decision,
                "approver": UUID(decision["approver"]),
                "requester": UUID(decision["requester"]) if decision["requester"] else None,
            },
        )
    patch = replay.get("record_patch")
    if patch:
        model, rid = patch["model"], UUID(patch["record_id"])
        row = rt.records.get(actor, model, rid)["record"]
        values = {**patch["values"], patch["state_field"]: patch["to_state"]}
        values = {key: value for key, value in values.items() if row.get(key) != value}
        if values:
            rt.records.workflow_write(actor, model, rid, int(row["revision"]), values)


def recover_app_incidents(
    app_dsn: str,
    *,
    tenant: TenantRef,
    pack: str,
    models: Sequence[str],
    incidents: Sequence[Mapping[str, str]],
    approved_by: str,
    digest: str,
    audit_key: AuditKey | None = None,
) -> dict[str, int]:
    counts = {"retried": 0, "failed": 0, "skipped": 0}
    db = Database(app_dsn, min_size=0, max_size=2, audit_key=audit_key)
    rt = Runtime(app_db=db, migrator_dsn=None, audit_key=audit_key)
    actor = ModelActor(tenant, NIL, (WORKFLOW_ROLE,), client_azp="xt-pack-enable", trace_id=digest)
    try:
        with rt.uow.scope(actor):
            profile = rt.uow.run(lambda: rt.store.profile(actor))
            if profile is None:
                raise LookupError("tenant has no profile")
            store = PgWorkflowStore(rt.uow)
            for candidate in incidents:
                iid, incident_id = candidate["instance"], candidate["incident"]

                def retry(candidate: Mapping[str, str] = candidate) -> bool:
                    rec = store.get_for_update(candidate["instance"])
                    if (
                        rec is None
                        or rec.status != "running"
                        or dict(candidate)
                        not in app_incidents_for(candidate["instance"], rec.state, pack, models)
                    ):
                        return False
                    incident = next(
                        i
                        for i in rec.state["meta"]["incidents"]
                        if i["id"] == candidate["incident"]
                    )
                    replay = incident["retry"]
                    try:
                        person_id = UUID(replay["actor"]) if replay["operation"] == "act" else NIL
                    except (KeyError, TypeError, ValueError):
                        return False
                    original = ModelActor(
                        tenant,
                        person_id,
                        (WORKFLOW_ROLE,),
                        client_azp="xt-pack-enable",
                        trace_id=digest,
                    )
                    with rt.uow.tenant_tx(original):
                        pc = ProcessContext(rt, SpiffProcessEngine(), original, profile.timezone)
                        _authorize_replay(rt, original, profile, candidate["instance"], replay)
                        _check_patch_revision(rt, original, replay)
                        result = pc.service().retry_incident(
                            candidate["instance"], candidate["incident"], retried_by=approved_by
                        )
                        if result["retried"]:
                            _finish_replay(rt, original, replay)
                            _sync_request(
                                rt,
                                original,
                                candidate["instance"],
                                result,
                                expected_revision=replay.get("request_revision", {}).get(
                                    "expected_revision"
                                ),
                            )
                    return bool(result["retried"])

                try:
                    retried = rt.uow.run(retry)
                except Exception as error:
                    # The retry transaction is already rolled back, including partial service ops.
                    # Persist a safe structured cause, never exception messages or replay inputs.
                    details = getattr(error, "incident", None) or getattr(error, "details", {})
                    code = details.get("code", getattr(error, "code", "internal_error"))
                    reason = details.get("reason", getattr(error, "reason", None)) or "retry_failed"
                    failure = {"code": str(getattr(code, "value", code)), "reason": str(reason)}

                    def failed(
                        iid: str = iid,
                        incident_id: str = incident_id,
                        failure: Mapping[str, str] = failure,
                    ) -> None:
                        rec = store.get_for_update(iid)
                        if rec is not None:
                            for inc in rec.state.get("meta", {}).get("incidents", []):
                                if inc.get("id") == incident_id and inc.get("status") == "open":
                                    inc["attempts"] = int(inc.get("attempts", 0)) + 1
                                    inc["retried_by"] = approved_by
                                    store.save(iid, rec)
                                    break
                        rt.store.process_event(
                            actor,
                            iid,
                            "incident.retry",
                            {
                                "incident": incident_id,
                                "result": "failed",
                                "pack": pack,
                                "retried_by": approved_by,
                                "digest": digest,
                                **failure,
                            },
                        )

                    rt.uow.run(failed)
                    counts["failed"] += 1
                else:
                    counts["retried" if retried else "skipped"] += 1
    finally:
        rt.close()
        db.close()
    return counts
