# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Vòng đời policy: người duyệt gắn với digest nội dung; đổi nội dung thì mất hiệu lực."""

from __future__ import annotations

from xt.domain.iam import HUMAN, PolicyDocument, Principal, policy_digest

ADMIN_ROLE = "iam_admin"


class PolicyApprovalError(ValueError):
    """Duyệt hoặc kích hoạt policy sai quy tắc."""


class PolicyStore:
    """Bản trong bộ nhớ của `iam_policy` (prod: JSONB + sha256 + revision, cấm sửa bản đã duyệt)."""

    def __init__(self, doc: PolicyDocument, proposer: Principal) -> None:
        self.doc = doc
        self.proposer = proposer
        # SoD theo người thật: agent đề xuất thì người được đại diện cũng không được tự duyệt.
        effective = proposer.id if proposer.kind == HUMAN else (proposer.behalf or proposer.id)
        self._barred = frozenset({proposer.id.casefold(), effective.casefold()})
        self._approved_digest: str | None = None

    def approve(self, actor: Principal) -> None:
        if (
            actor.kind != HUMAN
            or actor.tenant != self.doc.tenant
            or ADMIN_ROLE not in actor.roles
            or actor.id.casefold() in self._barred
        ):
            raise PolicyApprovalError(
                "cần người (không phải agent) là iam_admin cùng tenant và khác người đề xuất"
            )
        self._approved_digest = policy_digest(self.doc)

    def active(self) -> PolicyDocument:
        if self._approved_digest is None:
            raise PolicyApprovalError("policy chưa được duyệt")
        if self._approved_digest != policy_digest(self.doc):
            raise PolicyApprovalError("nội dung policy đã đổi sau khi duyệt")
        return self.doc
