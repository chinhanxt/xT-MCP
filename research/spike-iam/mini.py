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
        # Hot path short-circuits per statement; the full predicate trace lives in explain().
        hits = []
        roles = {"role:" + r for r in req.actor.roles}
        for s in self.doc["statements"]:
            subjects = s["subjects"]
            if not (req.actor.id in subjects or "*" in subjects or roles.intersection(subjects)):
                continue
            if not any(star(p, req.action) for p in s["actions"]):
                continue
            if not any(star(p, req.xrn) for p in s["resources"]):
                continue
            if conditions(req, s["conditions"]):
                hits.append(s)
        denied = any(s["effect"] == "Deny" for s in hits)
        allowed = bool(hits) and not denied
        return Decision(
            allowed,
            "explicit_deny" if denied else "allow" if allowed else "implicit_deny",
            tuple(s["id"] for s in hits),
        )

    def explain(self, req):
        """Internal diagnostics. Authorize the explain tool separately before exposure."""
        d = self.evaluate(req)
        return {
            "allowed": d.allowed,
            "reason": d.reason,
            "statements": d.statements,
            "trace": diagnostics(self.doc, req),
        }


def diagnostics(doc, req):
    """Side-effect-free predicate trace, never used to authorize an operation."""
    result = []
    for s in doc["statements"]:
        predicates = {
            "subject": req.actor.id in s["subjects"]
            or "*" in s["subjects"]
            or any("role:" + r in s["subjects"] for r in req.actor.roles),
            "action": any(star(p, req.action) for p in s["actions"]),
            "resource": any(star(p, req.xrn) for p in s["resources"]),
        }
        details = []
        for c in s["conditions"]:
            try:
                left = value(req, c["left"])
                right = (
                    value(req, c["right"]["ref"]) if isinstance(c["right"], dict) else c["right"]
                )
                details.append(
                    {
                        "field": c["left"],
                        "op": c["op"],
                        "actual": left,
                        "expected": right,
                        "passed": conditions(req, [c]),
                    }
                )
            except (KeyError, AttributeError):
                details.append({"field": c["left"], "passed": False, "error": "missing_attribute"})
        predicates["conditions"] = all(d["passed"] for d in details)
        result.append(
            {
                "id": s["id"],
                "effect": s["effect"],
                "matched": all(predicates.values()),
                "predicates": predicates,
                "conditions": details,
            }
        )
    return result
