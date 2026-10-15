-- SPDX-License-Identifier: Apache-2.0
-- SPDX-FileCopyrightText: 2026 xT contributors
-- Chạy bằng xt_migrator (xt db migrate). Cache phát lại DPoP (RFC 9449 §11.1, audit DPA-02): jti đã
-- dùng theo khóa (jkt), DÙNG CHUNG mọi tiến trình/replica xt-api và sống qua restart. Không theo
-- tenant: kiểm proof chạy ở cổng xác thực, trước khi có actor. Bảng không cấp quyền trực tiếp cho ai;
-- xt_app chỉ gọi được hai hàm SECURITY DEFINER bên dưới (tiêu một jti; dọn mục hết hạn, job worker).
-- Idempotent.
CREATE SCHEMA IF NOT EXISTS xt_identity AUTHORIZATION xt_migrator;
REVOKE ALL ON SCHEMA xt_identity FROM PUBLIC;
GRANT USAGE ON SCHEMA xt_identity TO xt_app;

CREATE TABLE IF NOT EXISTS xt_identity.dpop_jti (
  jkt        text NOT NULL CHECK (char_length(jkt) BETWEEN 1 AND 64),
  jti        text NOT NULL CHECK (char_length(jti) BETWEEN 1 AND 256),
  expires_at timestamptz NOT NULL,
  PRIMARY KEY (jkt, jti)
);
CREATE INDEX IF NOT EXISTS dpop_jti_expiry ON xt_identity.dpop_jti (expires_at);
REVOKE ALL ON xt_identity.dpop_jti FROM PUBLIC;

-- true = lần dùng đầu (hoặc mục cũ đã hết hạn được thay); false = phát lại.
CREATE OR REPLACE FUNCTION xt_identity.dpop_jti_consume(p_jkt text, p_jti text, p_expires timestamptz)
RETURNS boolean LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
  INSERT INTO xt_identity.dpop_jti AS d (jkt, jti, expires_at) VALUES (p_jkt, p_jti, p_expires)
  ON CONFLICT (jkt, jti) DO UPDATE SET expires_at = EXCLUDED.expires_at
    WHERE d.expires_at <= clock_timestamp();
  RETURN FOUND;
END $$;
REVOKE ALL ON FUNCTION xt_identity.dpop_jti_consume(text, text, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION xt_identity.dpop_jti_consume(text, text, timestamptz) TO xt_app;

CREATE OR REPLACE FUNCTION xt_identity.dpop_jti_purge(p_limit integer) RETURNS integer
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = pg_catalog AS $$
  WITH gone AS (
    DELETE FROM xt_identity.dpop_jti WHERE ctid IN (
      SELECT ctid FROM xt_identity.dpop_jti WHERE expires_at <= clock_timestamp()
       ORDER BY expires_at LIMIT greatest(p_limit, 1))
    RETURNING 1)
  SELECT count(*)::integer FROM gone
$$;
REVOKE ALL ON FUNCTION xt_identity.dpop_jti_purge(integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION xt_identity.dpop_jti_purge(integer) TO xt_app;
