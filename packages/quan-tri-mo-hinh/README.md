<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->
# Gói `quan-tri-mo-hinh`

Chỉ có policy IAM: ai được đề xuất thay đổi model (`propose_model_change`) và ai được duyệt đề xuất
(`decide_request` trên `model_proposal:<id>`). Duyệt luôn qua trang `/desk/confirm` (step-up OTP);
thay đổi PHÁ HỦY cần hai người duyệt khác nhau. Sau khi duyệt, `xt worker` áp thay đổi
(`model.apply`: pha 1 DDL + pha 2 backfill/index). Trạng thái xem bằng `get_record`.

Cài: `xt pack install --tenant <alias> packages/quan-tri-mo-hinh --proposed-by … --approved-by …`.
