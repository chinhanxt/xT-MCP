-- SPDX-License-Identifier: Apache-2.0
-- SPDX-FileCopyrightText: 2026 xT contributors
-- Chạy bằng xt_migrator (xt db migrate). Dữ liệu phi cấu trúc: metadata tệp trên SeaweedFS (S3).
-- Byte nằm ở object storage với khóa `t/<tenant_id>/sha256/<hh>/<hex>` (hoặc quarantine/<id> khi
-- chưa finalize/chưa quét); bảng này là nguồn sự thật về ai sở hữu, trạng thái quét, xóa mềm.
-- docs/files-api.md.

CREATE TABLE IF NOT EXISTS xt_meta.file_object (
  tenant_id         uuid NOT NULL DEFAULT xt.tenant() REFERENCES xt_meta.tenant(id) ON DELETE CASCADE,
  id                uuid NOT NULL,
  name              text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 255),
  declared_type     text NOT NULL CHECK (char_length(declared_type) BETWEEN 3 AND 127),
  content_type      text CHECK (char_length(content_type) <= 127),
  size_bytes        bigint CHECK (size_bytes >= 0),
  sha256            text CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  object_key        text CHECK (char_length(object_key) <= 300),
  status            text NOT NULL CHECK (status IN ('awaiting_upload', 'uploaded', 'scanning',
                      'clean', 'not_scanned', 'infected', 'expired')),
  scan_signature    text CHECK (char_length(scan_signature) <= 200),
  created_by        uuid NOT NULL,
  client_id         text CHECK (char_length(client_id) <= 200),
  created_at        timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at        timestamptz NOT NULL DEFAULT clock_timestamp(),
  upload_expires_at timestamptz,
  finalized_at      timestamptz,
  deleted_at        timestamptz,
  PRIMARY KEY (id),
  -- Đích FK ghép (tenant_id, id) của field kiểu `file` trong bảng model: chặn tham chiếu chéo
  -- tenant ở mức DB (compiler.fk_sql).
  CONSTRAINT file_object_tenant_id UNIQUE (tenant_id, id),
  CONSTRAINT file_object_finalized CHECK (
    status IN ('awaiting_upload', 'expired') OR (sha256 IS NOT NULL AND size_bytes IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS file_object_scanning ON xt_meta.file_object (tenant_id, created_at)
  WHERE status = 'scanning' AND deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS file_object_sha ON xt_meta.file_object (tenant_id, sha256);

CREATE TABLE IF NOT EXISTS xt_meta.file_event (
  tenant_id  uuid NOT NULL DEFAULT xt.tenant() REFERENCES xt_meta.tenant(id) ON DELETE CASCADE,
  id         uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  file_id    uuid NOT NULL,
  action     text NOT NULL CHECK (action IN ('created', 'uploaded', 'rejected', 'finalized',
               'scanned', 'downloaded', 'deleted', 'expired')),
  person     uuid,
  client_id  text,
  trace_id   text,
  detail     jsonb NOT NULL DEFAULT '{}',
  at         timestamptz NOT NULL DEFAULT clock_timestamp(),
  FOREIGN KEY (tenant_id, file_id) REFERENCES xt_meta.file_object (tenant_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS file_event_file ON xt_meta.file_event (tenant_id, file_id, at);

DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['file_object', 'file_event'] LOOP
    EXECUTE format('ALTER TABLE xt_meta.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE xt_meta.%I FORCE ROW LEVEL SECURITY', t);
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'xt_meta' AND tablename = t
                   AND policyname = 'tenant_isolation') THEN
      EXECUTE format('CREATE POLICY tenant_isolation ON xt_meta.%I TO xt_app USING '
                     '(tenant_id = xt.tenant()) WITH CHECK (tenant_id = xt.tenant())', t);
    END IF;
  END LOOP;
END $$;
-- Không có DELETE: xóa là xóa mềm (deleted_at); sự kiện chỉ ghi thêm.
GRANT SELECT, INSERT ON xt_meta.file_object, xt_meta.file_event TO xt_app;
GRANT UPDATE (content_type, size_bytes, sha256, object_key, status, scan_signature, updated_at,
  upload_expires_at, finalized_at, deleted_at) ON xt_meta.file_object TO xt_app;

-- Hash-chain audit của tenant (I-AUD-META). Tên tệp có thể mang dữ liệu cá nhân (vd "CCCD_<họ
-- tên>.pdf"): audit chỉ giữ sha256 của tên; tên thật chỉ ở bảng (RLS).
DO $$ BEGIN
  IF to_regprocedure('xt_audit.install_meta(name, name, text[])') IS NULL THEN
    RAISE EXCEPTION 'thiếu xt_audit.install_meta: chạy xt.adapters.db.tenancy.install_meta_audit()'
      ' bằng superuser rồi migrate lại';
  END IF;
  GRANT TRIGGER ON xt_meta.file_object, xt_meta.file_event TO xt_audit_owner;
  PERFORM xt_audit.install_meta('file_object', 'id', '{name}');
  PERFORM xt_audit.install_meta('file_event', 'id', '{}');
END $$;
