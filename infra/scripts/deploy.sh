#!/usr/bin/env bash
set -Eeuo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/lib.sh"

ensure_repo
acquire_operation_lock
ensure_clean_source
validate_production_env
assert_no_pending_staged_release
if [ -f "$RELEASE_ENV_FILE" ]; then
  assert_api_key_pepper_unchanged
fi
image_tag="$(current_image_tag)"
source_revision="$(current_source_revision)"
export CONNECTMD_IMAGE_TAG="$image_tag"

restore_worker_state="absent"
restore_api_state="running"
restore_converter_state="running"
restore_projection_state="running"
restore_frontend_state="running"
restore_nginx_state="running"
restore_state_present=false
if [ -e "$RESTORE_STATE_FILE" ] || [ -L "$RESTORE_STATE_FILE" ]; then
  [ -f "$RESTORE_STATE_FILE" ] && [ ! -L "$RESTORE_STATE_FILE" ] || die "Restore state path is unsafe"
  [ "$(stat -c '%a' "$RESTORE_STATE_FILE")" = "600" ] || die "Restore state permissions are unsafe"
  restore_state_format="$(record_value "$RESTORE_STATE_FILE" format)"
  case "$restore_state_format" in
    connectmd-restore-state-v2)
      expected_restore_keys="$(printf '%s\n' api_image_id backup_acceptance_receipt_digest backup_format db_manifest_digest deletion_journal_head_digest deletion_journal_head_sequence format generation_id image_tag markdown_manifest_digest nginx_image_id phase registration_receipt_digest release_receipt_digest search_rebuild_pending source_revision web_image_id worker_prior_state | LC_ALL=C sort)"
      ;;
    connectmd-restore-state-v3)
      expected_restore_keys="$(printf '%s\n' api_image_id api_prior_state backup_acceptance_receipt_digest backup_format converter_prior_state db_manifest_digest deletion_journal_head_digest deletion_journal_head_sequence format frontend_prior_state generation_id image_tag markdown_manifest_digest nginx_image_id nginx_prior_state phase projection_prior_state registration_receipt_digest release_receipt_digest search_rebuild_pending source_revision web_image_id worker_prior_state | LC_ALL=C sort)"
      restore_api_state="$(record_value "$RESTORE_STATE_FILE" api_prior_state)"
      restore_converter_state="$(record_value "$RESTORE_STATE_FILE" converter_prior_state)"
      restore_projection_state="$(record_value "$RESTORE_STATE_FILE" projection_prior_state)"
      restore_frontend_state="$(record_value "$RESTORE_STATE_FILE" frontend_prior_state)"
      restore_nginx_state="$(record_value "$RESTORE_STATE_FILE" nginx_prior_state)"
      ;;
    *) die "Restore state format is unsupported" ;;
  esac
  actual_restore_keys="$(cut -d= -f1 "$RESTORE_STATE_FILE" | LC_ALL=C sort)"
  [ "$actual_restore_keys" = "$expected_restore_keys" ] || die "Restore state contains unsupported fields"
  grep -Fxq 'phase=complete' "$RESTORE_STATE_FILE" || die "Restore state is incomplete; explicit recovery is required"
  restore_search_rebuild_pending="$(grep -E '^search_rebuild_pending=' "$RESTORE_STATE_FILE" | cut -d= -f2-)"
  case "$restore_search_rebuild_pending" in true | false) ;; *) die "Restore search rebuild state is invalid" ;; esac
  restore_generation="$(grep -E '^generation_id=' "$RESTORE_STATE_FILE" | cut -d= -f2-)"
  restore_backup_format="$(grep -E '^backup_format=' "$RESTORE_STATE_FILE" | cut -d= -f2-)"
  restore_backup_acceptance_digest="$(grep -E '^backup_acceptance_receipt_digest=' "$RESTORE_STATE_FILE" | cut -d= -f2-)"
  restore_source_revision="$(grep -E '^source_revision=' "$RESTORE_STATE_FILE" | cut -d= -f2-)"
  restore_image_tag="$(grep -E '^image_tag=' "$RESTORE_STATE_FILE" | cut -d= -f2-)"
  restore_api_image_id="$(grep -E '^api_image_id=' "$RESTORE_STATE_FILE" | cut -d= -f2-)"
  restore_web_image_id="$(grep -E '^web_image_id=' "$RESTORE_STATE_FILE" | cut -d= -f2-)"
  restore_nginx_image_id="$(grep -E '^nginx_image_id=' "$RESTORE_STATE_FILE" | cut -d= -f2-)"
  restore_release_receipt_digest="$(grep -E '^release_receipt_digest=' "$RESTORE_STATE_FILE" | cut -d= -f2-)"
  restore_worker_state="$(grep -E '^worker_prior_state=' "$RESTORE_STATE_FILE" | cut -d= -f2-)"
  restore_db_digest="$(grep -E '^db_manifest_digest=' "$RESTORE_STATE_FILE" | cut -d= -f2-)"
  restore_markdown_digest="$(grep -E '^markdown_manifest_digest=' "$RESTORE_STATE_FILE" | cut -d= -f2-)"
  restore_receipt_digest="$(grep -E '^registration_receipt_digest=' "$RESTORE_STATE_FILE" | cut -d= -f2-)"
  restore_journal_sequence="$(grep -E '^deletion_journal_head_sequence=' "$RESTORE_STATE_FILE" | cut -d= -f2-)"
  restore_journal_digest="$(grep -E '^deletion_journal_head_digest=' "$RESTORE_STATE_FILE" | cut -d= -f2-)"
  printf '%s' "$restore_generation" | grep -Eq '^connectmd-[0-9]{8}T[0-9]{6}Z$' || die "Restore state generation is invalid"
  is_full_source_revision "$restore_source_revision" || die "Restore state source revision is invalid"
  for digest in "$restore_db_digest" "$restore_markdown_digest" "$restore_receipt_digest" "$restore_release_receipt_digest"; do
    printf '%s' "$digest" | grep -Eq '^[0-9a-f]{64}$' || die "Restore state digest is invalid"
  done
  case "$restore_backup_format" in
    connectmd-backup-v2) [ "$restore_backup_acceptance_digest" = none ] || die "Legacy restore state must not carry accepted authority" ;;
    connectmd-backup-v3) printf '%s' "$restore_backup_acceptance_digest" | grep -Eq '^[0-9a-f]{64}$' || die "V3 restore state acceptance authority is invalid" ;;
    *) die "Restore state backup format is unsupported" ;;
  esac
  case "$restore_journal_sequence" in "" | *[!0-9]*) die "Restore deletion journal sequence is invalid" ;; esac
  printf '%s' "$restore_journal_digest" | grep -Eq '^[0-9a-f]{64}$' || die "Restore deletion journal digest is invalid"
  [ "$restore_source_revision" = "$source_revision" ] || die "Deployment source does not match the restored generation"
  [ "$restore_image_tag" = "$image_tag" ] || die "Deployment image does not match the restored generation"
  assert_release_images_match "$restore_image_tag" "$restore_api_image_id" "$restore_web_image_id" "$restore_nginx_image_id"
  restore_release_receipt="$(release_receipt_path "$restore_image_tag")"
  validate_release_receipt "$restore_release_receipt" "$restore_source_revision" "$restore_image_tag" "$restore_api_image_id" "$restore_web_image_id" "$restore_nginx_image_id"
  [ "$(sha256sum "$restore_release_receipt" | cut -d' ' -f1)" = "$restore_release_receipt_digest" ] \
    || die "Restore state release receipt does not match durable release history"
  case "$restore_worker_state" in absent | stopped | running | restarting | paused) ;; *) die "Restore worker state is invalid" ;; esac
  for restore_service_state in "$restore_api_state" "$restore_converter_state" "$restore_projection_state" "$restore_frontend_state" "$restore_nginx_state"; do
    case "$restore_service_state" in absent | stopped | running | paused) ;; *) die "Restore service state is invalid" ;; esac
  done
  restore_receipt="$(backup_root)/.connectmd-lifecycle/registrations/$restore_generation.env"
  [ -f "$restore_receipt" ] && [ ! -L "$restore_receipt" ] || die "Completed restore receipt is missing or unsafe"
  [ "$(stat -c '%a' "$restore_receipt")" = "600" ] || die "Completed restore receipt permissions are unsafe"
  [ "$(sha256sum "$restore_receipt" | cut -d' ' -f1)" = "$restore_receipt_digest" ] || die "Completed restore receipt does not match durable restore state"
  restore_state_present=true
