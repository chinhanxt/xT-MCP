<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->
# Gói `tep-dinh-kem`

Quyền cho dịch vụ dữ liệu phi cấu trúc (`/files/v1`, tool `upload_file`; `docs/files-api.md`) và model
`tai_lieu` (sổ tài liệu: tiêu đề + field kiểu `file` + loại + ghi chú).

- `file.create`: mọi nhân sự (`employee`) và agent thay mặt họ.
- `file.read` / `file.delete` (xóa mềm): người tải lên; `file.read` thêm cho người duyệt hồ sơ
  (`dept_head`, `director`, `chief_accountant`, `accountant`, `hr`).
- Tệp chưa quét virus (profile `minimal`) chỉ người tải lên tải về được (mặc định
  `XT_FILES_UNSCANNED_DOWNLOAD=uploader`); profile `full` quét bằng ClamAV trước.
- Field `file` có FK ghép `(tenant_id, id)` tới `xt_meta.file_object`: không trỏ được sang tệp của
  tenant khác, không trỏ được tới id bịa.
