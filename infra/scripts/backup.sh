#!/usr/bin/env bash
set -Eeuo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/lib.sh"

ensure_repo
acquire_operation_lock
ensure_clean_source
validate_production_env
select_release_image_tag accepted-only
require_command sha256sum
require_command tar
require_command date
require_command realpath
require_command rm
require_command ln
require_command stat
umask 077

db_name="$(read_env_value POSTGRES_DB || printf 'connectmd')"
backup_root_path="$(backup_root)"
minimum_free_bytes="$(read_env_value CONNECTMD_BACKUP_MIN_FREE_BYTES || printf '2147483648')"
retention_days="$(read_env_value CONNECTMD_BACKUP_RETENTION_DAYS || printf '30')"
case "$minimum_free_bytes" in "" | *[!0-9]*) die "CONNECTMD_BACKUP_MIN_FREE_BYTES must be a non-negative integer" ;; esac
case "$retention_days" in "" | 0 | *[!0-9]*) die "CONNECTMD_BACKUP_RETENTION_DAYS must be a positive integer" ;; esac
available_kib="$(df -Pk -- "$backup_root_path" | awk 'END {print $4}')"
case "$available_kib" in "" | *[!0-9]*) die "Could not determine free backup filesystem space" ;; esac
available_bytes=$((available_kib * 1024))
[ "$available_bytes" -ge "$minimum_free_bytes" ] || die "Backup filesystem is below CONNECTMD_BACKUP_MIN_FREE_BYTES"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
generation_id="connectmd-$timestamp"
destination="$backup_root_path/$generation_id"
staging=""
lifecycle_root="$backup_root_path/.connectmd-lifecycle"
registration_root="$lifecycle_root/registrations"
retirement_root="$lifecycle_root/retirements"
journal_root="$lifecycle_root/deletion-journal"
for lifecycle_directory in "$lifecycle_root" "$registration_root" "$retirement_root" "$journal_root"; do
  [ ! -L "$lifecycle_directory" ] || die "Lifecycle evidence directory must not be a symlink: $lifecycle_directory"
done
mkdir -p "$registration_root" "$retirement_root"
for lifecycle_directory in "$lifecycle_root" "$registration_root" "$retirement_root" "$journal_root"; do
  [ "$(realpath -e "$lifecycle_directory")" = "$lifecycle_directory" ] || die "Lifecycle evidence directory path is invalid: $lifecycle_directory"
done
chmod 700 "$lifecycle_root" "$registration_root" "$retirement_root" "$journal_root"
assert_active_release_identity
assert_no_pending_staged_release
release_source_revision="$RELEASE_SOURCE_REVISION"
release_image_tag="$RELEASE_IMAGE_TAG"
release_api_image_id="$RELEASE_API_IMAGE_ID"
release_web_image_id="$RELEASE_WEB_IMAGE_ID"
release_nginx_image_id="$RELEASE_NGINX_IMAGE_ID"
release_receipt_digest="$RELEASE_RECEIPT_DIGEST"
release_acceptance_digest="$RELEASE_ACCEPTANCE_DIGEST"
api_was_running=false
worker_should_run=false
worker_should_pause=false
services_stopped=false
restart_allowed=true

metadata_value() {
  local key="$1" file="$2" lines value
  lines="$(grep -E "^${key}=" "$file" || true)"
  [ -n "$lines" ] || die "Backup metadata is missing $key"
  [ "$(printf '%s\n' "$lines" | wc -l | tr -d ' ')" = "1" ] || die "Backup metadata has multiple $key values"
  value="${lines#*=}"
  [ -n "$value" ] || die "Backup metadata has an empty $key"
  printf '%s' "$value"
}

digest_of() {
  local file="$1" digest
  digest="$(sha256sum "$file" | awk '{print $1}')"
  case "$digest" in
    [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]* ) ;;
    *) die "Could not calculate SHA-256 for $file" ;;
  esac
  [ "${#digest}" = "64" ] || die "Could not calculate SHA-256 for $file"
  printf '%s' "$digest"
}

