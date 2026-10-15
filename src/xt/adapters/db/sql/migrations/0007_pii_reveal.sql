-- SPDX-License-Identifier: Apache-2.0
-- SPDX-FileCopyrightText: 2026 xT contributors
-- Chạy bằng xt_migrator (xt db migrate). Kênh người xem giá trị thật PII (`/pii/reveal`,
-- research/spike-pii-lane.md §3.7, docs/audit-pii-reveal.md).
-- Idempotent: CSDL dev đã áp bản cũ tên `0006_pii_reveal.sql` (trùng số với 0006_audit_meta) chạy
-- lại file này an toàn; CSDL mới áp một lần.

-- Vault: nguồn (model.field) + mức (basic/sensitive) để kiểm quyền xem.
ALTER TABLE xt_meta.pii_token ADD COLUMN IF NOT EXISTS field text;
ALTER TABLE xt_meta.pii_token ADD COLUMN IF NOT EXISTS level text;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'pii_token_level_check') THEN
    ALTER TABLE xt_meta.pii_token ADD CONSTRAINT pii_token_level_check
      CHECK (level IN ('basic', 'sensitive'));
  END IF;
END $$;
GRANT UPDATE (field, level) ON xt_meta.pii_token TO xt_app;

-- PIIR-02: token KHÔNG phải chìa khóa. Nguồn của token (bản ghi + field) để xem lại quyền đọc
-- bản ghi lúc đổi vé; "đã thấy" ràng token với người + client đã nhận nó.
CREATE TABLE IF NOT EXISTS xt_meta.pii_token_source (
  tenant_id uuid NOT NULL REFERENCES xt_meta.tenant(id) ON DELETE CASCADE,
  token     text NOT NULL,
  ref       text NOT NULL CHECK (char_length(ref) <= 200),   -- model:<id> hoặc person:<id>
  field     text NOT NULL,
  level     text NOT NULL CHECK (level IN ('basic', 'sensitive')),
  at        timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (tenant_id, token, ref, field)
);
CREATE TABLE IF NOT EXISTS xt_meta.pii_token_seen (
  tenant_id  uuid NOT NULL REFERENCES xt_meta.tenant(id) ON DELETE CASCADE,
  token      text NOT NULL,
  person     uuid NOT NULL,
  client_id  text NOT NULL,
  last_seen  timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (tenant_id, token, person, client_id)
);

CREATE TABLE IF NOT EXISTS xt_meta.pii_reveal_ticket (
  tenant_id   uuid NOT NULL REFERENCES xt_meta.tenant(id) ON DELETE CASCADE,
  ticket_hash bytea NOT NULL CHECK (octet_length(ticket_hash) = 32),
  person      uuid NOT NULL,
  client_id   text NOT NULL,
  tokens      text[] NOT NULL CHECK (cardinality(tokens) BETWEEN 1 AND 50),
  pubkey      jsonb NOT NULL,
  expires_at  timestamptz NOT NULL,
  used_at     timestamptz,
  created_at  timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (ticket_hash)
);
ALTER TABLE xt_meta.pii_reveal_ticket ADD COLUMN IF NOT EXISTS key_thumbprint bytea;
CREATE INDEX IF NOT EXISTS pii_reveal_ticket_expiry ON xt_meta.pii_reveal_ticket (expires_at);
CREATE INDEX IF NOT EXISTS pii_reveal_ticket_rate
  ON xt_meta.pii_reveal_ticket (tenant_id, person, created_at);
-- PIIR-07: mỗi lần "Hiện" là một khóa mới của khung; khóa dùng lại bị từ chối.
CREATE UNIQUE INDEX IF NOT EXISTS pii_reveal_ticket_key
  ON xt_meta.pii_reveal_ticket (tenant_id, key_thumbprint) WHERE key_thumbprint IS NOT NULL;

DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['pii_token_source', 'pii_token_seen', 'pii_reveal_ticket'] LOOP
    EXECUTE format('ALTER TABLE xt_meta.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE xt_meta.%I FORCE ROW LEVEL SECURITY', t);
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'xt_meta' AND tablename = t
                   AND policyname = 'tenant_isolation') THEN
      EXECUTE format('CREATE POLICY tenant_isolation ON xt_meta.%I TO xt_app USING '
                     '(tenant_id = xt.tenant()) WITH CHECK (tenant_id = xt.tenant())', t);
    END IF;
  END LOOP;
END $$;
GRANT SELECT, INSERT ON xt_meta.pii_token_source, xt_meta.pii_token_seen,
  xt_meta.pii_reveal_ticket TO xt_app;
GRANT UPDATE (last_seen) ON xt_meta.pii_token_seen TO xt_app;
GRANT UPDATE (used_at) ON xt_meta.pii_reveal_ticket TO xt_app;
-- PIIR-08: worker dọn vé hết hạn.
GRANT DELETE ON xt_meta.pii_reveal_ticket TO xt_app;

-- POST /pii/reveal không có token đăng nhập: tenant suy từ vé (sha256 của 256 bit ngẫu nhiên);
-- hàm chỉ lộ tenant của một vé còn hạn, chưa dùng (không lộ nội dung).
CREATE OR REPLACE FUNCTION xt.pii_ticket_tenant(h bytea) RETURNS TABLE (seq int, id uuid)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
  SELECT t.seq, t.id FROM xt_meta.pii_reveal_ticket r JOIN xt_meta.tenant t ON t.id = r.tenant_id
   WHERE r.ticket_hash = h AND r.used_at IS NULL AND r.expires_at > clock_timestamp()
$$;
REVOKE ALL ON FUNCTION xt.pii_ticket_tenant(bytea) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION xt.pii_ticket_tenant(bytea) TO xt_app;

-- PIIR-03: mỗi lần xin vé / đổi vé (kể cả thất bại) một dòng egress: người xem, kết quả, token
-- (không phải giá trị), bản ghi nguồn đã kiểm quyền, token bị từ chối.
ALTER TABLE xt_meta.pii_egress ADD COLUMN IF NOT EXISTS person uuid;
ALTER TABLE xt_meta.pii_egress ADD COLUMN IF NOT EXISTS outcome text;
ALTER TABLE xt_meta.pii_egress ADD COLUMN IF NOT EXISTS tokens text[];
ALTER TABLE xt_meta.pii_egress ADD COLUMN IF NOT EXISTS records text[];
ALTER TABLE xt_meta.pii_egress ADD COLUMN IF NOT EXISTS denied text[];
