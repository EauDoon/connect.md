#!/usr/bin/env bash
set -Eeuo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/lib.sh"

ensure_repo
acquire_operation_lock
ensure_clean_source
validate_production_env
select_release_image_tag accepted-only
assert_no_pending_staged_release

recruiting_enabled="$(read_env_optional_value CONNECTMD_RECRUITING_ENABLED)" || die "CONNECTMD_RECRUITING_ENABLED must appear at most once in .env"
recruiting_enabled="${recruiting_enabled:-false}"
if [ "$recruiting_enabled" = "true" ]; then
  die "Recruiting enablement requires a newly staged and accepted release"
fi

image_tag="$RELEASE_IMAGE_TAG"
[ "$image_tag" = "$(current_image_tag)" ] || die "Frontend build configuration changed; create a new source release with deploy.sh"
assert_stateful_secrets_unchanged

compose config -q
worker_prior_state="$(profiled_service_state account-lifecycle account-erasure-worker)"
lifecycle_enabled="$(read_env_optional_value CONNECTMD_ACCOUNT_LIFECYCLE_ENABLED)" || die "CONNECTMD_ACCOUNT_LIFECYCLE_ENABLED must appear at most once in .env"
worker_should_run=false
worker_should_pause=false
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

reconfigure_barrier_entered=false
reconfigure_complete=false
stop_failed_reconfigure_on_exit() {
  local status=$?
  trap - EXIT
  if [ "$status" -ne 0 ] && [ "$reconfigure_barrier_entered" = true ] && [ "$reconfigure_complete" = false ]; then
    if ! compose --profile account-lifecycle stop account-erasure-worker nginx frontend api search-projection-worker converter >/dev/null 2>&1; then
      printf 'ERROR: failed reconfigure could not stop every application service; inspect immediately.\n' >&2
    else
      printf 'ERROR: failed reconfigure left application services stopped for explicit recovery.\n' >&2
    fi
  fi
  exit "$status"
}
trap stop_failed_reconfigure_on_exit EXIT

reconfigure_barrier_entered=true
compose --profile account-lifecycle stop account-erasure-worker nginx frontend api search-projection-worker converter >/dev/null
for service in api frontend nginx search-projection-worker converter; do
  if service_is_active "$service"; then
    die "$service is still running; refusing reconfiguration"
  fi
done
if profiled_service_is_active account-lifecycle account-erasure-worker; then
  die "account-erasure-worker is still running; refusing reconfiguration"
fi
reconcile_database_roles
compose --profile exact-search-operations run --rm --no-deps -T exact-search-admin python -m app.cli exact-search backfill --if-required
compose --profile exact-search-operations run --rm --no-deps -T exact-search-admin python -m app.cli exact-search verify
compose up -d --no-build --force-recreate converter search-projection-worker api frontend nginx
wait_for_service converter
wait_for_service search-projection-worker
wait_for_service api
wait_for_service frontend
wait_for_service nginx
if [ "$worker_should_run" = true ]; then
  compose --profile account-lifecycle up -d --no-build --force-recreate account-erasure-worker
  wait_for_profiled_service account-lifecycle account-erasure-worker
  if [ "$worker_should_pause" = true ]; then
    compose --profile account-lifecycle pause account-erasure-worker
    [ "$(profiled_service_state account-lifecycle account-erasure-worker)" = "paused" ] || die "account-erasure-worker did not return to its prior paused state"
  fi
fi
reconfigure_complete=true
printf 'RECONFIGURED_IMAGE_TAG=%s\n' "$image_tag"
printf 'PostgreSQL and Meilisearch credential rotation require a separately planned data-service procedure.\n'
