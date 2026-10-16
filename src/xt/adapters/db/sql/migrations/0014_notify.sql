-- SPDX-License-Identifier: Apache-2.0
-- SPDX-FileCopyrightText: 2026 xT contributors
-- Extend the existing inbox: get_inbox remains the only notification inbox API.
ALTER TABLE xt_meta.notification DROP CONSTRAINT IF EXISTS notification_title_check;
ALTER TABLE xt_meta.notification ADD CONSTRAINT notification_title_check
 CHECK (char_length(title) BETWEEN 1 AND 1000);
ALTER TABLE xt_meta.notification ADD CONSTRAINT notification_tenant_id UNIQUE (tenant_id,id);
ALTER TABLE xt_meta.notification ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'notification';
ALTER TABLE xt_meta.notification ADD COLUMN IF NOT EXISTS confirmation_code text;
ALTER TABLE xt_meta.notification ADD COLUMN IF NOT EXISTS confirm_url text;
ALTER TABLE xt_meta.notification ADD CONSTRAINT notification_kind_check
 CHECK (kind IN ('notification','assignment','escalation','confirmation_required'));
CREATE POLICY notification_reader ON xt_meta.notification AS RESTRICTIVE FOR SELECT TO xt_app
 USING (recipient = xt.actor() OR xt.has_role('xt_workflow'));
CREATE POLICY notification_marker ON xt_meta.notification AS RESTRICTIVE FOR UPDATE TO xt_app
 USING (recipient = xt.actor()) WITH CHECK (recipient = xt.actor());

