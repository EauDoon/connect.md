#!/usr/bin/env bash
set -Eeuo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/lib.sh"

usage() {
  printf 'Usage: %s <backup-directory> {--verify-only|--yes-restore}\n' "${0##*/}" >&2
  exit 64
}

[ "$#" -eq 2 ] || usage
ensure_repo
acquire_operation_lock
ensure_clean_source
validate_production_env
require_command sha256sum
require_command stat
require_command realpath
require_command date

metadata_value() {
  local key="$1" lines value
  lines="$(grep -E "^${key}=" "$directory/metadata.env" || true)"
  [ -n "$lines" ] || die "Backup metadata is missing $key"
  [ "$(printf '%s\n' "$lines" | wc -l | tr -d ' ')" = "1" ] || die "Backup metadata has multiple $key values"
  value="${lines#*=}"
  [ -n "$value" ] || die "Backup metadata has an empty $key"
  printf '%s' "$value"
}

verify_registration_receipt() {
  [ -f "$receipt" ] && [ ! -L "$receipt" ] || die "Backup registration receipt path is unsafe"
  [ "$(stat -c '%a' "$receipt")" = "600" ] || die "Backup registration receipt permissions are unsafe"
  [ "$(wc -l < "$receipt" | tr -d ' ')" = "8" ] || die "Backup registration receipt contains unsupported fields"
  grep -Fxq "generation_id=$generation_id" "$receipt" || die "Backup registration receipt generation does not match"
  grep -Fxq "created_at=$created_at" "$receipt" || die "Backup registration receipt creation time does not match"
  grep -Fxq "expires_at=$expires_at" "$receipt" || die "Backup registration receipt expiry does not match"
  grep -Fxq "db_manifest_digest=$db_manifest_digest" "$receipt" || die "Backup registration receipt PostgreSQL digest does not match"
  grep -Fxq "markdown_manifest_digest=$markdown_manifest_digest" "$receipt" || die "Backup registration receipt Markdown digest does not match"
  grep -Fxq "deletion_journal_head_sequence=$deletion_journal_head_sequence" "$receipt" || die "Backup registration receipt deletion journal sequence does not match"
  grep -Fxq "deletion_journal_head_digest=$deletion_journal_head_digest" "$receipt" || die "Backup registration receipt deletion journal digest does not match"
  grep -Eq '^registered_at=[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$' "$receipt" || die "Backup registration receipt timestamp is invalid"
}

managed_api_tag() {
  case "$1" in
    connectmd-api:*) printf '%s' "${1#connectmd-api:}" ;;
    *) die "Active service is not using a managed connectmd-api image: $1" ;;
  esac
}

managed_web_tag() {
  case "$1" in
    connectmd-web:*) printf '%s' "${1#connectmd-web:}" ;;
    *) die "Active service is not using a managed connectmd-web image: $1" ;;
  esac
}

managed_nginx_tag() {
  case "$1" in
    connectmd-nginx:*) printf '%s' "${1#connectmd-nginx:}" ;;
    *) die "Active service is not using a managed connectmd-nginx image: $1" ;;
  esac
}

state_is_active() {
  case "$1" in running | restarting | paused) return 0 ;; *) return 1 ;; esac
}

state_is_serving() {
  case "$1" in running | restarting) return 0 ;; *) return 1 ;; esac
}

normalize_prior_state() {
  case "$1" in
    running | restarting) printf 'running' ;;
    paused) printf 'paused' ;;
    "") printf 'absent' ;;
    *) printf 'stopped' ;;
  esac
}

