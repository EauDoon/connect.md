-- Network MVP schema 0001: accounts, profiles, contact, conversations, agent grants.
-- Applies to PostgreSQL 14+. Every table is created idempotently by the runner.

CREATE TABLE IF NOT EXISTS network_accounts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  handle TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'deactivated')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS network_sessions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id UUID NOT NULL REFERENCES network_accounts(id) ON DELETE CASCADE,
  token_hash TEXT NOT NULL UNIQUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL,
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  revoked_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS network_sessions_account_idx ON network_sessions(account_id);

CREATE TABLE IF NOT EXISTS network_profiles (
  account_id UUID PRIMARY KEY REFERENCES network_accounts(id) ON DELETE CASCADE,
  markdown TEXT NOT NULL,
  etag TEXT NOT NULL,
  visibility TEXT NOT NULL DEFAULT 'private' CHECK (visibility IN ('private', 'public')),
  published_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS network_contact_requests (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  requester_id UUID NOT NULL REFERENCES network_accounts(id) ON DELETE CASCADE,
  recipient_id UUID NOT NULL REFERENCES network_accounts(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'rejected', 'revoked', 'blocked')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  decided_at TIMESTAMPTZ,
  CHECK (requester_id <> recipient_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS network_contact_requests_active_pair
  ON network_contact_requests(requester_id, recipient_id)
  WHERE status IN ('pending', 'accepted');

CREATE TABLE IF NOT EXISTS network_contact_blocks (
  blocker_id UUID NOT NULL REFERENCES network_accounts(id) ON DELETE CASCADE,
  blocked_id UUID NOT NULL REFERENCES network_accounts(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (blocker_id, blocked_id)
);
CREATE INDEX IF NOT EXISTS network_contact_blocks_blocked_idx ON network_contact_blocks(blocked_id);

CREATE TABLE IF NOT EXISTS network_conversations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  account_a UUID NOT NULL REFERENCES network_accounts(id) ON DELETE CASCADE,
  account_b UUID NOT NULL REFERENCES network_accounts(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (account_a <> account_b),
  UNIQUE (account_a, account_b)
);

CREATE TABLE IF NOT EXISTS network_messages (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id UUID NOT NULL REFERENCES network_conversations(id) ON DELETE CASCADE,
  sender_id UUID NOT NULL REFERENCES network_accounts(id) ON DELETE CASCADE,
  body TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS network_messages_conversation_idx ON network_messages(conversation_id, created_at);

CREATE TABLE IF NOT EXISTS network_agent_grants (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id UUID NOT NULL REFERENCES network_accounts(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE,
  token_prefix TEXT NOT NULL,
  scopes TEXT[] NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ,
  revoked_at TIMESTAMPTZ,
  last_used_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS network_agent_grants_account_idx ON network_agent_grants(account_id);

CREATE TABLE IF NOT EXISTS network_auth_buckets (
  bucket_key TEXT NOT NULL,
  window_started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  count INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (bucket_key)
);

CREATE TABLE IF NOT EXISTS network_schema_migrations (
  name TEXT PRIMARY KEY,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO network_schema_migrations(name) VALUES ('0001_network_mvp')
  ON CONFLICT (name) DO NOTHING;
