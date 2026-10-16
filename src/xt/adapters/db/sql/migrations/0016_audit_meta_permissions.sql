-- SPDX-License-Identifier: Apache-2.0
-- SPDX-FileCopyrightText: 2026 xT contributors
--
-- R3-06: bảng quyền/danh tính/catalog vào hash-chain audit (xt_audit.install_meta2, cần
-- sql/audit_meta.sql phiên bản 2: CSDL cũ chạy tenancy.install_meta_audit() trước).
DO $$
DECLARE t record;
BEGIN
  IF to_regprocedure('xt_audit.install_meta2(name, text[], text[], text)') IS NULL THEN
    RAISE EXCEPTION 'thiếu xt_audit.install_meta2: chạy xt.adapters.db.tenancy.install_meta_audit()'
      ' bằng superuser rồi migrate lại';
  END IF;
  FOR t IN SELECT * FROM (VALUES
      ('person',          ARRAY['id'],                 '{}'::text[], 'tenant_id'),
      ('agent_grant',     ARRAY['person', 'client_id'], '{}',         'tenant_id'),
      ('tenant_profile',  ARRAY['tenant_id'],          '{}',          'tenant_id'),
      ('org_unit',        ARRAY['id'],                 '{}',          'tenant_id'),
      ('login_tenant',    ARRAY['login'],              '{}',          'tenant_id'),
      ('catalog_version', ARRAY['tenant_seq', 'version'], '{}',       'seq:tenant_seq'),
      ('tenant',          ARRAY['seq'],                '{}',          'self:id')
    ) v(tbl, key_cols, redact, tenant_col)
  LOOP
    IF to_regclass('xt_meta.' || t.tbl) IS NULL THEN
      CONTINUE;
    END IF;
    EXECUTE format('GRANT TRIGGER ON xt_meta.%I TO xt_audit_owner', t.tbl);
    PERFORM xt_audit.install_meta2(t.tbl::name, t.key_cols, t.redact, t.tenant_col);
  END LOOP;
END $$;
