#!/usr/bin/env bash
set -Eeuo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/lib.sh"

ensure_repo
validate_production_env
readonly service_health_attempts=30
readonly lifecycle_health_attempts=30
for service in postgres meilisearch converter search-projection-worker api frontend nginx; do
  wait_for_service "$service" "$service_health_attempts"
  printf 'SERVICE_HEALTH=%s:PASS\n' "$service"
done

lifecycle_enabled="$(read_env_optional_value CONNECTMD_ACCOUNT_LIFECYCLE_ENABLED)" || die "CONNECTMD_ACCOUNT_LIFECYCLE_ENABLED must appear at most once in .env"
if [ "${lifecycle_enabled:-false}" = "true" ]; then
  wait_for_profiled_service account-lifecycle account-erasure-worker "$lifecycle_health_attempts"
  printf 'SERVICE_HEALTH=account-erasure-worker:PASS\n'
fi

compose exec -T nginx wget --no-verbose --spider http://127.0.0.1/nginx-health
printf 'NGINX_INTERNAL_PROBE=PASS\n'
compose exec -T api python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:8000' + os.environ['API_READINESS_PATH'], timeout=3).read()"
printf 'API_READINESS_PROBE=PASS\n'
printf 'HEALTH=PASS\n'
