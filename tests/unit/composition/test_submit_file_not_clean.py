# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""TCV1 §5.14: submit_request với tệp đính kèm chưa `clean`/`not_scanned` -> `file_not_clean`."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from xt.application.tools.errors import ToolError
from xt.domain.model.spec import Catalog
from xt.entrypoints.composition.process import ProcessToolAdapter

ROOT = Path(__file__).resolve().parents[3]


def _catalog() -> Catalog:
    m = json.loads((ROOT / "packages/de-xuat-phe-duyet/models/advance_request.json").read_text())
    m["fields"] = [f for f in m["fields"] if f["name"] != "proposal"]
    m.pop("title_field", None)
    m["fields"].append(
        {"id": "f99", "name": "chung_tu", "type": "file", "label": "Chứng từ", "pii": "none"}
    )
    return Catalog.model_validate({"version": 1, "models": [m]})


def _adapter(bad: list[uuid.UUID]) -> tuple[ProcessToolAdapter, list[Any]]:
    seen: list[Any] = []

    def files_not_clean(actor: Any, ids: list[uuid.UUID]) -> list[uuid.UUID]:
        seen.append(ids)
        return [i for i in ids if i in bad]

    rt = SimpleNamespace(
        catalogs=SimpleNamespace(current=lambda actor: (1, _catalog())),
        store=SimpleNamespace(files_not_clean=files_not_clean),
    )
    a = ProcessToolAdapter.__new__(ProcessToolAdapter)
    a.rt = rt  # type: ignore[assignment]
    return a, seen


def _caller() -> Any:
    return SimpleNamespace(actor=None, workflow=lambda: None)


def test_scanning_attachment_blocks_submit() -> None:
    fid = uuid.uuid4()
    a, seen = _adapter([fid])
    with pytest.raises(ToolError) as ex:
        a._require_clean_files(_caller(), "advance_request", {"chung_tu": f"file:{fid}"})
    err = ex.value.to_error()
    assert err["code"] == "precondition_failed"
    assert err["details"]["reason"] == "file_not_clean"
    assert f"file:{fid}" in json.dumps(err)
    assert seen == [[fid]]


def test_clean_or_absent_attachment_passes() -> None:
    a, seen = _adapter([])
    a._require_clean_files(_caller(), "advance_request", {"chung_tu": str(uuid.uuid4())})
    a._require_clean_files(_caller(), "advance_request", {"chung_tu": None})
    assert len(seen) == 1
