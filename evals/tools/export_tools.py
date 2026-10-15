# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Sinh lại evals/data/tools.json từ ToolContract thật (src/xt/contracts/tools); chạy bằng `uv run python evals/tools/export_tools.py`."""
import json
import pathlib

from xt.contracts.tools import CATALOG, CORE_10, catalog_digest

out = {
    "_source": "src/xt/contracts/tools (ToolContract catalog), regenerate with tools/export_tools.py",
    "_catalog_digest": catalog_digest(),
    "core10": list(CORE_10),
    "tools": [
        {
            "name": t.name,
            "since": t.since,
            "max_autonomy": t.max_autonomy,
            "description": t.description,
            "input_schema": t.input_schema,
        }
        for t in CATALOG
    ],
}
dest = pathlib.Path(__file__).resolve().parents[1] / "data" / "tools.json"
dest.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
print("wrote", dest, len(out["tools"]), "tools")
