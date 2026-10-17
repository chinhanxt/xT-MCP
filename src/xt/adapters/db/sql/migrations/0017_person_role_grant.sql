-- SPDX-License-Identifier: Apache-2.0
-- SPDX-FileCopyrightText: 2026 xT contributors
-- Chạy bằng xt_migrator (xt db migrate). D-UNI-H-1: vai trò cấp qua `access_grant` đã duyệt
-- (system keycloak) của gói nhân sự. Vai trò hiệu lực của person = `person.roles` (sơ đồ tổ chức,
-- `xt org import`) ∪ các dòng còn hiệu lực ở đây; đọc lại ở MỖI lời gọi (Runtime.resolve) nên thu
-- hồi có hiệu lực ngay lần kiểm quyền kế tiếp. `xt org import` không chạm bảng này.
-- SoD ở mức DB: người duyệt khác người được cấp và khác người đề nghị. Idempotent.

CREATE TABLE IF NOT EXISTS xt_meta.person_role_grant (
  tenant_id    uuid NOT NULL REFERENCES xt_meta.tenant(id) ON DELETE CASCADE,
  grant_ref    text NOT NULL CHECK (char_length(grant_ref) BETWEEN 1 AND 200),
  person       uuid NOT NULL,
  role         text NOT NULL CHECK (role ~ '^[a-z][a-z0-9_]{0,39}$'),
  requested_by uuid NOT NULL,
  approved_by  uuid NOT NULL,
  applied_at   timestamptz NOT NULL DEFAULT clock_timestamp(),
  revoked_at   timestamptz,
  revoked_by   uuid,
  PRIMARY KEY (tenant_id, grant_ref),
  CONSTRAINT role_grant_sod CHECK (approved_by <> person AND approved_by <> requested_by),
  FOREIGN KEY (tenant_id, person) REFERENCES xt_meta.person (tenant_id, id) ON DELETE CASCADE,
  FOREIGN KEY (tenant_id, approved_by) REFERENCES xt_meta.person (tenant_id, id)
);
CREATE INDEX IF NOT EXISTS person_role_grant_active ON xt_meta.person_role_grant
  (tenant_id, person) WHERE revoked_at IS NULL;

ALTER TABLE xt_meta.person_role_grant ENABLE ROW LEVEL SECURITY;
ALTER TABLE xt_meta.person_role_grant FORCE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'xt_meta'
                 AND tablename = 'person_role_grant' AND policyname = 'tenant_isolation') THEN
    CREATE POLICY tenant_isolation ON xt_meta.person_role_grant TO xt_app
      USING (tenant_id = xt.tenant()) WITH CHECK (tenant_id = xt.tenant());
  END IF;
END $$;
-- Không DELETE: thu hồi là cập nhật revoked_at (giữ lịch sử).
GRANT SELECT, INSERT ON xt_meta.person_role_grant TO xt_app;
GRANT UPDATE (person, role, requested_by, approved_by, applied_at, revoked_at, revoked_by)
  ON xt_meta.person_role_grant TO xt_app;

DO $$ BEGIN
  IF to_regprocedure('xt_audit.install_meta2(name, text[], text[], text)') IS NULL THEN
    RAISE EXCEPTION 'thiếu xt_audit.install_meta2: chạy xt.adapters.db.tenancy.install_meta_audit()'
      ' bằng superuser rồi migrate lại';
  END IF;
  GRANT TRIGGER ON xt_meta.person_role_grant TO xt_audit_owner;
  PERFORM xt_audit.install_meta2('person_role_grant', ARRAY['grant_ref'], '{}', 'tenant_id');
END $$;