write_restore_state() {
  local phase="$1" receipt_digest="$2" temporary
  local normalized_api_state normalized_converter_state normalized_projection_state
  local normalized_worker_state normalized_frontend_state normalized_nginx_state
  case "$phase" in in_progress | complete) ;; *) die "Restore state phase is invalid" ;; esac
  normalized_api_state="$(normalize_prior_state "$api_prior_state")"
  normalized_converter_state="$(normalize_prior_state "$converter_prior_state")"
  normalized_projection_state="$(normalize_prior_state "$projection_prior_state")"
  normalized_worker_state="$(normalize_prior_state "$worker_prior_state")"
  normalized_frontend_state="$(normalize_prior_state "$frontend_prior_state")"
  normalized_nginx_state="$(normalize_prior_state "$nginx_prior_state")"
  temporary="$(mktemp "$REPO_ROOT/.connectmd-restore-state.XXXXXX")"
  chmod 600 "$temporary"
  {
    printf 'format=connectmd-restore-state-v3\n'
    printf 'phase=%s\n' "$phase"
    printf 'search_rebuild_pending=true\n'
    printf 'backup_format=%s\n' "$backup_format"
    printf 'backup_acceptance_receipt_digest=%s\n' "${backup_acceptance_receipt_digest:-none}"
    printf 'generation_id=%s\n' "$generation_id"
    printf 'source_revision=%s\n' "$backup_source_revision"
    printf 'image_tag=%s\n' "$backup_image_tag"
    printf 'api_image_id=%s\n' "$backup_api_image_id"
    printf 'web_image_id=%s\n' "$backup_web_image_id"
    printf 'nginx_image_id=%s\n' "$backup_nginx_image_id"
    printf 'release_receipt_digest=%s\n' "$backup_release_receipt_digest"
    printf 'api_prior_state=%s\n' "$normalized_api_state"
    printf 'converter_prior_state=%s\n' "$normalized_converter_state"
    printf 'projection_prior_state=%s\n' "$normalized_projection_state"
    printf 'worker_prior_state=%s\n' "$normalized_worker_state"
    printf 'frontend_prior_state=%s\n' "$normalized_frontend_state"
    printf 'nginx_prior_state=%s\n' "$normalized_nginx_state"
    printf 'db_manifest_digest=%s\n' "$db_manifest_digest"
    printf 'markdown_manifest_digest=%s\n' "$markdown_manifest_digest"
    printf 'deletion_journal_head_sequence=%s\n' "$deletion_journal_head_sequence"
    printf 'deletion_journal_head_digest=%s\n' "$deletion_journal_head_digest"
    printf 'registration_receipt_digest=%s\n' "$receipt_digest"
  } > "$temporary"
  if [ "$phase" = "in_progress" ]; then
    [ ! -e "$RESTORE_STATE_FILE" ] && [ ! -L "$RESTORE_STATE_FILE" ] || { rm -f -- "$temporary"; die "A prior restore state requires deployment or explicit recovery"; }
    if ! ln "$temporary" "$RESTORE_STATE_FILE"; then
      rm -f -- "$temporary"
      die "Refusing to overwrite durable restore state"
    fi
  else
    [ -f "$RESTORE_STATE_FILE" ] && [ ! -L "$RESTORE_STATE_FILE" ] || { rm -f -- "$temporary"; die "In-progress restore state is missing or unsafe"; }
    grep -Fxq 'phase=in_progress' "$RESTORE_STATE_FILE" || { rm -f -- "$temporary"; die "Restore state is not awaiting completion"; }
    grep -Fxq "generation_id=$generation_id" "$RESTORE_STATE_FILE" || { rm -f -- "$temporary"; die "Restore state generation changed"; }
    grep -Fxq "source_revision=$backup_source_revision" "$RESTORE_STATE_FILE" || { rm -f -- "$temporary"; die "Restore state source revision changed"; }
    grep -Fxq "image_tag=$backup_image_tag" "$RESTORE_STATE_FILE" || { rm -f -- "$temporary"; die "Restore state image tag changed"; }
    grep -Fxq "api_image_id=$backup_api_image_id" "$RESTORE_STATE_FILE" || { rm -f -- "$temporary"; die "Restore state API image identity changed"; }
    grep -Fxq "web_image_id=$backup_web_image_id" "$RESTORE_STATE_FILE" || { rm -f -- "$temporary"; die "Restore state web image identity changed"; }
    grep -Fxq "nginx_image_id=$backup_nginx_image_id" "$RESTORE_STATE_FILE" || { rm -f -- "$temporary"; die "Restore state Nginx image identity changed"; }
    grep -Fxq "release_receipt_digest=$backup_release_receipt_digest" "$RESTORE_STATE_FILE" || { rm -f -- "$temporary"; die "Restore state release receipt changed"; }
    grep -Fxq "backup_format=$backup_format" "$RESTORE_STATE_FILE" || { rm -f -- "$temporary"; die "Restore state backup format changed"; }
    grep -Fxq "backup_acceptance_receipt_digest=${backup_acceptance_receipt_digest:-none}" "$RESTORE_STATE_FILE" || { rm -f -- "$temporary"; die "Restore state acceptance authority changed"; }
    grep -Fxq "db_manifest_digest=$db_manifest_digest" "$RESTORE_STATE_FILE" || { rm -f -- "$temporary"; die "Restore state PostgreSQL digest changed"; }
    grep -Fxq "markdown_manifest_digest=$markdown_manifest_digest" "$RESTORE_STATE_FILE" || { rm -f -- "$temporary"; die "Restore state Markdown digest changed"; }
    grep -Fxq "api_prior_state=$normalized_api_state" "$RESTORE_STATE_FILE" || { rm -f -- "$temporary"; die "Restore state API intent changed"; }
    grep -Fxq "converter_prior_state=$normalized_converter_state" "$RESTORE_STATE_FILE" || { rm -f -- "$temporary"; die "Restore state converter intent changed"; }
    grep -Fxq "projection_prior_state=$normalized_projection_state" "$RESTORE_STATE_FILE" || { rm -f -- "$temporary"; die "Restore state projection intent changed"; }
    grep -Fxq "worker_prior_state=$normalized_worker_state" "$RESTORE_STATE_FILE" || { rm -f -- "$temporary"; die "Restore state lifecycle-worker intent changed"; }
    grep -Fxq "frontend_prior_state=$normalized_frontend_state" "$RESTORE_STATE_FILE" || { rm -f -- "$temporary"; die "Restore state frontend intent changed"; }
    grep -Fxq "nginx_prior_state=$normalized_nginx_state" "$RESTORE_STATE_FILE" || { rm -f -- "$temporary"; die "Restore state Nginx intent changed"; }
    mv -f -- "$temporary" "$RESTORE_STATE_FILE"
    temporary=""
  fi
  if [ -n "$temporary" ]; then
    rm -f -- "$temporary"
  fi
}

