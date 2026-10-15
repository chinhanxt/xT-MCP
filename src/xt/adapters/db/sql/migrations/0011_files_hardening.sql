-- SPDX-License-Identifier: Apache-2.0
-- SPDX-FileCopyrightText: 2026 xT contributors
-- Chạy bằng xt_migrator. Sau audit dịch vụ tệp (docs/audit-files.md):
--   FB-01: quét ClamAV có số lần thử giới hạn -> trạng thái `scan_failed` (không kẹt `scanning`);
--   FB-02: quota theo người/tenant (đếm từ bảng) + dọn ý định hết hạn / byte quarantine / object
--          của tệp đã xóa mềm quá hạn lưu (sự kiện `purged`).

ALTER TABLE xt_meta.file_object ADD COLUMN IF NOT EXISTS scan_attempts integer NOT NULL DEFAULT 0
  CHECK (scan_attempts >= 0);
ALTER TABLE xt_meta.file_object DROP CONSTRAINT IF EXISTS file_object_status_check;
ALTER TABLE xt_meta.file_object ADD CONSTRAINT file_object_status_check CHECK (status IN (
  'awaiting_upload', 'uploaded', 'scanning', 'clean', 'not_scanned', 'infected', 'scan_failed',
  'expired'));
ALTER TABLE xt_meta.file_event DROP CONSTRAINT IF EXISTS file_event_action_check;
ALTER TABLE xt_meta.file_event ADD CONSTRAINT file_event_action_check CHECK (action IN (
  'created', 'uploaded', 'rejected', 'finalized', 'scanned', 'scan_failed', 'downloaded',
  'deleted', 'expired', 'purged'));
GRANT UPDATE (scan_attempts) ON xt_meta.file_object TO xt_app;

-- Quota: ý định đang mở + byte theo người / tenant; dọn: hàng hết hạn và hàng đã xóa mềm.
CREATE INDEX IF NOT EXISTS file_object_pending ON xt_meta.file_object (tenant_id, created_by)
  WHERE status IN ('awaiting_upload', 'uploaded') AND deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS file_object_expiry ON xt_meta.file_object (tenant_id, upload_expires_at)
  WHERE status IN ('awaiting_upload', 'uploaded');
CREATE INDEX IF NOT EXISTS file_object_deleted ON xt_meta.file_object (tenant_id, deleted_at)
  WHERE deleted_at IS NOT NULL AND object_key IS NOT NULL;