receipt_matches() {
  local receipt="$1" expected_generation="$2" expected_created="$3" expected_expires="$4" expected_db_digest="$5" expected_markdown_digest="$6" expected_journal_sequence="$7" expected_journal_digest="$8"
  [ -f "$receipt" ] || return 1
  [ ! -L "$receipt" ] || die "Backup registration receipt must not be a symlink: $receipt"
  [ "$(stat -c '%a' "$receipt")" = "600" ] || die "Backup registration receipt permissions are unsafe: $receipt"
  [ "$(wc -l < "$receipt" | tr -d ' ')" = "8" ] || die "Backup registration receipt contains unsupported fields: $receipt"
  [ "$(metadata_value generation_id "$receipt")" = "$expected_generation" ] || die "Backup registration receipt generation does not match: $receipt"
  [ "$(metadata_value created_at "$receipt")" = "$expected_created" ] || die "Backup registration receipt creation time does not match: $receipt"
  [ "$(metadata_value expires_at "$receipt")" = "$expected_expires" ] || die "Backup registration receipt expiry does not match: $receipt"
  [ "$(metadata_value db_manifest_digest "$receipt")" = "$expected_db_digest" ] || die "Backup registration receipt PostgreSQL digest does not match: $receipt"
  [ "$(metadata_value markdown_manifest_digest "$receipt")" = "$expected_markdown_digest" ] || die "Backup registration receipt Markdown digest does not match: $receipt"
  [ "$(metadata_value deletion_journal_head_sequence "$receipt")" = "$expected_journal_sequence" ] || die "Backup registration receipt deletion journal sequence does not match: $receipt"
  [ "$(metadata_value deletion_journal_head_digest "$receipt")" = "$expected_journal_digest" ] || die "Backup registration receipt deletion journal digest does not match: $receipt"
  grep -Eq '^registered_at=[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$' "$receipt" || die "Backup registration receipt timestamp is invalid: $receipt"
}

retirement_completion_matches() {
  local proof="$1" completion="$2" generation="$3" expected_proof_digest
  [ -f "$proof" ] && [ ! -L "$proof" ] || die "Backup retirement proof path is unsafe: $proof"
  [ -f "$completion" ] && [ ! -L "$completion" ] || die "Pending backup retirement proof requires explicit recovery before another backup: $proof"
  [ "$(stat -c '%a' "$proof")" = "600" ] || die "Backup retirement proof permissions are unsafe: $proof"
  [ "$(stat -c '%a' "$completion")" = "600" ] || die "Backup retirement completion permissions are unsafe: $completion"
  [ "$(wc -l < "$proof" | tr -d ' ')" = "9" ] || die "Backup retirement proof contains unsupported fields: $proof"
  [ "$(wc -l < "$completion" | tr -d ' ')" = "4" ] || die "Backup retirement completion contains unsupported fields: $completion"
  [ "$(metadata_value format "$proof")" = "connectmd-backup-retirement-proof-v1" ] || die "Backup retirement proof format is unsupported: $proof"
  [ "$(metadata_value generation_id "$proof")" = "$generation" ] || die "Backup retirement proof generation does not match: $proof"
  grep -Eq '^retired_at=[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$' "$proof" || die "Backup retirement proof timestamp is invalid: $proof"
  [ "$(metadata_value format "$completion")" = "connectmd-backup-retirement-complete-v1" ] || die "Backup retirement completion format is unsupported: $completion"
  [ "$(metadata_value generation_id "$completion")" = "$generation" ] || die "Backup retirement completion generation does not match: $completion"
  expected_proof_digest="$(digest_of "$proof")"
  [ "$(metadata_value proof_digest "$completion")" = "$expected_proof_digest" ] || die "Backup retirement completion digest does not match: $completion"
  grep -Eq '^expired_at=[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$' "$completion" || die "Backup retirement completion timestamp is invalid: $completion"
}

write_atomic_record() {
  local target="$1" directory="$2" record_name temporary
  record_name="$(basename "$target")"
  case "$directory" in
    "$registration_root") printf '%s' "$record_name" | grep -Eq '^connectmd-[0-9]{8}T[0-9]{6}Z\.env$' || die "Registration record name is invalid" ;;
    "$retirement_root") printf '%s' "$record_name" | grep -Eq '^connectmd-[0-9]{8}T[0-9]{6}Z\.(proof|expired)$' || die "Retirement record name is invalid" ;;
    *) die "Lifecycle record directory is invalid" ;;
  esac
  [ "$(dirname "$target")" = "$directory" ] || die "Lifecycle record target escaped its directory"
  temporary="$(mktemp "$directory/.record.XXXXXX")"
  case "$temporary" in "$directory"/.record.*) ;; *) die "Lifecycle record temporary path is invalid" ;; esac
  chmod 600 "$temporary"
  cat > "$temporary"
  if ! ln "$temporary" "$target"; then
    rm -f -- "$temporary"
    die "Refusing to overwrite durable lifecycle record: $target"
  fi
  rm -f -- "$temporary"
}