fi

compose config -q
if [ "$restore_state_present" = true ]; then
  assert_release_images_match "$image_tag" "$restore_api_image_id" "$restore_web_image_id" "$restore_nginx_image_id"
else
  build_or_reuse_release_images "$image_tag" "$source_revision"
fi
api_image_id="$(image_identity_for_tag connectmd-api "$image_tag")"
web_image_id="$(image_identity_for_tag connectmd-web "$image_tag")"
nginx_image_id="$(image_identity_for_tag connectmd-nginx "$image_tag")"
assert_release_images_match "$image_tag" "$api_image_id" "$web_image_id" "$nginx_image_id"
assert_exact_search_image_contract "$image_tag"
compose up -d --no-build postgres meilisearch
wait_for_service postgres
wait_for_service meilisearch

# Migrations are a fail-closed, single-writer barrier. Preserve the optional
# lifecycle-worker intent, but never let it or an old API race the migration.
worker_was_running=false
worker_should_pause=false
current_worker_state="$(profiled_service_state account-lifecycle account-erasure-worker)"
lifecycle_enabled="$(read_env_optional_value CONNECTMD_ACCOUNT_LIFECYCLE_ENABLED)" || die "CONNECTMD_ACCOUNT_LIFECYCLE_ENABLED must appear at most once in .env"
if [ "$restore_state_present" = true ] && [ "$restore_state_format" = "connectmd-restore-state-v3" ]; then
  case "$restore_worker_state" in
    running | restarting) worker_was_running=true ;;
    paused)
      worker_was_running=true
      worker_should_pause=true
      ;;
  esac