directory="$(backup_directory "$1")"
mode="$2"
case "$mode" in --verify-only | --yes-restore) ;; *) usage ;; esac
verify_backup "$directory"

backup_format="$(metadata_value format)"
case "$backup_format" in
  connectmd-backup-v2)
    expected_metadata_keys="$(printf '%s\n' api_image_id created_at deletion_journal_head_digest deletion_journal_head_sequence expires_at format generation_id image_tag nginx_image_id postgres_database release_receipt_digest source_revision web_image_id | LC_ALL=C sort)"
    ;;
  connectmd-backup-v3)
    expected_metadata_keys="$(printf '%s\n' acceptance_receipt_digest api_image_id created_at deletion_journal_head_digest deletion_journal_head_sequence expires_at format generation_id image_tag nginx_image_id postgres_database release_receipt_digest source_revision web_image_id | LC_ALL=C sort)"
    ;;
  *) die "Backup format is unsupported" ;;
esac
actual_metadata_keys="$(cut -d= -f1 "$directory/metadata.env" | LC_ALL=C sort)"
[ "$actual_metadata_keys" = "$expected_metadata_keys" ] || die "Backup metadata must contain exactly the supported fields"
db_name="$(read_env_value POSTGRES_DB || printf 'connectmd')"
db_user="$(read_env_value POSTGRES_USER || printf 'postgres')"
metadata_db="$(metadata_value postgres_database)"
generation_id="$(metadata_value generation_id)"
created_at="$(metadata_value created_at)"
expires_at="$(metadata_value expires_at)"
backup_source_revision="$(metadata_value source_revision)"
backup_image_tag="$(metadata_value image_tag)"
backup_api_image_id="$(metadata_value api_image_id)"
backup_web_image_id="$(metadata_value web_image_id)"
backup_nginx_image_id="$(metadata_value nginx_image_id)"
backup_release_receipt_digest="$(metadata_value release_receipt_digest)"
backup_acceptance_receipt_digest=""
if [ "$backup_format" = "connectmd-backup-v3" ]; then
  backup_acceptance_receipt_digest="$(metadata_value acceptance_receipt_digest)"
