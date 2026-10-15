-- SPDX-License-Identifier: Apache-2.0
-- SPDX-FileCopyrightText: 2026 xT contributors
-- Chạy bằng xt_migrator (xt db migrate). Kênh người xem giá trị thật PII (`/pii/reveal`,
-- research/spike-pii-lane.md §3.7): token biết nguồn (model.field) + mức (basic/sensitive) để kiểm
-- quyền xem; vé dùng một lần gắn khóa ECDSA của khung Desk.
ALTER TABLE xt_meta.pii_token
  ADD COLUMN field text,
  ADD COLUMN level text CHECK (level IN ('basic', 'sensitive'));
-- Cùng một giá trị ở nhiều field cho cùng token (HMAC theo giá trị): mức chỉ được nâng.
GRANT UPDATE (field, level) ON xt_meta.pii_token TO xt_app;

CREATE TABLE xt_meta.pii_reveal_ticket (
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
CREATE INDEX pii_reveal_ticket_expiry ON xt_meta.pii_reveal_ticket (expires_at);
ALTER TABLE xt_meta.pii_reveal_ticket ENABLE ROW LEVEL SECURITY;
ALTER TABLE xt_meta.pii_reveal_ticket FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON xt_meta.pii_reveal_ticket TO xt_app
  USING (tenant_id = xt.tenant()) WITH CHECK (tenant_id = xt.tenant());
GRANT SELECT, INSERT ON xt_meta.pii_reveal_ticket TO xt_app;
GRANT UPDATE (used_at) ON xt_meta.pii_reveal_ticket TO xt_app;

-- POST /pii/reveal không có token đăng nhập: tenant suy từ vé (sha256 của 256 bit ngẫu nhiên);
-- hàm chỉ lộ tenant của một vé còn hạn, chưa dùng (không lộ nội dung).
CREATE FUNCTION xt.pii_ticket_tenant(h bytea) RETURNS TABLE (seq int, id uuid)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
  SELECT t.seq, t.id FROM xt_meta.pii_reveal_ticket r JOIN xt_meta.tenant t ON t.id = r.tenant_id
   WHERE r.ticket_hash = h AND r.used_at IS NULL AND r.expires_at > clock_timestamp()
$$;
REVOKE ALL ON FUNCTION xt.pii_ticket_tenant(bytea) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION xt.pii_ticket_tenant(bytea) TO xt_app;

-- Người xem (kênh người, tier human): mỗi lần /pii/reveal ghi một dòng egress có person.
ALTER TABLE xt_meta.pii_egress ADD COLUMN person uuid;