else
  if [ "${lifecycle_enabled:-false}" = "true" ]; then
    worker_was_running=true
  fi
  if [ "$current_worker_state" = "running" ] || [ "$current_worker_state" = "restarting" ]; then
    worker_was_running=true
  elif [ "$current_worker_state" = "paused" ]; then
    worker_was_running=true
    worker_should_pause=true
  elif [ "$restore_worker_state" = "running" ] || [ "$restore_worker_state" = "restarting" ]; then
    worker_was_running=true
  elif [ "$restore_worker_state" = "paused" ]; then
    worker_was_running=true
    worker_should_pause=true
  fi
fi

rollout_barrier_entered=false
rollout_complete=false
stop_failed_rollout_on_exit() {
  local status=$?
  trap - EXIT
  if [ "$status" -ne 0 ] && [ "$rollout_barrier_entered" = true ] && [ "$rollout_complete" = false ]; then
    if ! compose --profile account-lifecycle stop account-erasure-worker search-projection-worker nginx frontend api converter >/dev/null 2>&1; then
      printf 'ERROR: failed rollout could not stop every application service; inspect immediately.\n' >&2
    else
      printf 'ERROR: failed rollout left application services stopped for explicit recovery.\n' >&2
    fi
  fi
  exit "$status"
}
trap stop_failed_rollout_on_exit EXIT
rollout_barrier_entered=true
compose --profile account-lifecycle stop account-erasure-worker search-projection-worker nginx frontend api >/dev/null
for service in api frontend nginx search-projection-worker; do
  if service_is_active "$service"; then
    die "$service is still running; refusing to migrate"
  fi
done
if profiled_service_is_active account-lifecycle account-erasure-worker; then
  die "account-erasure-worker is still running; refusing to migrate"
fi
bootstrap_database_roles
compose --profile database-operations run --rm --no-deps -T db-migrate alembic upgrade head
reconcile_database_roles
if [ "$restore_state_present" = true ]; then
  compose --profile account-lifecycle run --rm --no-deps -T api python -m app.cli deletion-journal verify-checkpoint \
    --head-sequence "$restore_journal_sequence" \
    --head-digest "$restore_journal_digest"
fi
compose --profile account-lifecycle run --rm --no-deps -T api python -m app.cli deletion-journal verify-live
compose --profile taxonomy-operations run --rm --no-deps -T taxonomy-admin python -m app.cli taxonomy backfill --if-required
compose --profile taxonomy-operations run --rm --no-deps -T taxonomy-admin python -m app.cli taxonomy verify
compose --profile exact-search-operations run --rm --no-deps -T exact-search-admin python -m app.cli exact-search backfill --if-required
compose --profile exact-search-operations run --rm --no-deps -T exact-search-admin python -m app.cli exact-search verify
compose --profile search-operations run --rm --no-deps -T search-admin python -m app.cli rebuild-search
if [ "$restore_state_present" = true ]; then
  restore_temporary="$(mktemp "$REPO_ROOT/.connectmd-restore-state.XXXXXX")"
  chmod 600 "$restore_temporary"
  sed 's/^search_rebuild_pending=.*/search_rebuild_pending=false/' "$RESTORE_STATE_FILE" > "$restore_temporary"
  grep -Fxq 'search_rebuild_pending=false' "$restore_temporary" || die "Restore search rebuild state update failed"
  mv -f -- "$restore_temporary" "$RESTORE_STATE_FILE"
fi
assert_release_images_match "$image_tag" "$api_image_id" "$web_image_id" "$nginx_image_id"
start_restored_service() {
  local service="$1" prior_state="$2" expected_identity="$3"
  case "$prior_state" in
    running | paused)
      compose up -d --no-build --no-deps "$service"
      wait_for_service "$service"
      assert_service_image_identity "$service" "$expected_identity"
      if [ "$prior_state" = "paused" ]; then
        compose pause "$service"
        [ "$(service_state "$service")" = "paused" ] \
          || die "$service did not return to its recorded paused state"
      fi
      ;;
    absent | stopped)
      if service_is_active "$service"; then
        die "$service became active despite its recorded restored state: $prior_state"
      fi
      ;;
    *) die "Unsupported restored service state for $service: $prior_state" ;;
  esac
}

