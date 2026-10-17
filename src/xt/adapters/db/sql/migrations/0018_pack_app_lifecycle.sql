-- SPDX-License-Identifier: Apache-2.0
-- SPDX-FileCopyrightText: 2026 xT contributors
--
-- G8 / D-UNI-T3-1: ứng dụng Tầng 3 cắm-rút theo tenant. `pack_install` ghi model, phụ thuộc và
-- khối `app` của gói (để ẩn/chặn ghi khi tắt và để inspect_workspace dựng launcher), cùng trạng
-- thái enabled/disabled do luồng đề xuất → duyệt đặt (người duyệt khác người đề xuất). Bảng đã
-- có RLS + trigger audit meta (0006) nên mỗi lần đổi trạng thái vào hash-chain audit.
-- Dòng cài trước migration này có models/depends_on NULL: tắt gói đó bị từ chối cho tới khi cài
-- lại (fail closed, không đoán model thuộc gói).
ALTER TABLE xt_meta.pack_install
  ADD COLUMN status text NOT NULL DEFAULT 'enabled' CHECK (status IN ('enabled', 'disabled')),
  ADD COLUMN models text[],
  ADD COLUMN depends_on text[],
  ADD COLUMN app jsonb,
  ADD COLUMN status_digest text,
  ADD COLUMN status_proposed_by text,
  ADD COLUMN status_approved_by text,
  ADD COLUMN status_changed_at timestamptz,
  ADD CONSTRAINT pack_install_status_sod CHECK (
    (status_proposed_by IS NULL) = (status_approved_by IS NULL)
    AND (status_proposed_by IS NULL OR status_approved_by <> status_proposed_by)
  );
