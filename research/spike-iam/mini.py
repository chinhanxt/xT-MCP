# SPDX-License-Identifier: Apache-2.0
"""Independent IAM-style evaluator. No sklite source was copied.

The intentionally small kernel implements statements as data; the shared host
implements trust boundaries. Keep these separate when comparing line counts.
"""
from contracts import Decision, conditions, star, validate
from policy_language import value

class Mini:
    def __init__(self, doc):
        self.doc = validate(doc)

    def evaluate(self, req):
        trace = []
        hits = []
        for s in self.doc['statements']:
            identity = req.actor.id in s['subjects'] or '*' in s['subjects'] or any('role:' + role in s['subjects'] for role in req.actor.roles)
            action = any(star(p, req.action) for p in s['actions'])
            resource = any(star(p, req.xrn) for p in s['resources'])
            abac = conditions(req, s['conditions'])
            trace.append({'id': s['id'], 'subject': identity, 'action': action, 'resource': resource, 'condition': abac})
            if identity and action and resource and abac:
                hits.append(s)
        denied = any(s['effect'] == 'Deny' for s in hits)
        allowed = bool(hits) and not denied
        return Decision(allowed, 'explicit_deny' if denied else 'allow' if allowed else 'implicit_deny',
                        tuple(s['id'] for s in hits), tuple(trace))

    def explain(self, req):
        """Internal diagnostics. Authorize the explain tool separately before exposure."""
        d = self.evaluate(req)
        return {"allowed": d.allowed, "reason": d.reason,
                "statements": d.statements, "trace": diagnostics(self.doc, req)}


def diagnostics(doc, req):
    """Side-effect-free predicate trace, never used to authorize an operation."""
    result = []
    for s in doc['statements']:
        predicates = {
            'subject': req.actor.id in s['subjects'] or '*' in s['subjects'] or any('role:' + r in s['subjects'] for r in req.actor.roles),
            'action': any(star(p, req.action) for p in s['actions']),
            'resource': any(star(p, req.xrn) for p in s['resources']),
        }
        details = []
        for c in s['conditions']:
            try:
                left = value(req, c['left'])
                right = value(req, c['right']['ref']) if isinstance(c['right'], dict) else c['right']
                details.append({'field': c['left'], 'op': c['op'], 'actual': left,
                                'expected': right, 'passed': conditions(req, [c])})
            except (KeyError, AttributeError):
                details.append({'field': c['left'], 'passed': False, 'error': 'missing_attribute'})
        predicates['conditions'] = all(d['passed'] for d in details)
        result.append({'id': s['id'], 'effect': s['effect'],
                       'matched': all(predicates.values()), 'predicates': predicates,
                       'conditions': details})
    return result
