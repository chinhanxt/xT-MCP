# SPDX-License-Identifier: Apache-2.0
"""Trust tier theo client (azp) + policy `llm_egress` theo tenant và theo agent.

Server KHÔNG biết LLM nào đứng sau client. Nó chỉ biết `azp` (client_id Keycloak) đã được ký trong JWT.
Vì vậy tier là một *khẳng định của admin về client*, lưu phía server, không phải thứ client tự khai:
- client DCR (ChatGPT/Claude/Claude Code/Goose tự đăng ký) -> không có trong registry -> `cloud` (fail closed)
- client cấp sẵn, confidential, admin gắn với LLM nội bộ (vd `xt-desk-agent`) -> `local`
- phiên của người trên Desk (không có LLM) -> `human`
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .spec import PiiLevel

Tier = Literal["cloud", "local", "human"]
Mode = Literal["full", "masked", "local_only"]
Action = Literal["raw", "token_hint", "token", "omit"]

MODE_RANK = {"full": 0, "masked": 1, "local_only": 2}   # càng lớn càng chặt

# (mode, pii level) -> hành động trên field khi ra khỏi xT
ACTIONS: dict[str, dict[str, Action]] = {
    "full":       {"none": "raw", "basic": "raw",        "sensitive": "raw"},
    "masked":     {"none": "raw", "basic": "token_hint", "sensitive": "token"},
    "local_only": {"none": "raw", "basic": "token",      "sensitive": "omit"},
}


@dataclass(frozen=True)
class Principal:
    tenant: str
    azp: str
    sub: str | None
    sid: str            # Keycloak session id (claim `sid`): ổn định qua refresh -> phạm vi vault token
    roles: frozenset[str] = frozenset()
    tier: Tier = "cloud"

    @property
    def session_key(self) -> str:
        return f"{self.tenant}|{self.azp}|{self.sub}|{self.sid}"


@dataclass
class ClientRegistry:
    """azp -> tier. Production: thuộc tính client Keycloak `xt.trust_tier` do admin đặt (client DCR không có)."""
    tiers: dict[str, Tier] = field(default_factory=dict)

    def tier_of(self, azp: str | None) -> Tier:
        return self.tiers.get(azp or "", "cloud")


@dataclass
class TenantPolicy:
    llm_egress: dict[str, Mode] = field(default_factory=lambda: {"cloud": "masked", "local": "full", "human": "full"})
    agent_overrides: dict[str, Mode] = field(default_factory=dict)   # azp -> mode, chỉ được CHẶT hơn
    basic_filter_ops: frozenset[str] = frozenset({"eq"})   # op được phép trên field basic khi mode != full
    hints: bool = True                     # gợi ý che một phần (Nguyễn V*** A) cho field basic ở mode masked
    reveal_roles: dict[str, frozenset[str]] = field(default_factory=lambda: {
        "basic": frozenset(),                              # rỗng = mọi người dùng đã đăng nhập của tenant
        "sensitive": frozenset({"hr", "giam_doc"})})

    def mode_for(self, p: Principal) -> Mode:
        base: Mode = self.llm_egress.get(p.tier, "local_only")
        ov = self.agent_overrides.get(p.azp)
        if ov is not None and MODE_RANK[ov] > MODE_RANK[base]:
            return ov
        return base


def action_for(mode: Mode, level: PiiLevel) -> Action:
    return ACTIONS[mode][level]


def principal_from_claims(claims: dict, registry: ClientRegistry) -> Principal:
    """Dựng Principal từ claim JWT ĐÃ verify (chữ ký/iss/aud/exp kiểm ở verifier, xem spike-mcp-auth)."""
    roles = frozenset((claims.get("realm_access") or {}).get("roles", []))
    azp = claims.get("azp") or ""
    return Principal(tenant=claims.get("tenant_id") or "", azp=azp, sub=claims.get("sub"),
                     sid=claims.get("sid") or claims.get("jti") or "", roles=roles, tier=registry.tier_of(azp))