stop_api_and_erasure_worker() {
  services_stopped=true
  compose --profile account-lifecycle stop account-erasure-worker api >/dev/null
}

assert_no_artifact_staging() {
  if ! compose run --rm --no-deps -T api python -c '
try:
    from app.services.storage import VersionStore
    scan = VersionStore("/app/storage").scan_staged_artifacts()
except Exception:
    raise SystemExit(2)
if scan.descriptors or scan.incomplete_payloads or scan.invalid_entry or scan.overbound:
    raise SystemExit(1)
'; then
    die "Artifact staging preflight did not prove an empty namespace"
  fi
}

restart_original_services_on_exit() {
  local status=$?
  trap - EXIT
  if [ "$services_stopped" = true ] && [ "$restart_allowed" = true ]; then
    if ! (restore_original_services); then
      compose --profile account-lifecycle stop account-erasure-worker api >/dev/null 2>&1 || true
      printf 'ERROR: pre-mutation backup failure could not restore healthy prior service intent; API and account-erasure worker remain stopped.\n' >&2
    fi
  elif [ "$services_stopped" = true ]; then
    compose --profile account-lifecycle stop account-erasure-worker api >/dev/null 2>&1 || true
    printf 'ERROR: API and account-erasure worker remain stopped for explicit lifecycle recovery.\n' >&2
  fi
  exit "$status"
}
trap restart_original_services_on_exit EXIT

restore_original_services() {
  if [ "$api_was_running" = true ]; then
    compose up -d --no-build api >/dev/null
    wait_for_service api
  fi
  if [ "$worker_should_run" = true ]; then
    compose --profile account-lifecycle up -d --no-build account-erasure-worker >/dev/null
    wait_for_profiled_service account-lifecycle account-erasure-worker
    if [ "$worker_should_pause" = true ]; then
      compose --profile account-lifecycle pause account-erasure-worker
      [ "$(profiled_service_state account-lifecycle account-erasure-worker)" = "paused" ] || die "account-erasure-worker did not return to its prior paused state"
    fi
  fi
}

write_registration_receipt() {
  local receipt="$registration_root/$generation_id.env"
  if [ -e "$receipt" ]; then
    receipt_matches "$receipt" "$generation_id" "$created_at" "$expires_at" "$db_manifest_digest" "$markdown_manifest_digest" "$journal_head_sequence" "$journal_head_digest"
    return
  fi
  write_atomic_record "$receipt" "$registration_root" <<EOF
generation_id=$generation_id
created_at=$created_at
expires_at=$expires_at
db_manifest_digest=$db_manifest_digest
markdown_manifest_digest=$markdown_manifest_digest
deletion_journal_head_sequence=$journal_head_sequence
deletion_journal_head_digest=$journal_head_digest
registered_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF
  receipt_matches "$receipt" "$generation_id" "$created_at" "$expires_at" "$db_manifest_digest" "$markdown_manifest_digest" "$journal_head_sequence" "$journal_head_digest"
}

register_generation_with_cli() {
  local registered_generation="$1" registered_created="$2" registered_expires="$3" registered_db_digest="$4" registered_markdown_digest="$5"
  compose --profile account-lifecycle run --rm --no-deps -T api python -m app.cli account-backup register \
    --generation-id "$registered_generation" \
    --created-at "$registered_created" \
    --expires-at "$registered_expires" \
    --db-manifest-digest "$registered_db_digest" \
    --markdown-manifest-digest "$registered_markdown_digest"
}

assert_no_pending_retirements() {
  local proof generation completion
  for proof in "$retirement_root"/connectmd-*.proof; do
    if [ ! -e "$proof" ] && [ ! -L "$proof" ]; then
      continue
    fi
    [ -f "$proof" ] && [ ! -L "$proof" ] || die "Backup retirement proof path is unsafe: $proof"
    generation="$(basename "${proof%.proof}")"
    completion="$retirement_root/$generation.expired"
    retirement_completion_matches "$proof" "$completion" "$generation"
  done
  for completion in "$retirement_root"/connectmd-*.expired; do
    if [ ! -e "$completion" ] && [ ! -L "$completion" ]; then
      continue
    fi
    generation="$(basename "${completion%.expired}")"
    proof="$retirement_root/$generation.proof"
    retirement_completion_matches "$proof" "$completion" "$generation"
  done
}

