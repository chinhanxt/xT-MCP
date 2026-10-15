-- SPDX-License-Identifier: Apache-2.0
-- SPDX-FileCopyrightText: 2026 xT contributors
-- Chạy bằng xt_migrator (xt db migrate). Quyết định của người cho một đề xuất thay đổi model
-- (`xt_meta.model_proposal` bất biến): ghi cùng transaction của decide_request (sau /desk/confirm),
-- đồng thời là outbox để worker áp (`model.apply`: approve_and_apply + pha 2).
CREATE TABLE xt_meta.model_change_request (
  tenant_id     uuid NOT NULL REFERENCES xt_meta.tenant(id) ON DELETE CASCADE,
  proposal_id   uuid NOT NULL REFERENCES xt_meta.model_proposal(id) ON DELETE CASCADE,
  state         text NOT NULL CHECK (state IN ('approved','rejected','applied','failed')),
  plan_hash     text NOT NULL,
  confirm       text[] NOT NULL DEFAULT '{}',
  decided_by    text[] NOT NULL CHECK (cardinality(decided_by) >= 1),
  reason        text,
  decided_at    timestamptz NOT NULL DEFAULT clock_timestamp(),
  dispatched_at timestamptz,
  applied_version integer,
  error         text,
  updated_at    timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (tenant_id, proposal_id)
);
CREATE INDEX model_change_pending ON xt_meta.model_change_request (tenant_id, decided_at)
  WHERE state = 'approved' AND dispatched_at IS NULL;
ALTER TABLE xt_meta.model_change_request ENABLE ROW LEVEL SECURITY;
ALTER TABLE xt_meta.model_change_request FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON xt_meta.model_change_request TO xt_app
  USING (tenant_id = xt.tenant()) WITH CHECK (tenant_id = xt.tenant());
GRANT SELECT, INSERT, UPDATE ON xt_meta.model_change_request TO xt_app;
