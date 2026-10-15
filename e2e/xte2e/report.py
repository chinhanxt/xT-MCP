# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Bằng chứng của từng kịch bản: `e2e/reports/<timestamp>/E<n>.json` + `E<n>.md` + `summary.md`.

Token, mật khẩu, TOTP không bao giờ được ghi: `redact` thay chuỗi JWT và các khóa nhạy cảm.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from xte2e.config import E2E

JWT = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
SECRET_KEYS = {
    "password",
    "totp",
    "access_token",
    "refresh_token",
    "id_token",
    "client_secret",
    "code_verifier",
    "authorization",
    "ticket",
    "sig",
    "registration_access_token",
}

SCENARIOS: dict[str, str] = {
    "E0": "Dựng stack + seed",
    "E1": "W1 qua Desk + LLM local (qwen3.5:4b)",
    "E2": "W1 qua MCP client có script (DCR + PKCE + /desk/confirm TOTP)",
    "E3": "Poka-yoke (rule tiếng Việt + remediation)",
    "E4": "RLS 2 tenant (MCP + PostgREST /data + tenant_ambiguous)",
    "E5": "PII (token che + /pii/reveal vé một lần)",
    "E6": "Đổi model (propose -> duyệt -> apply pha 2)",
    "E7": "SLA leo thang + restart worker",
    "E8": "Headless 4 dịch vụ (SSO, Gateway, Data, Workflow, Files)",
    "E9": "Đăng nhập OIDC trên Desk (container xt-desk thật)",
    "E10": "Security smoke + auth âm + SoD",
    "E11": "Audit tamper",
}


def redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            k: ("<redacted>" if str(k).lower() in SECRET_KEYS else redact(v))
            for k, v in obj.items()
        }
    if isinstance(obj, (list, tuple)):
        return [redact(x) for x in obj]
    if isinstance(obj, str):
        return JWT.sub("<jwt>", obj)
    return obj


def run_dir() -> Path:
    d = os.environ.get("XT_E2E_REPORT_DIR")
    if d:
        p = Path(d)
    else:
        p = E2E / "reports" / time.strftime("%Y%m%d-%H%M%S")
        os.environ["XT_E2E_REPORT_DIR"] = str(p)
    p.mkdir(parents=True, exist_ok=True)
    return p


@dataclass
class Evidence:
    scenario: str
    test: str
    steps: list[dict[str, Any]] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    started: float = field(default_factory=time.time)

    def step(self, name: str, data: Any = None, ok: bool | None = None) -> None:
        self.steps.append(
            {
                "t": round(time.time() - self.started, 2),
                "step": name,
                **({} if ok is None else {"ok": ok}),
                "data": redact(data),
            }
        )

    def gap(self, piece: str, detail: str = "") -> None:
        """Tính năng chưa có: ghi tên mảnh còn thiếu rồi FAIL (không skip)."""
        msg = f"THIẾU {piece}" + (f": {detail}" if detail else "")
        self.gaps.append(msg)
        self.step("gap", msg, ok=False)
        pytest.fail(msg, pytrace=False)


class Checks:
    """Gom nhiều kiểm tra rồi FAIL một lần với danh sách đầy đủ (báo cáo thấy hết khoảng trống)."""

    def __init__(self, ev: Evidence) -> None:
        self.ev, self.problems = ev, []  # type: list[str]

    def check(self, ok: bool, what: str, data: Any = None) -> bool:
        self.ev.step(what, data, ok=bool(ok))
        if not ok:
            self.problems.append(what)
        return bool(ok)

    def done(self) -> None:
        if self.problems:
            pytest.fail("; ".join(self.problems), pytrace=False)


def write_scenario(dirp: Path, scenario: str, results: list[dict[str, Any]]) -> None:
    status = "PASS" if results and all(r["outcome"] == "passed" for r in results) else "FAIL"
    data = {
        "scenario": scenario,
        "title": SCENARIOS.get(scenario, ""),
        "status": status,
        "tests": results,
    }
    (dirp / f"{scenario}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    lines = [f"# {scenario}: {SCENARIOS.get(scenario, '')}", "", f"Trạng thái: **{status}**", ""]
    for r in results:
        lines.append(f"## {r['test']}: {r['outcome'].upper()} ({r['duration']:.1f} s)")
        if r.get("message"):
            lines += ["", "```", r["message"][:1500], "```"]
        for s in r["steps"]:
            mark = "" if "ok" not in s else (" [OK]" if s["ok"] else " [FAIL]")
            lines.append(f"- {s['step']}{mark}")
        lines.append("")
    (dirp / f"{scenario}.md").write_text("\n".join(lines), encoding="utf-8")


def write_summary(
    dirp: Path, by_scenario: dict[str, list[dict[str, Any]]], selected: list[str]
) -> str:
    rows = ["| # | Kịch bản | Kết quả | Mảnh còn thiếu / lỗi |", "|---|---|---|---|"]
    out: dict[str, Any] = {}
    for sid in SCENARIOS:
        res = by_scenario.get(sid)
        if not res:
            status = "CHƯA CHẠY" if sid not in selected else "FAIL"
            rows.append(
                f"| {sid} | {SCENARIOS[sid]} | {status} | {'target riêng (make e2e-llm)' if sid == 'E1' else ''} |"
            )
            out[sid] = {"status": status}
            continue
        status = "PASS" if all(r["outcome"] == "passed" for r in res) else "FAIL"
        msgs = [
            r["message"].splitlines()[0][:400]
            for r in res
            if r["outcome"] != "passed" and r.get("message")
        ]
        rows.append(
            f"| {sid} | {SCENARIOS[sid]} | {status} | {'<br>'.join(m.replace('|', '/') for m in msgs)} |"
        )
        out[sid] = {"status": status, "failures": msgs}
    text = "# Báo cáo e2e xT\n\nThư mục: `" + str(dirp) + "`\n\n" + "\n".join(rows) + "\n"
    (dirp / "summary.md").write_text(text, encoding="utf-8")
    (dirp / "summary.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return text