assert_existing_backup_artifacts_registered() {
  local candidate resolved_candidate candidate_generation receipt candidate_metadata_generation candidate_created candidate_expires candidate_db_digest candidate_markdown_digest candidate_journal_sequence candidate_journal_digest
  for candidate in "$backup_root_path"/connectmd-* "$backup_root_path"/.connectmd-*; do
    if [ ! -e "$candidate" ] && [ ! -L "$candidate" ]; then
      continue
    fi
    [ ! -L "$candidate" ] || die "Backup artifact must not be a symlink: $candidate"
    [ -d "$candidate" ] || die "Unknown backup artifact requires explicit recovery before services can restart: $candidate"
    resolved_candidate="$(backup_directory "$candidate")"
    candidate_generation="$(basename "$resolved_candidate")"
    case "$candidate_generation" in
      .connectmd-lifecycle) continue ;;
      .connectmd-*) die "Unregistered staged backup data requires explicit recovery before services can restart: $resolved_candidate" ;;
    esac
    printf '%s' "$candidate_generation" | grep -Eq '^connectmd-[0-9]{8}T[0-9]{6}Z$' || die "Unrecognized backup directory requires explicit recovery before services can restart: $resolved_candidate"
    verify_backup "$resolved_candidate"
    receipt="$registration_root/$candidate_generation.env"
    [ -f "$receipt" ] || die "Unregistered backup data requires explicit registration recovery before services can restart: $resolved_candidate"
    candidate_metadata_generation="$(metadata_value generation_id "$resolved_candidate/metadata.env")"
    [ "$candidate_metadata_generation" = "$candidate_generation" ] || die "Registered backup generation metadata does not match its directory: $resolved_candidate"
    candidate_created="$(metadata_value created_at "$resolved_candidate/metadata.env")"
    candidate_expires="$(metadata_value expires_at "$resolved_candidate/metadata.env")"
    candidate_db_digest="$(digest_of "$resolved_candidate/postgres.dump")"
    candidate_markdown_digest="$(digest_of "$resolved_candidate/markdown-storage.tar.gz")"
    candidate_journal_sequence="$(metadata_value deletion_journal_head_sequence "$resolved_candidate/metadata.env")"
    candidate_journal_digest="$(metadata_value deletion_journal_head_digest "$resolved_candidate/metadata.env")"
    receipt_matches "$receipt" "$candidate_generation" "$candidate_created" "$candidate_expires" "$candidate_db_digest" "$candidate_markdown_digest" "$candidate_journal_sequence" "$candidate_journal_digest"
    register_generation_with_cli "$candidate_generation" "$candidate_created" "$candidate_expires" "$candidate_db_digest" "$candidate_markdown_digest"
  done
}

