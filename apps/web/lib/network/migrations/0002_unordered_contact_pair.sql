-- Fail without discarding history if an older installation has duplicate active pairs.
-- Resolve conflicting consent with both owners before retrying this migration.
CREATE UNIQUE INDEX network_contact_requests_unordered_active_pair
  ON network_contact_requests (LEAST(requester_id, recipient_id), GREATEST(requester_id, recipient_id))
  WHERE status IN ('pending', 'accepted');
