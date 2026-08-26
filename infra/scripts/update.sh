#!/usr/bin/env bash
set -Eeuo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/lib.sh"

[ "$#" -eq 1 ] || die "Usage: ${0##*/} FULL_TARGET_REVISION"
target_revision="$1"
is_full_source_revision "$target_revision" \
  || die "Update target must be a canonical full source revision"

ensure_repo
acquire_operation_lock
validate_production_env
ensure_clean_source
assert_no_pending_staged_release

current_source_revision_value="$(current_source_revision)"
upstream_ref="$(git -C "$REPO_ROOT" rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null)" \
  || die "Update requires a configured upstream branch"
case "$upstream_ref" in
  origin/*) ;;
  *) die "Update upstream must be an origin branch" ;;
esac

git -C "$REPO_ROOT" fetch --tags --prune origin
resolved_target_revision="$(git -C "$REPO_ROOT" rev-parse --verify "${target_revision}^{commit}" 2>/dev/null)" \
  || die "Requested update target is unavailable after fetching origin"
[ "$resolved_target_revision" = "$target_revision" ] \
  || die "Requested update target must be a canonical full commit revision"
git -C "$REPO_ROOT" show-ref --verify --quiet "refs/remotes/$upstream_ref" \
  || die "Configured update upstream is unavailable after fetching origin"
git -C "$REPO_ROOT" merge-base --is-ancestor "$current_source_revision_value" "$target_revision" \
  || die "Requested update target is not a fast-forward descendant of the current source"
git -C "$REPO_ROOT" merge-base --is-ancestor "$target_revision" "$upstream_ref" \
  || die "Requested update target is not reachable from the fetched origin upstream"

load_active_release_identity
previous_image_tag="$RELEASE_IMAGE_TAG"
previous_source_revision="$RELEASE_SOURCE_REVISION"
previous_api_image_id="$RELEASE_API_IMAGE_ID"
previous_web_image_id="$RELEASE_WEB_IMAGE_ID"
previous_nginx_image_id="$RELEASE_NGINX_IMAGE_ID"

if [ "$target_revision" = "$current_source_revision_value" ]; then
  printf 'UPDATE=NO_CHANGE\n'
  printf 'CURRENT_SOURCE_REVISION=%s\n' "$current_source_revision_value"
  exit 0
fi

printf 'PREVIOUS_IMAGE_TAG=%s\n' "$previous_image_tag"
printf 'PREVIOUS_SOURCE_REVISION=%s\n' "$previous_source_revision"
printf 'PREVIOUS_API_IMAGE_ID=%s\n' "$previous_api_image_id"
printf 'PREVIOUS_WEB_IMAGE_ID=%s\n' "$previous_web_image_id"
printf 'PREVIOUS_NGINX_IMAGE_ID=%s\n' "$previous_nginx_image_id"
printf 'TARGET_SOURCE_REVISION=%s\n' "$target_revision"
bash "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/backup.sh"
git -C "$REPO_ROOT" merge --ff-only "$target_revision"
ensure_clean_source
[ "$(current_source_revision)" = "$target_revision" ] \
  || die "Fast-forward did not produce the requested exact source revision"

bash "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/deploy.sh"
load_staged_release >/dev/null
[ "$STAGED_SOURCE_REVISION" = "$target_revision" ] \
  || die "Staged deployment did not record the requested exact source revision"
printf 'STAGED_IMAGE_TAG=%s\n' "$STAGED_IMAGE_TAG"
printf 'STAGED_SOURCE_REVISION=%s\n' "$STAGED_SOURCE_REVISION"
printf 'STAGED_API_IMAGE_ID=%s\n' "$STAGED_API_IMAGE_ID"
printf 'STAGED_WEB_IMAGE_ID=%s\n' "$STAGED_WEB_IMAGE_ID"
printf 'STAGED_NGINX_IMAGE_ID=%s\n' "$STAGED_NGINX_IMAGE_ID"
printf 'Run infra/scripts/release-accept.sh --yes-accept after public TLS verification.\n'
