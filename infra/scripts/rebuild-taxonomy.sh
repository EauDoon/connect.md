#!/usr/bin/env bash
set -Eeuo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/lib.sh"

ensure_repo
acquire_operation_lock
validate_production_env
select_release_image_tag staged-or-accepted
compose up -d --no-build postgres meilisearch
wait_for_service postgres
wait_for_service meilisearch

lifecycle_prior_state="$(profiled_service_state account-lifecycle account-erasure-worker)"
lifecycle_was_running=false
lifecycle_should_pause=false
lifecycle_enabled="$(read_env_optional_value CONNECTMD_ACCOUNT_LIFECYCLE_ENABLED)" || die "CONNECTMD_ACCOUNT_LIFECYCLE_ENABLED must appear at most once in .env"
if [ "${lifecycle_enabled:-false}" = "true" ]; then
  lifecycle_was_running=true
fi
if [ "$lifecycle_prior_state" = "running" ] || [ "$lifecycle_prior_state" = "restarting" ]; then
  lifecycle_was_running=true
elif [ "$lifecycle_prior_state" = "paused" ]; then
  lifecycle_was_running=true
  lifecycle_should_pause=true
fi

rebuild_barrier_entered=false
rebuild_complete=false
stop_failed_rebuild_on_exit() {
  local status=$?
  trap - EXIT
  if [ "$status" -ne 0 ] && [ "$rebuild_barrier_entered" = true ] && [ "$rebuild_complete" = false ]; then
    if ! compose --profile account-lifecycle stop account-erasure-worker search-projection-worker api >/dev/null 2>&1; then
      printf 'ERROR: failed taxonomy rebuild could not stop every writer; inspect immediately.\n' >&2
    else
      printf 'ERROR: failed taxonomy rebuild left every writer stopped for explicit recovery.\n' >&2
    fi
  fi
  exit "$status"
}
trap stop_failed_rebuild_on_exit EXIT

# Taxonomy memberships and Meilisearch must be rebuilt from one stable
# canonical epoch. Stop every document, lifecycle, and search-projection writer
# before reading verified Markdown or resetting either public projection.
rebuild_barrier_entered=true
compose --profile account-lifecycle stop account-erasure-worker search-projection-worker api >/dev/null
if service_is_active api || service_is_active search-projection-worker || profiled_service_is_active account-lifecycle account-erasure-worker; then
  die "API, search-projection-worker, and account-erasure-worker must stop before rebuilding taxonomy"
fi
compose --profile account-lifecycle run --rm --no-deps -T api python -m app.cli deletion-journal verify-live
compose --profile taxonomy-operations run --rm --no-deps -T taxonomy-admin python -m app.cli taxonomy backfill
compose --profile taxonomy-operations run --rm --no-deps -T taxonomy-admin python -m app.cli taxonomy verify
compose --profile search-operations run --rm --no-deps -T search-admin python -m app.cli rebuild-search
compose up -d --no-build search-projection-worker api
wait_for_service search-projection-worker
wait_for_service api
if [ "$lifecycle_was_running" = true ]; then
  compose --profile account-lifecycle up -d --no-build account-erasure-worker
  wait_for_profiled_service account-lifecycle account-erasure-worker
  if [ "$lifecycle_should_pause" = true ]; then
    compose --profile account-lifecycle pause account-erasure-worker
    [ "$(profiled_service_state account-lifecycle account-erasure-worker)" = "paused" ] || die "account-erasure-worker did not return to its prior paused state"
  fi
fi
rebuild_complete=true
printf 'TAXONOMY_REBUILD=PASS\n'
printf 'MEILISEARCH_REBUILD=PASS\n'
