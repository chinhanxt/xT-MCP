# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Parse BPMN bằng parser lxml cứng TRƯỚC khi đưa cho SpiffWorkflow (VS A.3).

SpiffWorkflow 3.2.0 gọi `etree.fromstring` với parser mặc định; xT không để nó parse chuỗi thô:
ta parse bằng parser tắt entity/DTD/mạng, từ chối DOCTYPE/ENTITY, scriptTask, callActivity,
pre/postScript, rồi đưa cây đã parse cho `add_bpmn_xml`.
"""

from __future__ import annotations

from typing import Any

from lxml import etree

from xt.domain.process.errors import UnsafeExpression

MAX_BPMN_BYTES = 2 * 1024 * 1024
FORBIDDEN_TAGS = frozenset({"scriptTask", "callActivity", "script", "preScript", "postScript"})


def _parser() -> etree.XMLParser:
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        huge_tree=False,
        load_dtd=False,
        dtd_validation=False,
        remove_comments=True,
        remove_pis=True,
    )


def parse_bpmn(xml: str | bytes) -> etree._Element:
    """Trả cây BPMN đã kiểm; lỗi -> `UnsafeExpression`."""
    raw = xml.encode() if isinstance(xml, str) else xml
    if len(raw) > MAX_BPMN_BYTES:
        raise UnsafeExpression(f"BPMN lớn quá {MAX_BPMN_BYTES} byte")
    upper = raw.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise UnsafeExpression("BPMN chứa DOCTYPE/ENTITY: bị từ chối")
    try:
        root = etree.fromstring(raw, _parser())
    except etree.XMLSyntaxError as e:
        raise UnsafeExpression(f"BPMN không phải XML hợp lệ: {e}") from None
    info: Any = root.getroottree().docinfo  # lxml-stubs thiếu thuộc tính doctype
    if info.doctype or info.internalDTD is not None or info.externalDTD is not None:
        # AUD-6: kiểm sau khi parse nên không phụ thuộc mã hóa (UTF-16/32, BOM)
        raise UnsafeExpression("BPMN chứa DOCTYPE/DTD: bị từ chối")
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        local = etree.QName(el).localname
        if local in FORBIDDEN_TAGS:
            raise UnsafeExpression(f"BPMN chứa phần tử bị cấm: {local}")
    return root


def harden_bpmn(xml: str | bytes) -> bytes:
    """Kiểm BPMN và trả lại bytes gốc (cho lưu trữ / so sánh)."""
    parse_bpmn(xml)
    return xml.encode() if isinstance(xml, str) else xml