CREATE TABLE xt_meta.notification_request (
 tenant_id uuid NOT NULL DEFAULT xt.tenant() REFERENCES xt_meta.tenant(id) ON DELETE CASCADE,
 actor uuid NOT NULL, key text NOT NULL, digest text NOT NULL, result jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY (tenant_id, actor, key)
);
CREATE TABLE xt_meta.notification_delivery (
 tenant_id uuid NOT NULL DEFAULT xt.tenant() REFERENCES xt_meta.tenant(id) ON DELETE CASCADE,
 id uuid PRIMARY KEY,
 actor uuid NOT NULL, recipient uuid NOT NULL, payload jsonb NOT NULL,
 state text NOT NULL DEFAULT 'queued' CHECK (state IN ('queued','sending','sent','failed')),
 external_id text, attempts int NOT NULL DEFAULT 0, updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(tenant_id,id) REFERENCES xt_meta.notification(tenant_id,id) ON DELETE CASCADE
);
CREATE INDEX notification_delivery_pending ON xt_meta.notification_delivery(tenant_id,state);
CREATE INDEX notification_delivery_rate ON xt_meta.notification_delivery(tenant_id,recipient,updated_at);
CREATE TABLE xt_meta.telegram_link_code (
 tenant_id uuid NOT NULL DEFAULT xt.tenant() REFERENCES xt_meta.tenant(id) ON DELETE CASCADE,
 hash bytea PRIMARY KEY CHECK (octet_length(hash)=32), person uuid NOT NULL,
 expires_at timestamptz NOT NULL, used_at timestamptz,
 FOREIGN KEY (tenant_id,person) REFERENCES xt_meta.person(tenant_id,id)
);
CREATE TABLE xt_meta.telegram_link (
 tenant_id uuid NOT NULL DEFAULT xt.tenant() REFERENCES xt_meta.tenant(id) ON DELETE CASCADE,
 person uuid NOT NULL, telegram_user_id text NOT NULL, chat_id text NOT NULL,
 linked_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(tenant_id,person), UNIQUE(tenant_id,telegram_user_id), UNIQUE(tenant_id,chat_id),
 FOREIGN KEY (tenant_id,person) REFERENCES xt_meta.person(tenant_id,id)
);
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['notification_request','notification_delivery','telegram_link_code','telegram_link'] LOOP
  EXECUTE format('ALTER TABLE xt_meta.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('ALTER TABLE xt_meta.%I FORCE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY tenant_isolation ON xt_meta.%I TO xt_app USING (tenant_id=xt.tenant()) WITH CHECK (tenant_id=xt.tenant())',t);
 END LOOP;
END $$;
CREATE POLICY request_actor ON xt_meta.notification_request AS RESTRICTIVE TO xt_app
 USING (actor=xt.actor() OR xt.has_role('xt_workflow'))
 WITH CHECK (actor=xt.actor() OR xt.has_role('xt_workflow'));
CREATE POLICY delivery_actor ON xt_meta.notification_delivery AS RESTRICTIVE TO xt_app
 USING (actor=xt.actor() OR recipient=xt.actor() OR xt.has_role('xt_workflow'))
 WITH CHECK (actor=xt.actor() OR xt.has_role('xt_workflow'));
CREATE POLICY code_actor ON xt_meta.telegram_link_code AS RESTRICTIVE TO xt_app
 USING (person=xt.actor()) WITH CHECK (person=xt.actor());
CREATE POLICY link_actor ON xt_meta.telegram_link AS RESTRICTIVE TO xt_app
 USING (person=xt.actor() OR xt.has_role('xt_workflow')) WITH CHECK (person=xt.actor());
GRANT SELECT,INSERT ON xt_meta.notification_request,xt_meta.notification_delivery,
 xt_meta.telegram_link_code,xt_meta.telegram_link TO xt_app;
GRANT UPDATE(state,attempts,external_id,updated_at) ON xt_meta.notification_delivery TO xt_app;
GRANT UPDATE(used_at) ON xt_meta.telegram_link_code TO xt_app;
GRANT UPDATE(telegram_user_id,chat_id,linked_at) ON xt_meta.telegram_link TO xt_app;

-- Capability lookup reveals only context for an unexpired 256-bit one-time code.
CREATE FUNCTION xt.telegram_code_context(h bytea) RETURNS TABLE(seq int,id uuid,person uuid)
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT t.seq,t.id,c.person FROM xt_meta.telegram_link_code c
 JOIN xt_meta.tenant t ON t.id=c.tenant_id JOIN xt_meta.person p ON p.tenant_id=c.tenant_id AND p.id=c.person
 WHERE c.hash=h AND c.used_at IS NULL AND c.expires_at>clock_timestamp() AND p.active
$$;
CREATE FUNCTION xt.telegram_summary_context(u text,c text,r text)
 RETURNS TABLE(seq int,id uuid,person uuid) LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=pg_catalog AS $$
 SELECT t.seq,t.id,l.person FROM xt_meta.telegram_link l
 JOIN xt_meta.tenant t ON t.id=l.tenant_id JOIN xt_meta.person p ON p.tenant_id=l.tenant_id AND p.id=l.person
 JOIN xt_meta.notification n ON n.tenant_id=l.tenant_id AND n.recipient=l.person
 WHERE l.telegram_user_id=u AND l.chat_id=c AND p.active AND (n.id::text=r OR n.confirmation_code=r)
 LIMIT 1
$$;
REVOKE ALL ON FUNCTION xt.telegram_code_context(bytea),xt.telegram_summary_context(text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION xt.telegram_code_context(bytea),xt.telegram_summary_context(text,text,text) TO xt_app;
-- Bot updates have no tenant until a link capability is consumed. Only narrow functions access this table.
CREATE TABLE xt_meta.telegram_update (id bigint PRIMARY KEY,at timestamptz NOT NULL DEFAULT clock_timestamp());
REVOKE ALL ON xt_meta.telegram_update FROM PUBLIC,xt_app;
CREATE FUNCTION xt.telegram_claim_update(i bigint) RETURNS boolean LANGUAGE plpgsql
 SECURITY DEFINER SET search_path=pg_catalog AS $$
 BEGIN
 DELETE FROM xt_meta.telegram_update WHERE at<clock_timestamp()-interval '7 days';
 INSERT INTO xt_meta.telegram_update(id) VALUES(i) ON CONFLICT DO NOTHING;
 RETURN FOUND;
 END
$$;
CREATE FUNCTION xt.telegram_release_update(i bigint) RETURNS void LANGUAGE sql
 SECURITY DEFINER SET search_path=pg_catalog AS $$ DELETE FROM xt_meta.telegram_update WHERE id=i $$;
REVOKE ALL ON FUNCTION xt.telegram_claim_update(bigint),xt.telegram_release_update(bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION xt.telegram_claim_update(bigint),xt.telegram_release_update(bigint) TO xt_app;
-- Notification content and linking identities must not appear in the hash-chain as plaintext.
GRANT TRIGGER ON xt_meta.notification,xt_meta.telegram_link TO xt_audit_owner;
SELECT xt_audit.install_meta('notification','id','{title}');
SELECT xt_audit.install_meta('telegram_link','person','{telegram_user_id,chat_id}');
-- Rate count spans all senders without exposing their messages to the caller.
CREATE FUNCTION xt.notification_rate(p uuid) RETURNS bigint LANGUAGE sql STABLE
 SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT count(*) FROM xt_meta.notification_delivery
 WHERE tenant_id=xt.tenant() AND recipient=p AND updated_at>clock_timestamp()-interval '1 minute'
$$;
REVOKE ALL ON FUNCTION xt.notification_rate(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION xt.notification_rate(uuid) TO xt_app;