fi
deletion_journal_head_sequence="$(metadata_value deletion_journal_head_sequence)"
deletion_journal_head_digest="$(metadata_value deletion_journal_head_digest)"
case "$db_name" in *[!A-Za-z0-9_]* | '') die "POSTGRES_DB must be an SQL identifier for restore" ;; esac
case "$db_user" in *[!A-Za-z0-9_]* | '') die "POSTGRES_USER must be an SQL identifier for restore" ;; esac
[ "$metadata_db" = "$db_name" ] || die "Backup database metadata does not match configured POSTGRES_DB"
printf '%s' "$generation_id" | grep -Eq '^connectmd-[0-9]{8}T[0-9]{6}Z$' || die "Backup generation ID is invalid"
printf '%s' "$created_at" | grep -Eq '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$' || die "Backup creation timestamp is invalid"
printf '%s' "$expires_at" | grep -Eq '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$' || die "Backup expiry timestamp is invalid"
created_epoch="$(date -u -d "$created_at" +%s)" || die "Backup creation timestamp is not a real UTC date"
expires_epoch="$(date -u -d "$expires_at" +%s)" || die "Backup expiry timestamp is not a real UTC date"
[ "$(date -u -d "@$created_epoch" +%Y-%m-%dT%H:%M:%SZ)" = "$created_at" ] || die "Backup creation timestamp is not canonical UTC"
[ "$(date -u -d "@$expires_epoch" +%Y-%m-%dT%H:%M:%SZ)" = "$expires_at" ] || die "Backup expiry timestamp is not canonical UTC"
[ "$expires_epoch" -gt "$created_epoch" ] || die "Backup expiry must be later than creation"
case "$backup_image_tag" in "" | *[!A-Za-z0-9_.-]*) die "Backup image tag is invalid" ;; esac
is_full_source_revision "$backup_source_revision" || die "Backup source revision is invalid"
is_image_identity "$backup_api_image_id" && is_image_identity "$backup_web_image_id" && is_image_identity "$backup_nginx_image_id" \
  || die "Backup image identity is invalid"
printf '%s' "$backup_release_receipt_digest" | grep -Eq '^[0-9a-f]{64}$' \
  || die "Backup release receipt digest is invalid"
if [ "$backup_format" = "connectmd-backup-v3" ]; then
  printf '%s' "$backup_acceptance_receipt_digest" | grep -Eq '^[0-9a-f]{64}$' \
    || die "Backup acceptance receipt digest is invalid"
fi
case "$deletion_journal_head_sequence" in "" | *[!0-9]*) die "Backup deletion journal sequence is invalid" ;; esac
printf '%s' "$deletion_journal_head_digest" | grep -Eq '^[0-9a-f]{64}$' || die "Backup deletion journal digest is invalid"
db_manifest_digest="$(sha256sum "$directory/postgres.dump" | cut -d' ' -f1)"
markdown_manifest_digest="$(sha256sum "$directory/markdown-storage.tar.gz" | cut -d' ' -f1)"
lifecycle_root="$(backup_root)/.connectmd-lifecycle"
registration_root="$lifecycle_root/registrations"
journal_root="$lifecycle_root/deletion-journal"
receipt="$registration_root/$generation_id.env"
for lifecycle_directory in "$lifecycle_root" "$registration_root" "$journal_root"; do
  [ ! -L "$lifecycle_directory" ] || die "Lifecycle evidence directory must not be a symlink: $lifecycle_directory"
  if [ -e "$lifecycle_directory" ]; then
    [ -d "$lifecycle_directory" ] || die "Lifecycle evidence path must be a directory: $lifecycle_directory"
    [ "$(realpath -e "$lifecycle_directory")" = "$lifecycle_directory" ] || die "Lifecycle evidence directory path is invalid: $lifecycle_directory"
  fi
done
if [ "$mode" = "--yes-restore" ]; then
  [ -f "$receipt" ] && [ ! -L "$receipt" ] || die "Destructive restore requires an existing durable registration receipt"
  verify_registration_receipt
elif [ -e "$receipt" ] || [ -L "$receipt" ]; then
  verify_registration_receipt
fi