retire_verified_generation() {
  local candidate="$1" resolved_candidate candidate_generation receipt candidate_metadata_generation candidate_created candidate_expires candidate_db_digest candidate_markdown_digest candidate_journal_sequence candidate_journal_digest expiry_epoch now_epoch proof completion proof_digest
  [ ! -L "$candidate" ] || die "Backup retirement target must not be a symlink: $candidate"
  resolved_candidate="$(backup_directory "$candidate")"
  [ "$(dirname "$resolved_candidate")" = "$backup_root_path" ] || die "Backup retirement target must be directly below CONNECTMD_BACKUP_DIR"
  candidate_generation="$(basename "$resolved_candidate")"
  printf '%s' "$candidate_generation" | grep -Eq '^connectmd-[0-9]{8}T[0-9]{6}Z$' || die "Backup retirement target has an invalid generation ID: $candidate_generation"
  [ "$candidate_generation" != "$generation_id" ] || return
  verify_backup "$resolved_candidate"
  receipt="$registration_root/$candidate_generation.env"
  [ -f "$receipt" ] || die "Backup retirement target is not registered: $resolved_candidate"
  candidate_metadata_generation="$(metadata_value generation_id "$resolved_candidate/metadata.env")"
  [ "$candidate_metadata_generation" = "$candidate_generation" ] || die "Registered backup generation metadata does not match its directory: $resolved_candidate"
  candidate_created="$(metadata_value created_at "$resolved_candidate/metadata.env")"
  candidate_expires="$(metadata_value expires_at "$resolved_candidate/metadata.env")"
  candidate_db_digest="$(digest_of "$resolved_candidate/postgres.dump")"
  candidate_markdown_digest="$(digest_of "$resolved_candidate/markdown-storage.tar.gz")"
  candidate_journal_sequence="$(metadata_value deletion_journal_head_sequence "$resolved_candidate/metadata.env")"
  candidate_journal_digest="$(metadata_value deletion_journal_head_digest "$resolved_candidate/metadata.env")"
  receipt_matches "$receipt" "$candidate_generation" "$candidate_created" "$candidate_expires" "$candidate_db_digest" "$candidate_markdown_digest" "$candidate_journal_sequence" "$candidate_journal_digest"
  expiry_epoch="$(date -u -d "$candidate_expires" +%s 2>/dev/null)" || die "Backup registration receipt expiry is invalid: $receipt"
  now_epoch="$(date -u +%s)"
  [ "$now_epoch" -ge "$expiry_epoch" ] || return

  proof="$retirement_root/$candidate_generation.proof"
  completion="$retirement_root/$candidate_generation.expired"
  [ ! -e "$proof" ] || die "Backup retirement proof already exists; recover its CLI transition before retrying: $proof"
  [ ! -e "$completion" ] || die "Backup retirement completion exists without its proof: $completion"
  restart_allowed=false
  write_atomic_record "$proof" "$retirement_root" <<EOF
format=connectmd-backup-retirement-proof-v1
generation_id=$candidate_generation
created_at=$candidate_created
expires_at=$candidate_expires
retired_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
metadata_digest=$(digest_of "$resolved_candidate/metadata.env")
db_manifest_digest=$candidate_db_digest
markdown_manifest_digest=$candidate_markdown_digest
registration_receipt_digest=$(digest_of "$receipt")
EOF
  proof_digest="$(digest_of "$proof")"

  # Revalidate the exact, immediate-child target immediately before deletion.
  resolved_candidate="$(backup_directory "$resolved_candidate")"
  [ "$(dirname "$resolved_candidate")" = "$backup_root_path" ] || die "Backup retirement target escaped CONNECTMD_BACKUP_DIR"
  [ "$(basename "$resolved_candidate")" = "$candidate_generation" ] || die "Backup retirement target changed"
  verify_backup "$resolved_candidate"
  [ "$(metadata_value generation_id "$resolved_candidate/metadata.env")" = "$candidate_generation" ] || die "Backup retirement target metadata changed"
  [ "$(metadata_value created_at "$resolved_candidate/metadata.env")" = "$candidate_created" ] || die "Backup retirement target creation time changed"
  [ "$(metadata_value expires_at "$resolved_candidate/metadata.env")" = "$candidate_expires" ] || die "Backup retirement target expiry changed"
  [ "$(digest_of "$resolved_candidate/postgres.dump")" = "$candidate_db_digest" ] || die "Backup retirement target PostgreSQL digest changed"
  [ "$(digest_of "$resolved_candidate/markdown-storage.tar.gz")" = "$candidate_markdown_digest" ] || die "Backup retirement target Markdown digest changed"
  receipt_matches "$receipt" "$candidate_generation" "$candidate_created" "$candidate_expires" "$candidate_db_digest" "$candidate_markdown_digest" "$candidate_journal_sequence" "$candidate_journal_digest"
  rm -rf -- "$resolved_candidate"
  [ ! -e "$resolved_candidate" ] || die "Backup retirement directory was not completely removed: $resolved_candidate"

  compose --profile account-lifecycle run --rm --no-deps -T api python -m app.cli account-backup expire \
    --generation-id "$candidate_generation" \
    --proof-digest "$proof_digest"
  write_atomic_record "$completion" "$retirement_root" <<EOF
format=connectmd-backup-retirement-complete-v1
generation_id=$candidate_generation
proof_digest=$proof_digest
expired_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF
}

service_is_running api || die "API must be running before a consistent backup is taken"
api_was_running=true
worker_prior_state="$(profiled_service_state account-lifecycle account-erasure-worker)"
lifecycle_enabled="$(read_env_optional_value CONNECTMD_ACCOUNT_LIFECYCLE_ENABLED)" || die "CONNECTMD_ACCOUNT_LIFECYCLE_ENABLED must appear at most once in .env"
if [ "${lifecycle_enabled:-false}" = "true" ]; then
  worker_should_run=true
