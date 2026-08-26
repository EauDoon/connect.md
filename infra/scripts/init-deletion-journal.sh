#!/usr/bin/env bash
set -Eeuo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/lib.sh"

readonly authority_uid=10001
readonly authority_gid=10001
require_command id
[ "$(id -u)" = "$authority_uid" ] || die "Deletion authorities must be initialized by host UID 10001"
[ "$(id -g)" = "$authority_gid" ] || die "Deletion authorities must be initialized by host GID 10001"

ensure_repo
acquire_operation_lock
ensure_clean_source
validate_production_env
require_command realpath
require_command find
require_command stat
umask 077

image_tag="$(current_image_tag)"
export CONNECTMD_IMAGE_TAG="$image_tag"
api_image_created_by_initializer=false
api_image_created_id=""

remove_initializer_owned_api_image() {
  local current_image_id
  [ "$api_image_created_by_initializer" = true ] || return 0
  [ -n "$api_image_created_id" ] || {
    printf 'ERROR: Initializer-created API image identity was not captured; refusing removal: connectmd-api:%s\n' "$image_tag" >&2
    return 1
  }
  current_image_id="$(docker image inspect --format '{{.Id}}' "connectmd-api:$image_tag" 2>/dev/null)" || {
    printf 'ERROR: Initializer-created API image is missing before cleanup: connectmd-api:%s\n' "$image_tag" >&2
    return 1
  }
  [ "$current_image_id" = "$api_image_created_id" ] || {
    printf 'ERROR: Initializer-created API image identity changed; refusing removal: connectmd-api:%s\n' "$image_tag" >&2
    return 1
  }
  docker image rm "connectmd-api:$image_tag" >/dev/null || {
    printf 'ERROR: Could not remove initializer-created API image: connectmd-api:%s\n' "$image_tag" >&2
    return 1
  }
  api_image_created_by_initializer=false
}

cleanup_initializer_owned_api_image() {
  local status=$?
  trap - EXIT
  if ! remove_initializer_owned_api_image; then
    status=1
  fi
  exit "$status"
}
trap cleanup_initializer_owned_api_image EXIT

backup_root_path="$(backup_root)"
lifecycle_root="$backup_root_path/.connectmd-lifecycle"
journal_root="$lifecycle_root/deletion-journal"
witness_root="$(read_env_value CONNECTMD_DELETION_WITNESS_DIR)"
[ -n "$witness_root" ] || die "CONNECTMD_DELETION_WITNESS_DIR is required"
case "$witness_root" in /*) ;; *) die "CONNECTMD_DELETION_WITNESS_DIR must be absolute" ;; esac
[ "$(realpath -m "$witness_root")" = "$witness_root" ] || die "CONNECTMD_DELETION_WITNESS_DIR must be canonical"
[ "$witness_root" != / ] || die "CONNECTMD_DELETION_WITNESS_DIR must not be the filesystem root"
case "$witness_root" in "$backup_root_path" | "$backup_root_path"/*) die "Deletion witness authority must be outside CONNECTMD_BACKUP_DIR" ;; esac
case "$backup_root_path" in "$witness_root" | "$witness_root"/*) die "Deletion witness authority must not contain CONNECTMD_BACKUP_DIR" ;; esac
[ ! -L "$journal_root" ] || die "Deletion journal root must not be a symlink"
[ ! -L "$witness_root" ] || die "Deletion witness root must not be a symlink"
[ -d "$witness_root" ] || die "Pre-create CONNECTMD_DELETION_WITNESS_DIR with owner-only deploy-account access"
[ "$(stat -c '%u' "$witness_root")" = "$authority_uid" ] || die "CONNECTMD_DELETION_WITNESS_DIR must be owned by UID 10001"
[ "$(stat -c '%g' "$witness_root")" = "$authority_gid" ] || die "CONNECTMD_DELETION_WITNESS_DIR must be owned by GID 10001"
[ "$(stat -c '%a' "$witness_root")" = "700" ] || die "CONNECTMD_DELETION_WITNESS_DIR permissions must be 700"
if [ -e "$lifecycle_root" ] && [ ! -f "$journal_root/state.json" ]; then
  [ -d "$lifecycle_root" ] && [ ! -L "$lifecycle_root" ] || die "Lifecycle evidence root is unsafe"
  [ -z "$(find "$lifecycle_root" -mindepth 1 -maxdepth 1 ! -name deletion-journal -print -quit)" ] || die "Lifecycle evidence exists without an initialized deletion journal; explicit recovery is required"
  if [ -e "$journal_root" ]; then
    [ -d "$journal_root" ] && [ ! -L "$journal_root" ] || die "Deletion journal root is unsafe"
    [ -z "$(find "$journal_root" -mindepth 1 -print -quit)" ] || die "Deletion journal is partially initialized; explicit recovery is required"
  fi
fi
mkdir -p "$journal_root"
[ "$(realpath -e "$journal_root")" = "$journal_root" ] || die "Deletion journal root path is unsafe"
[ "$(stat -c '%u' "$journal_root")" = "$authority_uid" ] || die "Deletion journal root must be owned by UID 10001"
[ "$(stat -c '%g' "$journal_root")" = "$authority_gid" ] || die "Deletion journal root must be owned by GID 10001"
chmod 700 "$journal_root"
[ "$(stat -c '%a' "$journal_root")" = "700" ] || die "Deletion journal root permissions must be 700"
[ "$(realpath -e "$witness_root")" = "$witness_root" ] || die "Deletion witness root path is unsafe"

if docker image inspect "connectmd-api:$image_tag" >/dev/null 2>&1; then
  die "Source-tagged API image already exists; initializer cannot prove ownership: connectmd-api:$image_tag"
fi
api_image_created_by_initializer=true
compose build api
api_image_created_id="$(image_identity_for_tag connectmd-api "$image_tag")"
compose --profile account-lifecycle run --rm --no-deps -T api python -m app.cli deletion-journal init
compose --profile account-lifecycle run --rm --no-deps -T api python -m app.cli deletion-journal checkpoint

if ! remove_initializer_owned_api_image; then
  trap - EXIT
  die "Deletion authorities were initialized, but the exact initializer-created API image could not be removed; resolve the reported tag before deploy"
fi
trap - EXIT
printf 'DELETION_JOURNAL_AND_WITNESS_AUTHORITIES=INITIALIZED\n'