# Authenticate the exact local release image before it can read any backup bytes.
[ "$(current_source_revision)" = "$backup_source_revision" ] \
  || die "Checked-out source revision does not match the backup generation"
backup_release_receipt="$(release_receipt_path "$backup_image_tag")"
validate_release_receipt "$backup_release_receipt" "$backup_source_revision" "$backup_image_tag" "$backup_api_image_id" "$backup_web_image_id" "$backup_nginx_image_id"
backup_recruiting_enabled="$(record_value "$backup_release_receipt" recruiting_enabled)"
[ "$backup_recruiting_enabled" = "$(normalize_recruiting_enabled)" ] || die "Backup release recruiting state does not match .env"
[ "$(sha256sum "$backup_release_receipt" | cut -d' ' -f1)" = "$backup_release_receipt_digest" ] \
  || die "Backup release receipt does not match the recorded generation"
if [ "$backup_format" = "connectmd-backup-v3" ]; then
  backup_acceptance_receipt="$(load_release_acceptance "$backup_image_tag" "$backup_acceptance_receipt_digest")"
  [ "$(digest_of_file "$backup_acceptance_receipt")" = "$backup_acceptance_receipt_digest" ] \
    || die "Backup acceptance receipt does not match the recorded generation"
fi
assert_release_images_match "$backup_image_tag" "$backup_api_image_id" "$backup_web_image_id" "$backup_nginx_image_id"

CONNECTMD_IMAGE_TAG="$backup_image_tag" \
  compose --profile account-lifecycle run --rm --no-deps -T api python -m app.cli deletion-journal verify-checkpoint \
    --head-sequence "$deletion_journal_head_sequence" \
    --head-digest "$deletion_journal_head_digest"

docker run --rm --network none --read-only \
  -v "$directory/markdown-storage.tar.gz:/restore/markdown-storage.tar.gz:ro" \
  --entrypoint python "$backup_api_image_id" \
  -m app.services.backup_archive /restore/markdown-storage.tar.gz
compose --profile ops run --rm --no-deps -T backup-verify pg_restore --list < "$directory/postgres.dump" >/dev/null

if [ "$mode" = "--verify-only" ]; then
  printf 'RESTORE_INPUT=VERIFIED\n'
  exit 0
fi
[ "$mode" = "--yes-restore" ] || usage

# Refuse before the destructive boundary unless the recorded image also exposes
# the expected data-authority contracts.
docker run --rm --network none --entrypoint python "connectmd-api:$backup_image_tag" -c "from app.services.deletion_journal import DELETION_AUTHORITY_CONTRACT_VERSION as version; assert version >= 1"
assert_exact_search_image_contract "$backup_image_tag"
[ ! -e "$RESTORE_STATE_FILE" ] && [ ! -L "$RESTORE_STATE_FILE" ] || die "A prior restore state requires deployment or explicit recovery"
mkdir -p "$registration_root"
for lifecycle_directory in "$lifecycle_root" "$registration_root" "$journal_root"; do
  [ "$(realpath -e "$lifecycle_directory")" = "$lifecycle_directory" ] || die "Lifecycle evidence directory path is invalid: $lifecycle_directory"
done
chmod 700 "$lifecycle_root" "$registration_root" "$journal_root"
evidence_probe="$(mktemp "$registration_root/.restore-preflight.XXXXXX")"
chmod 600 "$evidence_probe"
if ! ln "$evidence_probe" "$evidence_probe.link"; then
  rm -f -- "$evidence_probe"
  die "Lifecycle evidence directory does not support atomic receipt creation"
fi
rm -f -- "$evidence_probe.link" "$evidence_probe"

assert_no_active_one_shot_consumers() {
  local profiled_consumer consumer_profile consumer_service
  for profiled_consumer in \
    search-operations:search-admin \
    taxonomy-operations:taxonomy-admin \
    exact-search-operations:exact-search-admin \
    ops:storage-backup \
    ops:storage-restore \
    ops:backup-verify
  do
    consumer_profile="${profiled_consumer%%:*}"
    consumer_service="${profiled_consumer#*:}"
    if profiled_service_is_active "$consumer_profile" "$consumer_service"; then
      die "One-shot canonical-state consumer must finish before destructive restore: $consumer_service"
    fi
  done
}
assert_no_active_one_shot_consumers

