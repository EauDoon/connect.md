#!/usr/bin/env bash
set -Eeuo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/lib.sh"

[ "$#" -eq 1 ] || die "Usage: ${0##*/} IMAGE_TAG"
image_tag="$1"
case "$image_tag" in *[!A-Za-z0-9._-]* | '') die "Image tag contains unsupported characters" ;; esac

ensure_repo
acquire_operation_lock
ensure_clean_source
validate_production_env
if [ -f "$RELEASE_ENV_FILE" ]; then
  assert_api_key_pepper_unchanged
fi
pending_stage_rollback=false
staged_rollback_digest=""
staged_rollback_prior_marker_digest=""
if [ -e "$STAGED_RELEASE_FILE" ] || [ -L "$STAGED_RELEASE_FILE" ]; then
  load_staged_release >/dev/null
  staged_rollback_digest="$STAGED_RELEASE_DIGEST"
  staged_rollback_prior_marker_digest="$STAGED_PRIOR_ACCEPTED_MARKER_DIGEST"
  pending_stage_rollback=true
  load_active_release_identity
  [ "$(active_marker_digest_or_none)" = "$STAGED_PRIOR_ACCEPTED_MARKER_DIGEST" ] \
    || die "Pending staged release no longer has its recorded prior accepted authority"
  [ "$image_tag" = "$RELEASE_IMAGE_TAG" ] \
    || die "Pending staged release can roll back only to its prior accepted target"
else
  load_active_release_identity
fi
load_release_receipt "$image_tag" >/dev/null
load_release_acceptance "$image_tag" >/dev/null
[ "$RELEASE_RECRUITING_ENABLED" = "$(normalize_recruiting_enabled)" ] || die "Rollback release recruiting state does not match .env"
[ "$(current_source_revision)" = "$RELEASE_SOURCE_REVISION" ] \
  || die "Checked-out source revision does not match the rollback release receipt"
assert_release_images_match "$image_tag" "$RELEASE_API_IMAGE_ID" "$RELEASE_WEB_IMAGE_ID" "$RELEASE_NGINX_IMAGE_ID"
docker run --rm --network none --entrypoint python "connectmd-api:$image_tag" -c "from app.services.deletion_journal import DELETION_AUTHORITY_CONTRACT_VERSION as version; assert version >= 1"
assert_exact_search_image_contract "$image_tag"

export CONNECTMD_IMAGE_TAG="$image_tag"
compose config -q
lifecycle_prior_state="$(profiled_service_state account-lifecycle account-erasure-worker)"
lifecycle_enabled="$(read_env_optional_value CONNECTMD_ACCOUNT_LIFECYCLE_ENABLED)" || die "CONNECTMD_ACCOUNT_LIFECYCLE_ENABLED must appear at most once in .env"
lifecycle_should_run=false
lifecycle_should_pause=false
if [ "${lifecycle_enabled:-false}" = "true" ]; then
  lifecycle_should_run=true
fi
case "$lifecycle_prior_state" in
  running | restarting) lifecycle_should_run=true ;;
  paused)
    lifecycle_should_run=true
    lifecycle_should_pause=true
    ;;
esac
# Refuse historical images that cannot understand the live schema or provide
# the durable projection worker before stopping the current release.
compose run --rm --no-deps --entrypoint python search-projection-worker -c "from app.search_projection_worker import SEARCH_PROJECTION_CONTRACT_VERSION as version; from pathlib import Path; assert version >= 2 and Path('/app/alembic/versions/0019_search_projection_outbox.py').is_file()"
if [ "$lifecycle_should_run" = true ]; then
  compose --profile account-lifecycle run --rm --no-deps --entrypoint python account-erasure-worker -c "from app.account_erasure_worker import ACCOUNT_LIFECYCLE_HEALTH_CONTRACT_VERSION as version; assert version >= 1"
fi
compose --profile database-operations run --rm --no-deps -T db-migrate alembic current --check-heads
verify_database_roles
compose --profile taxonomy-operations run --rm --no-deps -T taxonomy-admin python -m app.cli taxonomy verify
compose --profile exact-search-operations run --rm --no-deps -T exact-search-admin python -m app.cli exact-search verify

rollback_barrier_entered=false
rollback_complete=false
stop_failed_rollback_on_exit() {
  local status=$?
  trap - EXIT
  if [ "$status" -ne 0 ] && [ "$rollback_barrier_entered" = true ] && [ "$rollback_complete" = false ]; then
    if ! compose --profile account-lifecycle stop account-erasure-worker search-projection-worker nginx frontend api converter >/dev/null 2>&1; then
      printf 'ERROR: failed rollback could not stop every application service; inspect immediately.\n' >&2
    else
      printf 'ERROR: failed rollback left application services stopped for explicit recovery.\n' >&2
    fi
  fi
  exit "$status"
}
trap stop_failed_rollback_on_exit EXIT
rollback_barrier_entered=true
compose --profile account-lifecycle stop account-erasure-worker search-projection-worker nginx frontend api converter >/dev/null
for service in api frontend nginx search-projection-worker converter; do
  if service_is_active "$service"; then
    die "$service is still running; refusing rollback"
  fi
done
if profiled_service_is_active account-lifecycle account-erasure-worker; then
  die "account-erasure-worker is still running; refusing rollback"
fi
reconcile_database_roles
compose --profile taxonomy-operations run --rm --no-deps -T taxonomy-admin python -m app.cli taxonomy verify
compose --profile exact-search-operations run --rm --no-deps -T exact-search-admin python -m app.cli exact-search backfill --if-required
compose --profile exact-search-operations run --rm --no-deps -T exact-search-admin python -m app.cli exact-search verify
compose --profile search-operations run --rm --no-deps -T search-admin python -m app.cli rebuild-search
compose up -d --no-build converter search-projection-worker api frontend nginx
wait_for_service converter
wait_for_service search-projection-worker
wait_for_service api
wait_for_service frontend
wait_for_service nginx
assert_service_image_identity api "$RELEASE_API_IMAGE_ID"
assert_service_image_identity frontend "$RELEASE_WEB_IMAGE_ID"
assert_service_image_identity nginx "$RELEASE_NGINX_IMAGE_ID"
if [ "$lifecycle_should_run" = true ]; then
  compose --profile account-lifecycle up -d --no-build account-erasure-worker
  wait_for_profiled_service account-lifecycle account-erasure-worker
  if [ "$lifecycle_should_pause" = true ]; then
    compose --profile account-lifecycle pause account-erasure-worker
    [ "$(profiled_service_state account-lifecycle account-erasure-worker)" = "paused" ] || die "account-erasure-worker did not return to its prior paused state"
  fi
fi
persist_image_tag "$image_tag" "$RELEASE_SOURCE_REVISION" "$RELEASE_API_IMAGE_ID" "$RELEASE_WEB_IMAGE_ID" "$RELEASE_NGINX_IMAGE_ID"
if [ "$pending_stage_rollback" = true ]; then
  discard_staged_release_after_rollback "$staged_rollback_digest" "$staged_rollback_prior_marker_digest" "$RELEASE_SOURCE_REVISION" "$RELEASE_IMAGE_TAG" "$RELEASE_API_IMAGE_ID" "$RELEASE_WEB_IMAGE_ID" "$RELEASE_NGINX_IMAGE_ID"
fi
rollback_complete=true
printf 'ROLLBACK_IMAGE_TAG=%s\n' "$image_tag"
printf 'ROLLBACK_SOURCE_REVISION=%s\n' "$RELEASE_SOURCE_REVISION"
printf 'Database migrations were not reversed; use only application versions compatible with the current schema.\n'
