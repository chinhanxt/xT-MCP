# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Một shape `ToolContract` duy nhất (tool-contracts-v1 §1) và effects -> annotations (§2).

Mọi đầu ra (đăng ký MCP, catalog/docs, contract test) chiếu từ đây; annotations không viết tay.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from typing import Any, Literal

from xt.domain.autonomy import RiskLevel

Since = Literal["0.1", "1.0"]
MaxAutonomy = Literal["L0", "L1", "L2", "L3"]


@dataclass(frozen=True, slots=True)
class Effects:
    """10 cờ effects, khai cho hợp của mọi input hợp lệ và mọi hệ quả deferred (REA)."""

    mutates_records: bool = False
    mutates_definitions: bool = False
    enqueues_work: bool = False
    notifies_internal: bool = False
    accesses_external_network: bool = False
    launches_process: bool = False
    may_discard_data: bool = False
    executes_foreign_code: bool = False
    mutates_device: bool = False
    idempotent: bool = False

    WRITE_KEYS = (
        "mutates_records",
        "mutates_definitions",
        "enqueues_work",
        "notifies_internal",
        "accesses_external_network",
        "launches_process",
        "mutates_device",
    )

    def as_dict(self) -> dict[str, bool]:
        return {f.name: bool(getattr(self, f.name)) for f in fields(self)}

    def true_keys(self) -> list[str]:
        return [k for k, v in self.as_dict().items() if v]

    @property
    def writes(self) -> bool:
        return any(getattr(self, k) for k in self.WRITE_KEYS)


@dataclass(frozen=True, slots=True)
class Annotations:
    read_only: bool
    destructive: bool
    idempotent: bool
    open_world: bool

    def as_mcp(self) -> dict[str, bool]:
        return {
            "readOnlyHint": self.read_only,
            "destructiveHint": self.destructive,
            "idempotentHint": self.idempotent,
            "openWorldHint": self.open_world,
        }


def derive_annotations(e: Effects) -> Annotations:
    """Đúng công thức REA (`toolEffects.ts:655-657`)."""
    return Annotations(
        read_only=not e.writes,
        destructive=e.may_discard_data or e.executes_foreign_code or e.mutates_device,
        idempotent=e.idempotent,
        open_world=e.accesses_external_network or e.launches_process,
    )


def title_from_name(name: str) -> str:
    """Title sinh từ name (REA `toolEffects.ts:~692`), không viết tay."""
    return " ".join(part.capitalize() for part in name.split("_"))


@dataclass(frozen=True, slots=True)
class ToolContract:
    name: str
    since: Since
    description: str
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any]
    effects: Effects
    max_autonomy: MaxAutonomy
    iam: tuple[str, ...]
    errors: tuple[str, ...]
    examples: tuple[Mapping[str, Any], ...]
    human_only: bool = False
    ui_resource: str | None = None
    returns_untrusted_content: bool = True
    sample_output: Mapping[str, Any] = field(default_factory=dict)

    @property
    def annotations(self) -> Annotations:
        return derive_annotations(self.effects)

    @property
    def title(self) -> str:
        return title_from_name(self.name)

    @property
    def read_only(self) -> bool:
        return self.annotations.read_only

    @property
    def ceiling(self) -> RiskLevel:
        """Trần tĩnh; `human_only` là lớp riêng ngoài thang L (architecture-v2 §5.4)."""
        return RiskLevel.HUMAN_ONLY if self.human_only else RiskLevel(self.max_autonomy)

    def doc_view(self) -> dict[str, Any]:
        """Dạng JSON trong `docs/tool-contracts-v1.md` §5 (đầu ra "catalog/docs")."""
        return {
            "name": self.name,
            "since": self.since,
            "description": self.description,
            "max_autonomy": self.max_autonomy,
            "human_only": self.human_only,
            "effects_true": self.effects.true_keys(),
            "annotations": self.annotations.as_mcp(),
            "iam": list(self.iam),
            "errors": list(self.errors),
            "ui_resource": self.ui_resource,
            "returns_untrusted_content": self.returns_untrusted_content,
            "input_schema": _plain(self.input_schema),
            "output_schema": _plain(self.output_schema),
            "examples": [_plain(e) for e in self.examples],
        }


def _plain(v: Any) -> Any:
    if isinstance(v, Mapping):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, list | tuple):
        return [_plain(x) for x in v]
    return v