if [ "$restore_state_present" = true ] && [ "$restore_state_format" = "connectmd-restore-state-v3" ]; then
  start_restored_service converter "$restore_converter_state" "$api_image_id"
  start_restored_service search-projection-worker "$restore_projection_state" "$api_image_id"
  start_restored_service api "$restore_api_state" "$api_image_id"
  start_restored_service frontend "$restore_frontend_state" "$web_image_id"
  start_restored_service nginx "$restore_nginx_state" "$nginx_image_id"
else
  compose up -d --no-build converter search-projection-worker api frontend nginx
  wait_for_service converter
  wait_for_service search-projection-worker
  wait_for_service api
  wait_for_service frontend
  wait_for_service nginx
  assert_service_image_identity api "$api_image_id"
  assert_service_image_identity frontend "$web_image_id"
  assert_service_image_identity nginx "$nginx_image_id"
fi
if [ "$worker_was_running" = true ]; then
  compose --profile account-lifecycle up -d --no-build account-erasure-worker
  wait_for_profiled_service account-lifecycle account-erasure-worker
  if [ "$worker_should_pause" = true ]; then
    compose --profile account-lifecycle pause account-erasure-worker
    [ "$(profiled_service_state account-lifecycle account-erasure-worker)" = "paused" ] || die "account-erasure-worker did not return to its prior paused state"
  fi
elif [ "$restore_state_present" = true ] && [ "$restore_state_format" = "connectmd-restore-state-v3" ] && profiled_service_is_active account-lifecycle account-erasure-worker; then
  die "account-erasure-worker became active despite its recorded restored state: $restore_worker_state"
fi
if [ "$restore_state_present" = true ]; then
  grep -Fxq 'search_rebuild_pending=false' "$RESTORE_STATE_FILE" || die "Restore search projection was not rebuilt"
fi
restore_relaunches_existing_acceptance=false
if [ "$restore_state_present" = true ] && [ "$restore_backup_format" = "connectmd-backup-v3" ] && [ -f "$RELEASE_ENV_FILE" ] && [ ! -L "$RELEASE_ENV_FILE" ] && grep -Fxq 'CONNECTMD_RELEASE_FORMAT=connectmd-release-v3' "$RELEASE_ENV_FILE"; then
  load_active_release_identity
  if [ "$RELEASE_SOURCE_REVISION" = "$source_revision" ] && [ "$RELEASE_IMAGE_TAG" = "$image_tag" ] && [ "$RELEASE_API_IMAGE_ID" = "$api_image_id" ] && [ "$RELEASE_WEB_IMAGE_ID" = "$web_image_id" ] && [ "$RELEASE_NGINX_IMAGE_ID" = "$nginx_image_id" ] && [ "$RELEASE_RECEIPT_DIGEST" = "$restore_release_receipt_digest" ] && [ "$RELEASE_ACCEPTANCE_DIGEST" = "$restore_backup_acceptance_digest" ]; then
    restore_relaunches_existing_acceptance=true
  fi
fi
if [ "$restore_relaunches_existing_acceptance" = true ]; then
  # A v3 backup relaunches its exact retained accepted release; it is not a new
  # candidate and therefore does not manufacture a second acceptance receipt.
  clear_matching_completed_restore_state "$source_revision" "$image_tag" "$api_image_id" "$web_image_id" "$nginx_image_id" "$restore_release_receipt_digest"
else
  write_staged_release "$source_revision" "$image_tag" "$api_image_id" "$web_image_id" "$nginx_image_id" >/dev/null
fi
rollout_complete=true

if [ "$restore_relaunches_existing_acceptance" = true ]; then
  printf 'RESTORED_ACCEPTED_IMAGE_TAG=%s\n' "$image_tag"
else
  printf 'STAGED_IMAGE_TAG=%s\n' "$image_tag"
  if [ "$restore_state_present" = true ] && [ "$restore_state_format" = "connectmd-restore-state-v3" ]; then
    printf 'The restored candidate re-established its recorded service topology but is not accepted authority. After its intended public edge is running with DNS and TLS ready, run infra/scripts/release-accept.sh --yes-accept.\n'
  else
    printf 'The locally healthy candidate is not accepted authority. After public DNS and TLS are ready, run infra/scripts/release-accept.sh --yes-accept.\n'
  fi
fi
