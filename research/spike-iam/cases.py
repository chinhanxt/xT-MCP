# SPDX-License-Identifier: Apache-2.0
"""Shared acceptance matrix (25 original + 7 added on review), negative variants per case."""
from copy import deepcopy
import sys
import threading
from dataclasses import replace
from contracts import Actor, Request, Host, PolicyStore, fingerprint


def condition(left, op, right):
    return {'left': left, 'op': op, 'right': right}

def statement(sid='base', effect='Allow', subjects=None, actions=None, resources=None, conditions=None):
    return {'id': sid, 'effect': effect, 'subjects': subjects or ['role:staff'],
            'actions': actions or ['record.*'], 'resources': resources or ['xrn:acme:*:*'],
            'conditions': conditions or []}

def document(*statements):
    return {'version': '1', 'tenant': 'acme', 'statements': list(statements)}

BASE = document(
    statement('own', conditions=[condition('resource.owner', 'eq', {'ref': 'subject.id'})]),
    statement('team', subjects=['role:lead'], conditions=[condition('resource.team', 'eq', {'ref': 'subject.team'})]),
    statement('department', subjects=['role:manager'], conditions=[condition('resource.department', 'eq', {'ref': 'subject.department'})]),
    statement('approve', subjects=['role:approver'], actions=['record.approve'], conditions=[condition('resource.amount', 'le', {'ref': 'subject.limit'})]),
    statement('agent', subjects=['role:worker'], conditions=[condition('resource.amount', 'le', {'ref': 'subject.limit'})]),
    statement('admin', subjects=['role:iam_admin'], actions=['*'], resources=['*']),
)
BOSS = Actor(id='boss', roles=('approver', 'staff'), limit=5000)
ADMIN = Actor(id='admin', roles=('iam_admin', 'approver'), limit=9000)
BOT = Actor(id='bot', kind='agent', roles=('worker',), behalf='boss', limit=5000)
OTHER = {'owner': 'bob', 'creator': 'carol', 'amount': 5000, 'team': 'red', 'department': 'sales'}


def host(cls, doc=BASE, **kwargs):
    return Host(cls(deepcopy(doc)), actors={'boss': BOSS, 'admin': ADMIN, 'bot': BOT, 'alice': Actor()}, **kwargs)

def check(h, req, allowed, reason=None, sid=None):
    d = h.evaluate(req)
    assert d.allowed is allowed, d
    if reason:
        assert d.reason == reason, d
    if sid:
        assert sid in d.statements, d
    return d


def receipt(req, by='admin'):
    return {'by': by, 'expires': 200, 'binding': (
        req.actor.id, req.actor.behalf, req.actor.tenant, req.action, req.xrn, req.revision, fingerprint(req.resource))}


