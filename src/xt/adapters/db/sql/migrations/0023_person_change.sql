-- SPDX-License-Identifier: Apache-2.0
-- SPDX-FileCopyrightText: 2026 xT contributors
-- Chạy bằng xt_migrator (xt db migrate). D-DEMO-LEVELS-1: `xt person set` đổi vai trò gốc
-- (`person.roles`) / cờ `active` của MỘT người, có kế hoạch + mã xác nhận như `xt pack install`.
-- `xt_meta.person` không nằm trong hash-chain audit (nạp hàng loạt bằng `xt org import`), nên mỗi
-- lần đổi ghi một dòng append-only ở đây (trước/sau, người đề xuất/duyệt, digest) trong CÙNG
-- transaction với UPDATE person; bảng này nằm trong hash-chain audit của tenant. Không xóa người.
-- Idempotent.

CREATE TABLE IF NOT EXISTS xt_meta.person_change (
  id          uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  tenant_id   uuid NOT NULL REFERENCES xt_meta.tenant(id) ON DELETE CASCADE,
  person      uuid NOT NULL,
  key         text NOT NULL CHECK (char_length(key) BETWEEN 1 AND 100),
  op          text NOT NULL CHECK (op IN ('person.set')),
  before      jsonb NOT NULL CHECK (octet_length(before::text) <= 4096),
  after       jsonb NOT NULL CHECK (octet_length(after::text) <= 4096),
  digest      text NOT NULL CHECK (digest ~ '^sha256:[0-9a-f]{64}$'),
  proposed_by text NOT NULL CHECK (char_length(proposed_by) BETWEEN 1 AND 200),
  approved_by text NOT NULL CHECK (char_length(approved_by) BETWEEN 1 AND 200),
  at          timestamptz NOT NULL DEFAULT clock_timestamp(),
  CONSTRAINT person_change_sod CHECK (proposed_by <> approved_by),
  FOREIGN KEY (tenant_id, person) REFERENCES xt_meta.person (tenant_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS person_change_by_person ON xt_meta.person_change (tenant_id, person, at);

ALTER TABLE xt_meta.person_change ENABLE ROW LEVEL SECURITY;
ALTER TABLE xt_meta.person_change FORCE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'xt_meta'
                 AND tablename = 'person_change' AND policyname = 'tenant_isolation') THEN
    CREATE POLICY tenant_isolation ON xt_meta.person_change TO xt_app
      USING (tenant_id = xt.tenant());
  END IF;
END $$;
-- xt_app chỉ đọc; chỉ lệnh quản trị (xt_migrator) ghi thêm. Không UPDATE/DELETE cho ai.
GRANT SELECT ON xt_meta.person_change TO xt_app;

DO $$ BEGIN
  IF to_regprocedure('xt_audit.install_meta2(name, text[], text[], text)') IS NULL THEN
    RAISE EXCEPTION 'thiếu xt_audit.install_meta2: chạy xt.adapters.db.tenancy.install_meta_audit()'
      ' bằng superuser rồi migrate lại';
  END IF;
  GRANT TRIGGER ON xt_meta.person_change TO xt_audit_owner;
  PERFORM xt_audit.install_meta2('person_change', ARRAY['id'], '{}', 'tenant_id');
END $$;