fi
case "$worker_prior_state" in
  running | restarting) worker_should_run=true ;;
  paused)
    worker_should_run=true
    worker_should_pause=true
    ;;
esac

# Stopping API and the deletion-capable worker serializes the coupled data capture.
stop_api_and_erasure_worker
if service_is_active api || profiled_service_is_active account-lifecycle account-erasure-worker; then
  die "API and account-erasure-worker must stop before artifact staging preflight"
fi
assert_no_artifact_staging
compose --profile account-lifecycle run --rm --no-deps -T api python -m app.cli deletion-journal verify-live
journal_checkpoint="$(compose --profile account-lifecycle run --rm --no-deps -T api python -m app.cli deletion-journal checkpoint)"
[ "$(printf '%s\n' "$journal_checkpoint" | wc -l | tr -d ' ')" = "2" ] || die "Deletion journal checkpoint output is invalid"
journal_head_sequence="$(printf '%s\n' "$journal_checkpoint" | grep -E '^deletion_journal_head_sequence=' | cut -d= -f2-)"
journal_head_digest="$(printf '%s\n' "$journal_checkpoint" | grep -E '^deletion_journal_head_digest=' | cut -d= -f2-)"
case "$journal_head_sequence" in "" | *[!0-9]*) die "Deletion journal checkpoint sequence is invalid" ;; esac
printf '%s' "$journal_head_digest" | grep -Eq '^[0-9a-f]{64}$' || die "Deletion journal checkpoint digest is invalid"
assert_no_pending_retirements
[ ! -e "$destination" ] || die "Backup destination already exists: $destination"
restart_allowed=false
assert_existing_backup_artifacts_registered
staging="$(mktemp -d "$backup_root_path/.connectmd-$timestamp.XXXXXX")"
verify_database_roles
compose --profile database-operations run --rm --no-deps -T database-backup \
  pg_dump -Fc --no-owner --no-privileges "$db_name" > "$staging/postgres.dump"
compose --profile ops run --rm --no-deps -T storage-backup tar -C /storage -czf - . > "$staging/markdown-storage.tar.gz"

created_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
expires_at="$(date -u -d "$created_at + $retention_days days" +%Y-%m-%dT%H:%M:%SZ)" || die "Could not calculate backup expiry timestamp"
{
  printf 'format=connectmd-backup-v3\n'
  printf 'generation_id=%s\n' "$generation_id"
  printf 'created_at=%s\n' "$created_at"
  printf 'expires_at=%s\n' "$expires_at"
  printf 'source_revision=%s\n' "$release_source_revision"
  printf 'image_tag=%s\n' "$release_image_tag"
  printf 'api_image_id=%s\n' "$release_api_image_id"
  printf 'web_image_id=%s\n' "$release_web_image_id"
  printf 'nginx_image_id=%s\n' "$release_nginx_image_id"
  printf 'release_receipt_digest=%s\n' "$release_receipt_digest"
  printf 'acceptance_receipt_digest=%s\n' "$release_acceptance_digest"
  printf 'postgres_database=%s\n' "$db_name"
  printf 'deletion_journal_head_sequence=%s\n' "$journal_head_sequence"
  printf 'deletion_journal_head_digest=%s\n' "$journal_head_digest"
} > "$staging/metadata.env"
(
  cd "$staging"
  sha256sum metadata.env postgres.dump markdown-storage.tar.gz > SHA256SUMS
)
verify_backup "$staging"
mv "$staging" "$destination"
verify_backup "$destination"
db_manifest_digest="$(digest_of "$destination/postgres.dump")"
markdown_manifest_digest="$(digest_of "$destination/markdown-storage.tar.gz")"

# The CLI commit is the authority; the local receipt is a durable, non-content recovery gate.
register_generation_with_cli "$generation_id" "$created_at" "$expires_at" "$db_manifest_digest" "$markdown_manifest_digest"
write_registration_receipt

for candidate in "$backup_root_path"/connectmd-*; do
  if [ ! -e "$candidate" ] && [ ! -L "$candidate" ]; then
    continue
  fi
  retire_verified_generation "$candidate"
done

restore_original_services
services_stopped=false
restart_allowed=true
printf 'BACKUP=%s\n' "$destination"
