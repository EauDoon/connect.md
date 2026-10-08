-- Network schema 0003: indexes that keep expired auth state cheap to prune.
-- auth-service prunes rate buckets and dead sessions in bounded batches. The
-- prune is correct without these indexes; they only stop it from scanning.
CREATE INDEX IF NOT EXISTS network_auth_buckets_window_idx
  ON network_auth_buckets (window_started_at);
CREATE INDEX IF NOT EXISTS network_sessions_expires_idx
  ON network_sessions (expires_at);
CREATE INDEX IF NOT EXISTS network_sessions_revoked_idx
  ON network_sessions (revoked_at) WHERE revoked_at IS NOT NULL;
