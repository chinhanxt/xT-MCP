# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""IAM thuần: policy document, evaluator Deny > Allow > implicit deny, explain. Xem docs/iam.md."""

from xt.domain.iam.attributes import normalize_resource, normalize_value
from xt.domain.iam.catalog import DEFAULT_ACTION_LEVELS, ActionCatalog, rank
from xt.domain.iam.decision import (
    AccessRequest,
    ConditionTrace,
    Decision,
    Explanation,
    Reason,
    StatementTrace,
)
from xt.domain.iam.evaluator import PolicyEvaluator
from xt.domain.iam.glob import Glob, glob_match
from xt.domain.iam.grants import ApprovalBinding, ApprovalReceipt, DelegationGrant, fingerprint
from xt.domain.iam.policy import (
    Attr,
    Condition,
    Effect,
    Op,
    PolicyDocument,
    Ref,
    Statement,
    loads_policy_json,
    policy_digest,
    policy_json_schema,
)
from xt.domain.iam.subject import AGENT, HUMAN, Actor, Principal
from xt.domain.iam.xrn import Xrn, parse_xrn

__all__ = [
    "AGENT",
    "DEFAULT_ACTION_LEVELS",
    "HUMAN",
    "AccessRequest",
    "ActionCatalog",
    "Actor",
    "ApprovalBinding",
    "ApprovalReceipt",
    "Attr",
    "Condition",
    "ConditionTrace",
    "Decision",
    "DelegationGrant",
    "Effect",
    "Explanation",
    "Glob",
    "Op",
    "PolicyDocument",
    "PolicyEvaluator",
    "Principal",
    "Reason",
    "Ref",
    "Statement",
    "StatementTrace",
    "Xrn",
    "fingerprint",
    "glob_match",
    "loads_policy_json",
    "normalize_resource",
    "normalize_value",
    "parse_xrn",
    "policy_digest",
    "policy_json_schema",
    "rank",
]
