<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->
# Gói `kenh-pii`

Policy cho kênh người xem giá trị thật của dữ liệu cá nhân (`/pii/reveal`, research/spike-pii-lane.md §3.7):
field `basic` mọi nhân sự đang làm xem được; field `sensitive` (số tài khoản, CCCD, lương…) chỉ vai trò
có `pii.reveal`. Mỗi lần xem ghi `xt_meta.pii_egress` (tier `human`, người xem, field đã mở).
