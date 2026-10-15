# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Hợp đồng MCP Apps (`ui://`): nguồn duy nhất cho view nào gắn với tool nào.

- Tool của model gắn view bằng `ToolContract.ui_resource` (catalog.py).
- Tool CHỈ DÀNH CHO APP (`visibility:["app"]`) khai ở đây; không thuộc 17 tool của model, host
  không đưa vào danh sách tool của model. Chúng chỉ đọc (không có tool app-only ghi), xem
  tool-contracts-v1 §Thẻ MCP App.
- Thẻ phê duyệt KHÔNG có tool app-only nào: nút chỉ mở `confirm_url`, không bao giờ duyệt.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from xt.contracts.tools.catalog import BY_NAME
from xt.contracts.tools.schema_parts import NAMED, NAMED_OR_NULL, REF_OUT, inp, out

ORGCHART_URI = "ui://xt/orgchart"
APPROVAL_CARD_URI = "ui://xt/approval-card"
RECORD_TABLE_URI = "ui://xt/record-table"


@dataclass(frozen=True, slots=True)
class UiViewContract:
    uri: str
    name: str
    description: str


UI_VIEWS: tuple[UiViewContract, ...] = (
    UiViewContract(ORGCHART_URI, "orgchart", "Sơ đồ tổ chức (SVG); bấm nút để xem chi tiết người."),
    UiViewContract(
        APPROVAL_CARD_URI,
        "approval_card",
        "Thẻ phê duyệt: tóm tắt yêu cầu chờ duyệt và nút mở trang xác nhận của xT.",
    ),
    UiViewContract(
        RECORD_TABLE_URI, "record_table", "Bảng kết quả tìm kiếm có phân trang và bộ lọc."
    ),
)


@dataclass(frozen=True, slots=True)
class AppToolContract:
    name: str
    description: str
    ui_resource: str
    iam: tuple[str, ...]
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any]
    delegates_to: str | None = None  # chuyển nguyên cho tool của catalog (qua dispatcher)


ORG_PERSON_DETAILS = AppToolContract(
    name="org_person_details",
    description=(
        "App-only: details of one person for the org chart (title, unit, manager, direct "
        "reports, contact). Contact fields pass through the PII lane."
    ),
    ui_resource=ORGCHART_URI,
    iam=("person.read",),
    input_schema=inp(
        {"ref": {"type": "string", "pattern": r"^person:[A-Za-z0-9._-]{1,64}$"}}, ["ref"]
    ),
    output_schema=out(
        {
            "ref": REF_OUT,
            "name": {"type": "string"},
            "title": {"type": "string"},
            "org_unit": NAMED_OR_NULL,
            "manager": NAMED_OR_NULL,
            "status": {"enum": ["active", "on_leave", "offboarding", "inactive"]},
            "email": {"type": "string"},
            "phone": {"type": "string"},
            "direct_reports": {"type": "array", "items": NAMED},
        },
        ["ref", "name"],
    ),
)

# Cùng schema với search_records: App gửi where/query/sort/cursor/limit, kết quả y hệt.
RECORDS_TABLE_PAGE = AppToolContract(
    name="records_table_page",
    description=(
        "App-only: one page of search_records for the record table (same arguments and result; "
        "runs through the same IAM, PII lane and signed cursors)."
    ),
    ui_resource=RECORD_TABLE_URI,
    iam=BY_NAME["search_records"].iam,
    input_schema=BY_NAME["search_records"].input_schema,
    output_schema=BY_NAME["search_records"].output_schema,
    delegates_to="search_records",
)

APP_TOOLS: tuple[AppToolContract, ...] = (ORG_PERSON_DETAILS, RECORDS_TABLE_PAGE)
APP_TOOLS_BY_NAME: Mapping[str, AppToolContract] = {t.name: t for t in APP_TOOLS}
