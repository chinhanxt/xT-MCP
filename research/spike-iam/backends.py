# SPDX-License-Identifier: Apache-2.0
"""Real library adapters; native libraries decide effect aggregation."""
from itertools import product
from pathlib import Path
import casbin
import vakt
from vakt.rules.base import Rule
from contracts import Decision, conditions, star, validate
from mini import Mini, diagnostics

class Explain:
    def explain(self, req):
        d = self.evaluate(req)
        return {'allowed': d.allowed, 'reason': d.reason, 'statements': d.statements,
                'native_trace': d.trace, 'trace': diagnostics(self.doc, req)}

def subject_key(subject):
    # Users and roles live in disjoint casbin namespaces so a user id can never become a role node.
    return subject if subject == '*' or subject.startswith('role:') else 'user:' + subject

class Casbin(Explain):
    def __init__(self, doc):
        self.doc = validate(doc)
        self.by_id = {s['id']: s for s in self.doc['statements']}
        self.enforcer = casbin.Enforcer(str(Path(__file__).with_name('model.conf')))
        self.enforcer.add_function('star', star)
        self.enforcer.add_function('checks', lambda req, sid: conditions(req, self.by_id[sid]['conditions']))
        self.enforcer.enable_auto_save(False)
        self.loaded_roles = {}
        for s in self.doc['statements']:
            for subject, resource, action in product(s['subjects'], s['resources'], s['actions']):
                self.enforcer.add_policy(subject_key(subject), doc['tenant'], resource, action, s['id'], s['effect'].lower())
    def evaluate(self, req):
        # Trusted host role membership synchronized into g(subject, role, domain).
        identity = (req.actor.tenant, req.actor.id)
        if self.loaded_roles.get(identity) != req.actor.roles:
            self.enforcer.remove_filtered_grouping_policy(0, 'user:' + req.actor.id, '', req.actor.tenant)
            for role in req.actor.roles:
                self.enforcer.add_role_for_user_in_domain('user:' + req.actor.id, 'role:' + role, req.actor.tenant)
            self.loaded_roles[identity] = req.actor.roles
        allowed, rule = self.enforcer.enforce_ex('user:' + req.actor.id, req.actor.tenant, req.xrn, req.action, req)
        # enforce_ex exposes a single decisive policy; no-match has an empty list.
        ids = (rule[4],) if rule else ()
        reason = 'allow' if allowed else 'explicit_deny' if rule and rule[-1] == 'deny' else 'implicit_deny'
        return Decision(allowed, reason, ids, ({'native_rule': rule},))

class Glob(Rule):
    def __init__(self, patterns):
        self.patterns = patterns
    def satisfied(self, what, inquiry=None):
        return any(star(p, what) for p in self.patterns)

class Subjects(Rule):
    def __init__(self, names):
        self.names = names
    def satisfied(self, what, inquiry=None):
        return '*' in self.names or what.id in self.names or any('role:' + r in self.names for r in what.roles)

class Call:
    # Per-evaluation carrier: matched statement ids never leak between concurrent requests.
    def __init__(self, req):
        self.req = req
        self.hits = []

class ABAC(Rule):
    def __init__(self, expressions, sid):
        self.expressions = expressions
        self.sid = sid
    def satisfied(self, what, inquiry=None):
        matched = conditions(what.req, self.expressions)
        if matched:
            what.hits.append(self.sid)
        return matched

class Vakt(Explain):
    def __init__(self, doc):
        self.doc = validate(doc)
        self.storage = vakt.MemoryStorage()
        self.checker = vakt.RulesChecker()
        self.policies = []
        for s in self.doc['statements']:
            p = vakt.Policy(s['id'], subjects=[Subjects(s['subjects'])], actions=[Glob(s['actions'])],
                resources=[Glob(s['resources'])], context={'request': ABAC(s['conditions'], s['id'])},
                effect=vakt.ALLOW_ACCESS if s['effect'] == 'Allow' else vakt.DENY_ACCESS)
            self.storage.add(p)
            self.policies.append(p)
        self.guard = vakt.Guard(self.storage, self.checker)
    def evaluate(self, req):
        call = Call(req)
        inquiry = vakt.Inquiry(subject=req.actor, action=req.action, resource=req.xrn, context={'request': call})
        allowed = self.guard.is_allowed(inquiry)
        ids = tuple(dict.fromkeys(call.hits))
        denied = any(p.uid in ids and not p.allow_access() for p in self.policies)
        return Decision(allowed, 'explicit_deny' if denied else 'allow' if allowed else 'implicit_deny',
                        ids, ({'matched': ids},))

BACKENDS = {'mini': Mini, 'casbin': Casbin, 'vakt': Vakt}
