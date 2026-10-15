# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Nạp và kiểm schema scenario."""
from __future__ import annotations

import json
import pathlib

from jsonschema import Draft202012Validator

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "scenarios" / "scenario.schema.json"


def load_all(directory: pathlib.Path | None = None, only: list[str] | None = None) -> list[dict]:
    directory = directory or ROOT / "scenarios"
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    v = Draft202012Validator(schema)
    out, seen = [], set()
    for f in sorted(directory.glob("*.json")):
        if f.name == "scenario.schema.json":
            continue
        doc = json.loads(f.read_text(encoding="utf-8"))
        errs = sorted(v.iter_errors(doc), key=lambda e: list(e.absolute_path))
        if errs:
            raise ValueError(f"{f.name}: " + "; ".join(f"{'/'.join(map(str, e.absolute_path))}: {e.message[:200]}" for e in errs[:5]))
        for sc in doc["scenarios"]:
            if sc["id"] in seen:
                raise ValueError(f"id trùng: {sc['id']}")
            seen.add(sc["id"])
            sc["pack"] = doc["pack"]
            sc.setdefault("seed", "binhminh")
            sc.setdefault("profiles", ["core-10", "full"])
            if not sc["id"].startswith(doc["pack"] + "."):
                raise ValueError(f"{sc['id']} phải bắt đầu bằng '{doc['pack']}.'")
            out.append(sc)
    if only:
        out = [s for s in out if any(o in s["id"] or o == s["pack"] or o == s["category"] for o in only)]
    return out
