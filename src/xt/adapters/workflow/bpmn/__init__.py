# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Engine BPMN của xT trên SpiffWorkflow 3.2.0 (LGPL-3.0, phụ thuộc thư viện không sửa).

- `compiler`: ProcessSpec v2 -> BPMN 2.0 + BPMNDI + manifest.
- `hardening`: parser lxml cứng (không entity/DTD/mạng), cấm scriptTask/pre/postScript.
- `engine`: script engine an toàn (AST whitelist, service allowlist, bọc lỗi service).
- `serializer`: state JSON + `_reorder` (bẫy JSONB).
- `spiff`: `SpiffProcessEngine` hiện thực port `ProcessEngine`.
"""
