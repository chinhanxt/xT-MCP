# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Sha của định nghĩa đã duyệt không được đổi khi spec thêm trường tùy chọn (D-DEMO-LEVELS-1).

`ProcessCatalog` so `spec_sha256` của bản compile lại với bản đã lưu; trường mới có giá trị mặc
định (vd `Assignee.alternates = []`) mà lọt vào sha sẽ làm mọi định nghĩa đang chạy bị từ chối.
Giá trị dưới đây chụp trên mã TRƯỚC khi thêm `alternates`."""

from __future__ import annotations

import json
import pathlib

import pytest

from xt.adapters.workflow.bpmn.compiler import spec_sha256
from xt.domain.process.spec import ProcessSpec

ROOT = pathlib.Path(__file__).resolve().parents[3]

GOLDEN = {
    "packages/cham-cong/processes/duyet_bang_cong.json": "5ed82958fb54e8b1349cebc636c46185266977dda4eaea06b33dce67912d84a2",  # noqa: E501
    "packages/crm/processes/duyet_giam_gia.json": "0686e91b3aedf34e899019a49a2e6eb345c855e03557984c863aa1c36db5d322",  # noqa: E501
    "packages/de-xuat-phe-duyet/processes/approval_ticket_approval.json": "fc6d6089e6135d9c5762a74ed8d01a5dfa98755d2d315f4c4d9ed71d922580f9",  # noqa: E501
    "packages/de-xuat-phe-duyet/processes/proposal_approval.json": "f0158db0ffab3168a4c4f878d72497f55d5896f8e686f6204b890b367a5a247c",  # noqa: E501
    "packages/giao-viec-sla/processes/task_normal.json": "c7a359422a51f2642168c75a6542f44964c811067f08a684929fff877c1905ea",  # noqa: E501
    "packages/giao-viec-sla/processes/task_urgent.json": "ac4b17e71ff449b5016bb9ad08021cb2976a20ef0cf1dc50c0b071d7a5d50088",  # noqa: E501
    "packages/giao-viec-sla/processes/weekly_digest.json": "437be6f9b388be9af10841c4f09b14be3b66375080279fc0dd64615069a4b42e",  # noqa: E501
    "packages/kho-hang/processes/stock_approval.json": "bf2015f1fd451065c284e53b440108b6503ab71da001424b1272a1c636ba7c1d",  # noqa: E501
    "packages/nhan-su-vong-doi/processes/employee_offboarding.json": "88839647d65061472d0a83fe632e1a9bdd062c111afebf6410ffdfaf3728d71f",  # noqa: E501
    "packages/tai-san/processes/duyet_bao_tri.json": "c992a9b5d5f494f9f77dc15e701d61ec9cc24979f9de5ac24b6a50951d73b376",  # noqa: E501
    "tests/fixtures/process/packs/ban_tin_tuan.json": "04c4bc972e96c2e5945cf6f14fa96738c720ff6f9435ae4fc75eb2cfac73fc79",  # noqa: E501
    "tests/fixtures/process/packs/de_xuat_phe_duyet.json": "0c3a799ab20ec3b072eb027597f86c02d635dfda5b2adfd759a80310b583e77e",  # noqa: E501
    "tests/fixtures/process/packs/giao_viec_khan.json": "601cb7255a062f18d376b1115fdcefa0f1e400832a7fa1e9ded23931f577e40b",  # noqa: E501
    "tests/fixtures/process/packs/giao_viec_thuong.json": "17dafca118c84221ca55587197bd38cb31e12c593861279e51ac196f53c9f1db",  # noqa: E501
    "tests/fixtures/process/packs/nv_offboarding.json": "a6143397e016e1309cd3def680c44563f6865feb28045bdd5f9e86f50e8dbbcb",  # noqa: E501
    "tests/fixtures/process/packs/nv_onboarding.json": "eebbbbe73131da52bbddac1b361430890623c7a3482c856dd5f2451c03eb76a2",  # noqa: E501
}


@pytest.mark.parametrize("rel", sorted(GOLDEN))
def test_spec_sha_of_approved_definitions_unchanged(rel: str) -> None:
    spec = ProcessSpec.model_validate(json.loads((ROOT / rel).read_text(encoding="utf-8")))
    assert spec_sha256(spec) == GOLDEN[rel]


def test_empty_alternates_not_in_sha_but_set_alternates_is() -> None:
    """`alternates` rỗng (mặc định) không vào sha; khai alternates thì sha đổi (là bản mới)."""
    rel = "packages/kho-hang/processes/stock_approval.json"
    doc = json.loads((ROOT / rel).read_text(encoding="utf-8"))
    doc["steps"][0]["assignee"] = {"kind": "role", "role": "warehouse_manager", "alternates": []}
    assert spec_sha256(ProcessSpec.model_validate(doc)) == GOLDEN[rel]
    doc["roles"] = ["warehouse_manager", "director"]
    doc["steps"][0]["assignee"]["alternates"] = ["role(director)"]
    assert spec_sha256(ProcessSpec.model_validate(doc)) != GOLDEN[rel]
