-- SPDX-License-Identifier: Apache-2.0
-- SPDX-FileCopyrightText: 2026 xT contributors
-- Chạy bằng xt_migrator (xt db migrate). Idempotency + xác nhận của tool, bền trong Postgres.
-- Durable tool idempotency and confirmation state. The request transaction sets
-- the same RLS context as model writes; receipt, replay key and audit commit together.
CREATE TABLE xt_meta.tool_operation (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES xt_meta.tenant(id) ON DELETE CASCADE,
  actor_key text NOT NULL,
  tool text NOT NULL,
  idempotency_key text NOT NULL,
  fingerprint text NOT NULL,
  confirmation_id text UNIQUE,
  pending jsonb,
  pending_status text CHECK (pending_status IN ('pending','awaiting_second_approver',
      'confirmed','declined','expired','invalidated')),
  expires_at timestamptz,
  receipt jsonb,
  -- in_progress: đã claim trong transaction đang chạy (không bao giờ commit ở trạng thái này
  -- trừ khi tiến trình chết giữa chừng; khi đó transaction rollback và hàng biến mất).
  state text NOT NULL DEFAULT 'in_progress' CHECK (state IN ('in_progress','pending','done')),
  PRIMARY KEY (tenant_id,actor_key,tool,idempotency_key),
  UNIQUE (tenant_id,confirmation_id),
  CHECK (state = 'in_progress' OR pending IS NOT NULL OR receipt IS NOT NULL),
  CHECK ((pending IS NULL) = (confirmation_id IS NULL))
);
CREATE INDEX tool_pending_open ON xt_meta.tool_operation (tenant_id,actor_key,expires_at)
  WHERE pending_status IN ('pending','awaiting_second_approver');
ALTER TABLE xt_meta.tool_operation ENABLE ROW LEVEL SECURITY;
CREATE POLICY tool_operation_self ON xt_meta.tool_operation FOR ALL TO xt_app
  USING (tenant_id=xt.tenant()) WITH CHECK (tenant_id=xt.tenant());
GRANT SELECT,INSERT,UPDATE ON xt_meta.tool_operation TO xt_app;
-- Không gắn trigger audit: xt_audit.log chỉ nhận bảng model của schema tenant (R2-04). Mutation
-- mà receipt mô tả đã được audit ở bảng model; hash-chain cho xt_meta: docs/integration-notes.md.

CREATE TABLE xt_meta.single_use_key (
  key text PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES xt_meta.tenant(id) ON DELETE CASCADE,
  expires_at bigint
);
ALTER TABLE xt_meta.single_use_key ENABLE ROW LEVEL SECURITY;
CREATE POLICY single_use_self ON xt_meta.single_use_key FOR ALL TO xt_app
  USING (tenant_id=xt.tenant()) WITH CHECK (tenant_id=xt.tenant());
GRANT SELECT,INSERT,UPDATE ON xt_meta.single_use_key TO xt_app;

CREATE TABLE xt_meta.confirmation_binding (
  confirmation_id text PRIMARY KEY,
  tenant_id uuid NOT NULL,
  nonce text NOT NULL,
  request_digest text NOT NULL,
  requester_subject text NOT NULL CHECK (requester_subject<>''),
  created_at timestamptz NOT NULL,
  expires_at timestamptz NOT NULL,
  FOREIGN KEY (tenant_id,confirmation_id)
    REFERENCES xt_meta.tool_operation(tenant_id,confirmation_id) ON DELETE CASCADE
);
ALTER TABLE xt_meta.confirmation_binding ENABLE ROW LEVEL SECURITY;
CREATE POLICY confirmation_binding_self ON xt_meta.confirmation_binding FOR ALL TO xt_app
  USING (tenant_id=xt.tenant()) WITH CHECK (tenant_id=xt.tenant());
GRANT SELECT,INSERT ON xt_meta.confirmation_binding TO xt_app;

CREATE TABLE xt_meta.confirmation_session (
  state text PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES xt_meta.tenant(id) ON DELETE CASCADE,
  data jsonb NOT NULL,
  expires_at timestamptz NOT NULL
);
CREATE INDEX confirmation_session_expiry ON xt_meta.confirmation_session (expires_at);
ALTER TABLE xt_meta.confirmation_session ENABLE ROW LEVEL SECURITY;
CREATE POLICY confirmation_session_self ON xt_meta.confirmation_session FOR ALL TO xt_app
  USING (tenant_id=xt.tenant()) WITH CHECK (tenant_id=xt.tenant());
GRANT SELECT,INSERT,DELETE ON xt_meta.confirmation_session TO xt_app;