api_prior_state="$(service_state api)"
converter_prior_state="$(service_state converter)"
projection_prior_state="$(service_state search-projection-worker)"
worker_prior_state="$(profiled_service_state account-lifecycle account-erasure-worker)"
frontend_prior_state="$(service_state frontend)"
nginx_prior_state="$(service_state nginx)"
if state_is_serving "$nginx_prior_state" && { ! state_is_serving "$api_prior_state" || ! state_is_serving "$frontend_prior_state"; }; then
  die "Serving Nginx requires running API and frontend before destructive restore"
fi
api_prior_tag=""
converter_prior_tag=""
projection_prior_tag=""
worker_prior_tag=""
frontend_prior_tag=""
nginx_prior_tag=""
api_prior_image_id=""
converter_prior_image_id=""
projection_prior_image_id=""
worker_prior_image_id=""
frontend_prior_image_id=""
nginx_prior_image_id=""
if state_is_active "$api_prior_state"; then
  api_prior_tag="$(managed_api_tag "$(service_image api)")"
  api_prior_image_id="$(service_image_identity api)" || die "API prior image identity is unavailable"
fi
if state_is_active "$converter_prior_state"; then
  converter_prior_tag="$(managed_api_tag "$(service_image converter)")"
  converter_prior_image_id="$(service_image_identity converter)" || die "Converter prior image identity is unavailable"
fi
if state_is_active "$projection_prior_state"; then
  projection_prior_tag="$(managed_api_tag "$(service_image search-projection-worker)")"
  projection_prior_image_id="$(service_image_identity search-projection-worker)" || die "Search projection prior image identity is unavailable"
fi
if state_is_active "$worker_prior_state"; then
  worker_prior_tag="$(managed_api_tag "$(profiled_service_image account-lifecycle account-erasure-worker)")"
  worker_prior_image_id="$(profiled_service_image_identity account-lifecycle account-erasure-worker)" || die "Lifecycle worker prior image identity is unavailable"
fi
if state_is_active "$frontend_prior_state"; then
  frontend_prior_tag="$(managed_web_tag "$(service_image frontend)")"
  frontend_prior_image_id="$(service_image_identity frontend)" || die "Frontend prior image identity is unavailable"
fi
if state_is_active "$nginx_prior_state"; then
  nginx_prior_tag="$(managed_nginx_tag "$(service_image nginx)")"
  nginx_prior_image_id="$(service_image_identity nginx)" || die "Nginx prior image identity is unavailable"
fi
restore_complete=false
mutation_started=false
restore_unprofiled_service_before_mutation() {
  local service="$1" image="$2" tag="$3" identity="$4" prior_state="$5"
  state_is_active "$prior_state" || return 0
  assert_image_identity "$image" "$tag" "$identity"
  export CONNECTMD_IMAGE_TAG="$tag"
  compose up -d --no-build --no-deps "$service" >/dev/null
  wait_for_service "$service" 30
  assert_service_image_identity "$service" "$identity"
  if [ "$prior_state" = "paused" ]; then
    compose pause "$service" >/dev/null
    [ "$(service_state "$service")" = "paused" ] || die "$service did not return to its prior paused state"
  fi
}

restore_lifecycle_worker_before_mutation() {
  state_is_active "$worker_prior_state" || return 0
  assert_image_identity connectmd-api "$worker_prior_tag" "$worker_prior_image_id"
  export CONNECTMD_IMAGE_TAG="$worker_prior_tag"
  compose --profile account-lifecycle up -d --no-build --no-deps account-erasure-worker >/dev/null
  wait_for_profiled_service account-lifecycle account-erasure-worker 30
  [ "$(profiled_service_image_identity account-lifecycle account-erasure-worker)" = "$worker_prior_image_id" ] \
    || die "Lifecycle worker did not return to its prior image identity"
  if [ "$worker_prior_state" = "paused" ]; then
    compose --profile account-lifecycle pause account-erasure-worker >/dev/null
    [ "$(profiled_service_state account-lifecycle account-erasure-worker)" = "paused" ] \
      || die "Lifecycle worker did not return to its prior paused state"
  fi
}

