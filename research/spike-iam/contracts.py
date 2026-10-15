# SPDX-License-Identifier: Apache-2.0
"""Trusted host contracts; deliberately not a production authentication service."""
from copy import deepcopy
from dataclasses import dataclass, field, replace
import hashlib
import json
import math
import re
from policy_language import conditions, star, validate

# Operation levels are host metadata, never accepted from agent arguments.
LEVELS = {'record.read': 0, 'record.create': 1, 'record.update': 1,
          'record.approve': 2, 'record.delete': 3, 'policy.activate': 3}

@dataclass(frozen=True)
class Actor:
    id: str = 'alice'
    tenant: str = 'acme'
    kind: str = 'human'
    roles: tuple = ('staff',)
    team: str = 'red'
    department: str = 'sales'
    limit: int = 5000
    autonomy: int = 3
    behalf: str | None = None

@dataclass(frozen=True)
class Request:
    actor: Actor = field(default_factory=Actor)
    action: str = 'record.update'
    xrn: str = 'xrn:acme:expense:42'
    resource: dict = field(default_factory=lambda: {
        'owner': 'alice', 'creator': 'carol', 'amount': 5000,
        'team': 'red', 'department': 'sales'})
    revision: int = 1
    approval: str | None = None
    delegation: str | None = None
    now: int = 100

@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    statements: tuple = ()
    trace: tuple = ()


def fingerprint(doc):
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()

class PolicyStore:
    """In-memory approval lifecycle, binding human approval to immutable content."""
    def __init__(self, doc, proposer='bot'):
        self.doc = validate(doc)
        self.proposer = proposer
        self.approved_hash = None
    def approve(self, actor):
        if actor.kind != 'human' or actor.tenant != self.doc['tenant'] or 'iam_admin' not in actor.roles or actor.id == self.proposer:
            raise ValueError('human tenant admin other than proposer required')
        self.approved_hash = fingerprint(self.doc)
    def active(self):
        if self.approved_hash != fingerprint(self.doc):
            raise ValueError('unapproved/changed policy')
        return validate(self.doc)

class Host:
    """Same PEP for every backend; approvals/delegations resolved from host store."""
    def __init__(self, backend, actors=None, approvals=None, delegations=None):
        self.backend = backend
        self.actors = actors or {}
        self.approvals = approvals or {}
        self.delegations = delegations or {}
    def evaluate(self, req):
        # Spike expense row schema, validated from trusted host data, not agent input.
        if any(not isinstance(req.resource.get(k), str) or not req.resource[k] for k in ('owner', 'creator', 'team', 'department')) or type(req.resource.get('amount')) not in (int, float) or not math.isfinite(req.resource['amount']) or req.resource['amount'] < 0:
            return Decision(False, 'invalid_attributes')
        parts = req.xrn.split(':')
        if len(parts) != 4 or parts[0] != 'xrn' or any(not re.fullmatch(r'[A-Za-z0-9_-]+', p) for p in parts[1:]):
            return Decision(False, 'invalid_xrn')
        if parts[1] != req.actor.tenant or req.actor.tenant != self.backend.doc['tenant']:
            return Decision(False, 'tenant_boundary')
        if req.action not in LEVELS:
            return Decision(False, 'unknown_action')
        level = LEVELS[req.action]
        represented = self.actors.get(req.actor.behalf) if req.actor.kind == 'agent' else None
        if req.actor.kind == 'agent' and (represented is None or represented.kind != 'human' or represented.tenant != req.actor.tenant):
            return Decision(False, 'invalid_behalf')
        identities = {req.actor.id}
        if represented:
            identities.add(represented.id)
        if req.action == 'record.approve' and (req.resource.get('creator') in identities or req.resource.get('owner') in identities):
            return Decision(False, 'self_approval')
        if req.actor.kind == 'agent':
            if level > req.actor.autonomy:
                return Decision(False, 'autonomy_ceiling')
            if level >= 2:
                receipt = self.approvals.get(req.approval)
                if not receipt:
                    return Decision(False, 'approval_required')
                approver = self.actors.get(receipt.get('by'))
                if not approver or approver.kind != 'human' or approver.tenant != req.actor.tenant or approver.id in identities or approver.id in (req.resource.get('creator'), req.resource.get('owner')):
                    return Decision(False, 'invalid_approver')
                binding = (req.actor.id, req.actor.behalf, req.actor.tenant, req.action, req.xrn, req.revision, fingerprint(req.resource))
                if tuple(receipt.get('binding', ())) != binding or receipt.get('expires', 0) <= req.now:
                    return Decision(False, 'approval_binding')
                # Human approving this exact operation must also hold the permission now.
                authority = self.backend.evaluate(replace(req, actor=approver))
                if not authority.allowed:
                    return Decision(False, 'approver_permission', authority.statements, authority.trace)
        direct = self.backend.evaluate(req)
        if req.delegation and direct.reason != 'explicit_deny':
            grant = self.delegations.get(req.delegation)
            donor = self.actors.get(grant.get('by')) if grant else None
            # Delegation recipients are humans only; agents act through behalf + autonomy, never delegated authority.
            if req.actor.kind != 'human' or not grant or not donor or donor.kind != 'human' or donor.tenant != req.actor.tenant or grant.get('to') != req.actor.id or not grant.get('absent') or grant.get('revoked') or not grant.get('start', 0) <= req.now < grant.get('end', 0):
                return Decision(False, 'invalid_delegation')
            if req.action != grant['action'] or not star(grant['resource'], req.xrn) or type(req.resource.get('amount')) not in (int, float) or not 0 <= req.resource['amount'] <= grant['limit']:
                return Decision(False, 'delegation_scope')
            if req.action == 'record.approve' and donor.id in (req.resource.get('creator'), req.resource.get('owner')):
                return Decision(False, 'self_approval')
            delegated = self.backend.evaluate(replace(req, actor=donor))
            if delegated.allowed:
                direct = Decision(True, 'delegated_allow', delegated.statements, delegated.trace)
        if not direct.allowed:
            return direct
        if represented:
            owner = self.backend.evaluate(replace(req, actor=represented))
            if not owner.allowed:
                return Decision(False, 'behalf_permission', owner.statements, owner.trace)
        return direct