def run_case(name, cls):
    req = Request()
    if name == '01_exact_allow':
        check(host(cls), req, True, 'allow', 'own')
    elif name == '02_default_deny':
        check(host(cls), replace(req, actor=Actor(roles=())), False, 'implicit_deny')
        check(host(cls, document()), req, False, 'implicit_deny')
    elif name == '03_deny_override_order':
        a = statement('allow'); d = statement('block', effect='Deny', actions=['record.update'])
        for rules in ((a, d), (d, a)):
            check(host(cls, document(*rules)), req, False, 'explicit_deny', 'block')
    elif name == '04_action_wildcard':
        check(host(cls, document(statement(actions=['record.u*']))), req, True)
        check(host(cls, document(statement(actions=['record.r*']))), req, False)
    elif name == '05_resource_wildcard_literal':
        check(host(cls, document(statement(resources=['xrn:acme:exp*:4*']))), req, True)
        for action, xrn in [('record.update.extra.record.update', req.xrn), (req.action, req.xrn + 'extra' + req.xrn), ('Record.update', req.xrn)]:
            d = cls(document(statement(actions=[req.action], resources=[req.xrn]))).evaluate(replace(req, action=action, xrn=xrn))
            assert not d.allowed, d
    elif name == '06_tenant_and_xrn_boundary':
        h = host(cls, document(statement(subjects=['*'], actions=['*'], resources=['*'])))
        for xrn, reason in [('xrn:other:expense:42', 'tenant_boundary'), ('xrn:acme:expense:42:extra', 'invalid_xrn'), ('xrn:acme:expense:*', 'invalid_xrn')]:
            check(h, replace(req, xrn=xrn), False, reason)
        check(h, replace(req, actor=Actor(tenant='other'), xrn='xrn:other:expense:42'), False, 'tenant_boundary')
    elif name == '07_rbac_domain_and_revoke':
        h = host(cls)
        check(h, req, True)
        check(h, replace(req, actor=replace(req.actor, roles=())), False)
        check(h, req, True)
        check(h, replace(req, actor=Actor(tenant='other')), False, 'tenant_boundary')
    elif name == '08_abac_inclusive_limit':
        h = host(cls, document(statement(conditions=[condition('resource.amount', 'le', {'ref': 'subject.limit'})])))
        check(h, req, True)
        check(h, replace(req, resource={**req.resource, 'amount': 5001}), False)
        why = h.backend.explain(replace(req, resource={**req.resource, 'amount': 5001}))
        assert why['reason'] == 'implicit_deny'
        assert why['trace'][0]['conditions'][0] == {'field': 'resource.amount', 'op': 'le', 'actual': 5001, 'expected': 5000, 'passed': False}
    elif name == '09_abac_missing_bad_type':
        h = host(cls, document(statement(conditions=[condition('resource.amount', 'le', {'ref': 'subject.limit'})])))
        for bad in (None, '1', True, float('nan'), float('inf')):
            check(h, replace(req, resource={**req.resource, 'amount': bad}), False)
        check(h, replace(req, resource={}), False)
    elif name == '10_own_scope':
        check(host(cls), req, True, sid='own')
        check(host(cls), replace(req, resource=OTHER), False)
    elif name == '11_team_scope':
        h = host(cls); r = replace(req, actor=Actor(roles=('lead',)), resource=OTHER)
        check(h, r, True, sid='team')
        check(h, replace(r, resource={**OTHER, 'team': 'blue'}), False)
    elif name == '12_department_scope':
        h = host(cls); r = replace(req, actor=Actor(roles=('manager',)), resource=OTHER)
        check(h, r, True, sid='department')
        check(h, replace(r, resource={**OTHER, 'department': 'hr'}), False)
    elif name == '13_agent_L0':
        bot = replace(BOT, autonomy=0, behalf='alice')
        h = host(cls)
        check(h, replace(req, actor=bot, action='record.read'), True)
        check(h, replace(req, actor=bot), False, 'autonomy_ceiling')
    elif name == '14_agent_L1':
        bot = replace(BOT, autonomy=1, behalf='alice')
        check(host(cls), replace(req, actor=bot, action='record.create'), True)
        check(host(cls), replace(req, actor=bot, action='record.approve', resource=OTHER), False, 'autonomy_ceiling')
    elif name == '15_agent_L2_human_receipt':
        r = replace(req, actor=replace(BOT, autonomy=2), action='record.approve', resource=OTHER, approval='ok')
        check(host(cls), replace(r, approval=None), False, 'approval_required')
        h = host(cls, approvals={'ok': receipt(r)})
        check(h, r, True)
        check(h, replace(r, revision=2), False, 'approval_binding')
        check(h, replace(r, resource={**OTHER, 'amount': 1}), False, 'approval_binding')
        check(h, replace(r, now=200), False, 'approval_binding')
    elif name == '16_agent_L3_human_receipt':
        bot = replace(BOT, behalf='admin')
        r = replace(req, actor=bot, action='record.delete', resource=OTHER, approval='ok')
        check(host(cls), replace(r, approval=None), False, 'approval_required')
        # An independent human with current permission, never a boolean flag.
        check(host(cls, approvals={'ok': receipt(r, by='boss')}), r, False, 'approver_permission')
        h = host(cls, approvals={'ok': receipt(r, by='admin')})
        check(h, r, False, 'invalid_approver')  # represented user cannot rubber stamp itself
        h.actors['reviewer'] = replace(ADMIN, id='reviewer')
        h.approvals['ok'] = receipt(r, by='reviewer')
        check(h, r, True)
        h.approvals['ok']['by'] = 'bot'
        check(h, r, False, 'invalid_approver')
    elif name == '17_agent_self_approval':
        r = replace(req, actor=BOT, action='record.approve', resource={**OTHER, 'creator': 'bot'})
        check(host(cls), r, False, 'self_approval')
    elif name == '18_behalf_self_approval':
        r = replace(req, actor=BOT, action='record.approve', resource={**OTHER, 'creator': 'boss'})
        check(host(cls), r, False, 'self_approval')
        check(host(cls), replace(r, actor=BOSS), False, 'self_approval')
    elif name == '19_agent_above_limit':
        r = replace(req, actor=BOT, action='record.approve', resource={**OTHER, 'amount': 5001}, approval='ok')
        check(host(cls, approvals={'ok': receipt(r)}), r, False, 'implicit_deny')
        # Agent itself limited even when represented principal has a higher ceiling.
        bot = replace(BOT, behalf='admin', limit=10)
        r = replace(req, actor=bot, resource={**OTHER, 'amount': 11})
        check(host(cls), r, False, 'implicit_deny')
    elif name == '20_agent_intersection':
        r = replace(req, actor=replace(BOT, behalf='alice'), resource=OTHER)
        check(host(cls), r, False, 'behalf_permission')
        check(host(cls), replace(r, actor=replace(BOT, behalf='missing')), False, 'invalid_behalf')
        h = host(cls); h.actors['boss'] = replace(BOSS, tenant='other')
        check(h, replace(req, actor=BOT), False, 'invalid_behalf')
    elif name in ('21_delegation_valid', '22_delegation_invalid', '23_delegation_attenuation'):
        grant = {'by': 'boss', 'to': 'alice', 'absent': True, 'revoked': False, 'start': 50, 'end': 150,
                 'action': 'record.approve', 'resource': 'xrn:acme:expense:*', 'limit': 5000}
        r = replace(req, action='record.approve', resource=OTHER, delegation='leave')
        h = host(cls, delegations={'leave': grant})
        if name == '21_delegation_valid':
            check(h, r, True, 'delegated_allow', 'approve')
            check(h, replace(r, delegation=None), False)
        elif name == '22_delegation_invalid':
            for key, val in [('end',100), ('start',101), ('revoked',True), ('absent',False), ('to','mallory'), ('by','missing')]:
                h.delegations['leave'] = {**grant, key: val}
                check(h, r, False, 'invalid_delegation')
            h.delegations['leave'] = grant
            h.actors['boss'] = replace(BOSS, tenant='other')
            check(h, r, False, 'invalid_delegation')
        else:
            check(h, replace(r, resource={**OTHER, 'amount':5001}), False, 'delegation_scope')
            check(h, replace(r, action='record.delete'), False, 'delegation_scope')
            h.actors['boss'] = replace(BOSS, limit=10)
            check(h, r, False, 'implicit_deny')
            deny = statement('recipient-deny', effect='Deny', subjects=['alice'], actions=['record.approve'])
            h = host(cls, document(*BASE['statements'], deny), delegations={'leave': grant})
            check(h, r, False, 'explicit_deny', 'recipient-deny')
            h = host(cls, delegations={'leave': grant})
            check(h, replace(r, resource={**OTHER, 'creator': 'boss'}), False, 'self_approval')
    elif name == '24_policy_human_lifecycle':
        store = PolicyStore(BASE)
        for action in (store.active, lambda: store.approve(BOT), lambda: store.approve(Actor()), lambda: store.approve(replace(ADMIN, tenant='other')), lambda: store.approve(replace(ADMIN, id='bot'))):
            try: action()
            except ValueError: pass
            else: raise AssertionError('unapproved policy accepted')
        store.approve(ADMIN)
        check(host(cls, store.active()), req, True)
        store.doc['statements'].append(statement('new', subjects=['*'], actions=['*']))
        try: store.active()
        except ValueError: pass
        else: raise AssertionError('changed policy accepted')
    elif name == '25_schema_fail_closed':
        docs = []
        d = deepcopy(BASE); d['version']='999'; docs.append(d)
        d = deepcopy(BASE); d['statements'][0]['condition']={'eval':'True'}; docs.append(d)
        d = deepcopy(BASE); d['statements'][0]['conditions']=[condition('resource.amount','eval','__import__("os")')]; docs.append(d)
        d = deepcopy(BASE); d['statements'][0]['effect']='Maybe'; docs.append(d)
        d = deepcopy(BASE); d['statements'][0]['conditions']=[condition('resource.secret','eq',1)]; docs.append(d)
        for d in docs:
            try: cls(d)
            except ValueError: pass
            else: raise AssertionError('invalid schema accepted')
        check(host(cls), replace(req, action='unknown.execute'), False, 'unknown_action')
    elif name == '26_deny_cross_subject_and_conditional':
        # Deny attached to a role beats Allow attached to the user id; conditional Deny only bites when true.
        allow = statement('user-allow', subjects=['alice'])
        deny = statement('role-deny', effect='Deny', subjects=['role:staff'], actions=['record.update'])
        check(host(cls, document(allow, deny)), req, False, 'explicit_deny', 'role-deny')
        cond = statement('hr-only', effect='Deny', subjects=['*'], conditions=[condition('resource.department', 'ne', 'sales')])
        h = host(cls, document(allow, cond))
        check(h, req, True, 'allow', 'user-allow')
        check(h, replace(req, resource={**req.resource, 'department': 'hr'}), False, 'explicit_deny', 'hr-only')
    elif name == '27_policy_tenant_mismatch':
        foreign = {**document(statement(subjects=['*'], actions=['*'], resources=['*'])), 'tenant': 'other'}
        check(host(cls, foreign), req, False, 'tenant_boundary')
        # A tenant document naming another tenant's xrn grants nothing there.
        h = host(cls, document(statement(subjects=['*'], actions=['*'], resources=['xrn:other:*'])))
        check(h, replace(req, xrn='xrn:other:expense:42'), False, 'tenant_boundary')
        check(h, req, False, 'implicit_deny')
    elif name == '28_wildcard_semantics':
        # '*' is a full-string glob that may cross ':' and '.'; every other char is literal.
        check(host(cls, document(statement(resources=['xrn:acme:*']))), req, True)
        check(host(cls, document(statement(resources=['xrn:acme:exp.nse:42']))), req, False)
        check(host(cls, document(statement(resources=['xrn:acme:expense:4']))), req, False)
        check(host(cls, document(statement(actions=['record.*']))), replace(req, action='record.update'), True)
        b = cls(document(statement(actions=['record.*'], resources=['*'])))
        assert not b.evaluate(replace(req, action='recordXupdate')).allowed
    elif name == '29_subject_namespace_collision':
        b = cls(document(statement('adm', subjects=['role:iam_admin'], actions=['*'], resources=['*'])))
        b.evaluate(replace(req, actor=Actor(id='role:staff', roles=('iam_admin',))))
        d = b.evaluate(replace(req, actor=Actor(id='eve', roles=('staff',)), resource=OTHER))
        assert not d.allowed, d
    elif name == '30_agent_delegation_cannot_bypass_limit':
        bot = replace(BOT, behalf='admin', limit=10)
        r = replace(req, actor=bot, resource={**OTHER, 'amount': 11}, delegation='d')
        grant = {'by': 'admin', 'to': 'bot', 'absent': True, 'revoked': False, 'start': 0, 'end': 999,
                 'action': 'record.update', 'resource': '*', 'limit': 5000}
        check(host(cls, delegations={'d': grant}), r, False, 'invalid_delegation')
    elif name == '31_receipt_bound_to_behalf':
        r = replace(req, actor=replace(BOT, autonomy=2), action='record.approve', resource=OTHER, approval='ok')
        h = host(cls, approvals={'ok': receipt(r)})
        h.actors['boss2'] = replace(BOSS, id='boss2')
        check(h, r, True)
        check(h, replace(r, actor=replace(r.actor, behalf='boss2')), False, 'approval_binding')
    elif name == '32_concurrent_evaluation_isolated':
        b = cls(BASE); bad = []
        def work(r, allowed, sid):
            for _ in range(2000):
                try:
                    d = b.evaluate(r)
                except Exception as e:
                    bad.append(repr(e)); return
                if d.allowed is not allowed or (sid and sid not in d.statements) or (not sid and d.statements):
                    bad.append(d); return
        jobs = [(req, True, 'own'), (replace(req, actor=Actor(id='zed', roles=()), resource=OTHER), False, None)]
        ts = [threading.Thread(target=work, args=jobs[i % 2]) for i in range(8)]
        old = sys.getswitchinterval(); sys.setswitchinterval(1e-6)  # force interleaving
        try:
            for t in ts: t.start()
            for t in ts: t.join()
        finally:
            sys.setswitchinterval(old)
        assert not bad, bad[:1]
    else:
        raise AssertionError(name)

CASES = [
 '01_exact_allow','02_default_deny','03_deny_override_order','04_action_wildcard',
 '05_resource_wildcard_literal','06_tenant_and_xrn_boundary','07_rbac_domain_and_revoke',
 '08_abac_inclusive_limit','09_abac_missing_bad_type','10_own_scope','11_team_scope',
 '12_department_scope','13_agent_L0','14_agent_L1','15_agent_L2_human_receipt',
 '16_agent_L3_human_receipt','17_agent_self_approval','18_behalf_self_approval',
 '19_agent_above_limit','20_agent_intersection','21_delegation_valid',
 '22_delegation_invalid','23_delegation_attenuation','24_policy_human_lifecycle',
 '25_schema_fail_closed','26_deny_cross_subject_and_conditional',
 '27_policy_tenant_mismatch','28_wildcard_semantics','29_subject_namespace_collision',
 '30_agent_delegation_cannot_bypass_limit','31_receipt_bound_to_behalf',
 '32_concurrent_evaluation_isolated']