restart_services_on_failure() {
  local status=$? recovery_failed=false
  trap - EXIT
  if [ "$restore_complete" = false ] && [ "$mutation_started" = false ]; then
    if ! (restore_unprofiled_service_before_mutation converter connectmd-api "$converter_prior_tag" "$converter_prior_image_id" "$converter_prior_state"); then recovery_failed=true; fi
    if ! (restore_unprofiled_service_before_mutation search-projection-worker connectmd-api "$projection_prior_tag" "$projection_prior_image_id" "$projection_prior_state"); then recovery_failed=true; fi
    if ! (restore_unprofiled_service_before_mutation api connectmd-api "$api_prior_tag" "$api_prior_image_id" "$api_prior_state"); then recovery_failed=true; fi
    if ! (restore_lifecycle_worker_before_mutation); then recovery_failed=true; fi
    if ! (restore_unprofiled_service_before_mutation frontend connectmd-web "$frontend_prior_tag" "$frontend_prior_image_id" "$frontend_prior_state"); then recovery_failed=true; fi
    if ! (restore_unprofiled_service_before_mutation nginx connectmd-nginx "$nginx_prior_tag" "$nginx_prior_image_id" "$nginx_prior_state"); then recovery_failed=true; fi
  fi
  if [ "$recovery_failed" = true ]; then
    printf 'ERROR: Pre-mutation restore failure could not re-establish every prior service state.\n' >&2
    status=1
  fi
  exit "$status"
}
trap restart_services_on_failure EXIT

compose stop nginx frontend
if service_is_active nginx || service_is_active frontend; then
  die "Nginx and frontend must stop before restore"
fi
compose --profile account-lifecycle stop account-erasure-worker search-projection-worker api converter
if service_is_active api || service_is_active search-projection-worker || profiled_service_is_active account-lifecycle account-erasure-worker || service_is_active converter; then
  die "API, converter, search-projection-worker, and account-erasure-worker must stop before restore"
fi
assert_no_active_one_shot_consumers
compose up -d --no-build postgres meilisearch
wait_for_service postgres

# Both archives and every checksum were verified before this destructive boundary.
write_restore_state in_progress unavailable
mutation_started=true
compose --profile ops run --rm --no-deps -T -v "$directory:/restore:ro" storage-restore sh -ceu '
  find /storage -mindepth 1 -maxdepth 1 -exec rm -rf -- {} \;
  tar -C /storage -xzf /restore/markdown-storage.tar.gz -o
  chown -R 10001:10001 /storage
'

compose exec -T postgres psql -v ON_ERROR_STOP=1 -U "$db_user" -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$db_name' AND pid <> pg_backend_pid();"
compose exec -T postgres dropdb -U "$db_user" --if-exists "$db_name"
compose exec -T postgres createdb -U "$db_user" "$db_name"
bootstrap_database_roles
attest_restore_migrator_role
compose --profile database-operations run --rm --no-deps -T database-restore \
  pg_restore --exit-on-error --no-owner --no-privileges -d "$db_name" < "$directory/postgres.dump"
reconcile_database_roles

# The dump predates registration of the generation that contains it. Recreate
# that authority with the matching recorded application image before success.
export CONNECTMD_IMAGE_TAG="$backup_image_tag"
assert_release_images_match "$backup_image_tag" "$backup_api_image_id" "$backup_web_image_id" "$backup_nginx_image_id"
compose --profile account-lifecycle run --rm --no-deps -T api python -m app.cli account-backup register \
  --generation-id "$generation_id" \
  --created-at "$created_at" \
  --expires-at "$expires_at" \
  --db-manifest-digest "$db_manifest_digest" \
  --markdown-manifest-digest "$markdown_manifest_digest"

verify_registration_receipt

compose --profile account-lifecycle run --rm --no-deps -T api python -m app.cli deletion-journal verify-live
registration_receipt_digest="$(sha256sum "$receipt" | cut -d' ' -f1)"
write_restore_state complete "$registration_receipt_digest"
restore_complete=true
printf 'RESTORE=COMPLETE\n'
printf 'Nginx, frontend, API, converter, search-projection-worker, and account-erasure-worker remain stopped. Durable restore state requires the matching deploy to rebuild search before re-establishing the recorded service topology.\n'
