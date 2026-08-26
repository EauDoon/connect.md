#!/usr/bin/env bash
set -Eeuo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/lib.sh"

ensure_repo
acquire_operation_lock
validate_production_env
load_active_release_identity >/dev/null
export CONNECTMD_IMAGE_TAG="$RELEASE_IMAGE_TAG"
assert_service_image_identity api "$RELEASE_API_IMAGE_ID"
service_is_running api || die "Accepted API service is not running"

compose exec -T api python -m app.cli retention run --limit 100
