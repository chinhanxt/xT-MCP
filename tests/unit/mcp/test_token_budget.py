# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Đo token thật của catalog đã quảng bá trên qwen3.5:4b (ollama), như tool-contracts-v1 §10.

Marker `slow` (cần ollama ở 127.0.0.1:11434): `uv run pytest -m slow tests/unit/mcp -s --no-cov`.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any

import pytest

from xt.adapters.mcp import tool_definition
from xt.contracts.tools import Profile, profile_tools

URL = "http://127.0.0.1:11434/api/chat"
MODEL = "qwen3.5:4b"


def _fn(tool: Any) -> dict[str, Any]:
    schema = {k: v for k, v in tool.input_schema.items() if k != "$schema"}
    return {
        "type": "function",
        "function": {"name": tool.name, "description": tool.description, "parameters": schema},
    }


def _prompt_tokens(tools: list[dict[str, Any]]) -> int:
    body = {
        "model": MODEL,
        "stream": False,
        "think": False,
        "tools": tools,
        "messages": [{"role": "user", "content": "xin chào"}],
        "options": {"num_ctx": 32768, "temperature": 0, "num_predict": 1},
    }
    req = urllib.request.Request(
        URL, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        return int(json.load(r)["prompt_eval_count"])


@pytest.mark.slow
def test_core10_tokens_on_local_model() -> None:
    try:
        urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=2).read()
    except OSError:
        pytest.skip("ollama không chạy")
    tools = {p: [_fn(tool_definition(c, frozenset())) for c in profile_tools(p)] for p in Profile}
    base = _prompt_tokens([])
    tok = {p.value: _prompt_tokens(t) - base for p, t in tools.items()}
    print("tool tokens on", MODEL, tok)
    assert tok["core-10"] < 0.55 * tok["full"]
    assert tok["core-10"] <= 4_500  # §10: 3.916 token; client local đặt num_ctx >= 16384
