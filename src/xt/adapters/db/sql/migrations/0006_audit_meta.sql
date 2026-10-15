-- SPDX-License-Identifier: Apache-2.0
-- SPDX-FileCopyrightText: 2026 xT contributors
--
-- I-AUD-META: đưa thay đổi metadata nền tảng vào hash-chain audit của tenant (xt_migrator).
-- Trigger thuộc xt_audit_owner, cài qua xt_audit.install_meta (sql/audit_meta.sql). CSDL bootstrap
-- trước khi có audit_meta.sql phải chạy `tenancy.install_meta_audit(superuser)` trước migration này.
DO $$
DECLARE t record;
BEGIN
  IF to_regprocedure('xt_audit.install_meta(name, name, text[])') IS NULL THEN
    RAISE EXCEPTION 'thiếu xt_audit.install_meta: chạy xt.adapters.db.tenancy.install_meta_audit()'
      ' bằng superuser rồi migrate lại';
  END IF;
  FOR t IN SELECT * FROM (VALUES
      ('iam_policy', 'id', '{}'::text[]),
      ('process_definition', 'id', '{}'),
      ('pack_install', 'id', '{}'),
      ('model_binding', 'id', '{}'),
      ('process_event', 'id', '{}'),
      ('pii_egress', 'id', '{}'),
      ('model_change_request', 'proposal_id', '{}'),
      -- pending/receipt: blob có thể chứa dữ liệu người dùng → chỉ lưu sha256
      ('tool_operation', 'id', '{pending,receipt}')
    ) v(tbl, key_col, redact)
  LOOP
    IF to_regclass('xt_meta.' || t.tbl) IS NULL THEN
      CONTINUE;  -- bảng chưa có ở bản cài này
    END IF;
    EXECUTE format('GRANT TRIGGER ON xt_meta.%I TO xt_audit_owner', t.tbl);
    PERFORM xt_audit.install_meta(t.tbl::name, t.key_col::name, t.redact);
  END LOOP;
END $$;
