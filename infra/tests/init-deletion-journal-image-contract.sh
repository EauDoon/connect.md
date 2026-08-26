#!/usr/bin/env bash
# Hermetic ownership and cleanup coverage for the initializer's temporary API image.
set -Eeuo pipefail

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
readonly SOURCE_SCRIPT="$REPO_ROOT/infra/scripts/init-deletion-journal.sh"
readonly EXPECTED_TAG=0123456789abcdef0123456789abcdef01234567-a1b2c3d4e5f60708

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

temp_root="${TMPDIR:-/tmp}"
scratch="$(mktemp -d "$temp_root/connectmd-journal-image-contract.XXXXXX")"
cleanup() {
  local status=$?
  trap - EXIT
  case "$scratch" in
    "$temp_root"/connectmd-journal-image-contract.*) rm -rf -- "$scratch" ;;
    *) printf 'ERROR: Refusing unsafe test cleanup: %s\n' "$scratch" >&2; status=1 ;;
  esac
  exit "$status"
}
trap cleanup EXIT

make_fixture() {
  local name="$1"
  local fixture="$scratch/$name"
  mkdir -p "$fixture/infra/scripts" "$fixture/backup" "$fixture/witness"
  cp "$SOURCE_SCRIPT" "$fixture/infra/scripts/init-deletion-journal.sh"
  cat > "$fixture/infra/scripts/lib.sh" <<'LIBRARY'
#!/usr/bin/env bash

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
require_command() { command -v "$1" >/dev/null 2>&1 || die "Missing command: $1"; }
ensure_repo() { :; }
acquire_operation_lock() { :; }
ensure_clean_source() { printf 'CLEAN_SOURCE\n' >> "$FAKE_IMAGE_LOG"; }
validate_production_env() { :; }
current_image_tag() { printf '%s\n' "$EXPECTED_TAG"; }
backup_root() { printf '%s\n' "$FAKE_BACKUP_ROOT"; }
read_env_value() {
  [ "$1" = CONNECTMD_DELETION_WITNESS_DIR ] || die "Unexpected environment lookup: $1"
  printf '%s\n' "$FAKE_WITNESS_ROOT"
}
id() {
  case "${1:-}" in
    -u | -g) printf '10001\n' ;;
    *) command id "$@" ;;
  esac
}
stat() {
  if [ "${1:-}" = -c ]; then
    case "${2:-}" in
      %u | %g) printf '10001\n'; return 0 ;;
      %a) printf '700\n'; return 0 ;;
    esac
  fi
  command stat "$@"
}
docker() {
  local target
  case "${1:-} ${2:-}" in
    "image inspect")
      target="${!#}"
      [ "$target" = "connectmd-api:$EXPECTED_TAG" ] || die "Unexpected image inspection target: $target"
      [ -f "$FAKE_IMAGE_STATE" ] || return 1
      if [ "${3:-}" = --format ]; then
        cat "$FAKE_IMAGE_STATE"
      fi
      ;;
    "image rm")
      target="${3:-}"
      [ "$target" = "connectmd-api:$EXPECTED_TAG" ] || die "Unexpected image removal target: $target"
      printf 'REMOVE %s\n' "$target" >> "$FAKE_IMAGE_LOG"
      rm -f -- "$FAKE_IMAGE_STATE"
      ;;
    *) die "Unexpected Docker operation: $*" ;;
  esac
}
image_identity_for_tag() {
  docker image inspect --format '{{.Id}}' "$1:$2"
}
compose() {
  if [ "${1:-}" = build ] && [ "${2:-}" = api ]; then
    printf 'BUILD connectmd-api:%s\n' "$EXPECTED_TAG" >> "$FAKE_IMAGE_LOG"
    printf 'sha256:initializer-owned\n' > "$FAKE_IMAGE_STATE"
    return 0
  fi
  if [ "${!#}" = init ] && [ "${FAIL_ON_INIT:-false}" = true ]; then
    return 23
  fi
  if [ "${!#}" = checkpoint ] && [ "${RETARGET_ON_CHECKPOINT:-false}" = true ]; then
    printf 'sha256:retargeted\n' > "$FAKE_IMAGE_STATE"
  fi
}
LIBRARY
  printf '%s\n' "$fixture"
}

run_initializer() {
  local fixture="$1"
  shift
  env \
    EXPECTED_TAG="$EXPECTED_TAG" \
    FAKE_BACKUP_ROOT="$fixture/backup" \
    FAKE_WITNESS_ROOT="$fixture/witness" \
    FAKE_IMAGE_STATE="$fixture/image-state" \
    FAKE_IMAGE_LOG="$fixture/image.log" \
    "$@" \
    bash "$fixture/infra/scripts/init-deletion-journal.sh"
}

created_fixture="$(make_fixture created)"
created_output="$(run_initializer "$created_fixture")" \
  || die "Initializer failed while cleaning its own image"
printf '%s\n' "$created_output" | grep -Fxq 'DELETION_JOURNAL_AND_WITNESS_AUTHORITIES=INITIALIZED' \
  || die "Initializer did not report success after exact image cleanup"
[ ! -e "$created_fixture/image-state" ] || die "Initializer-created image tag survived success"
grep -Fxq "BUILD connectmd-api:$EXPECTED_TAG" "$created_fixture/image.log" \
  || die "Initializer did not build the source-tagged API image"
[ "$(grep -Fxc "REMOVE connectmd-api:$EXPECTED_TAG" "$created_fixture/image.log")" = 1 ] \
  || die "Initializer did not remove exactly its source-tagged API image"

preexisting_fixture="$(make_fixture preexisting)"
printf 'sha256:preexisting\n' > "$preexisting_fixture/image-state"
if preexisting_output="$(run_initializer "$preexisting_fixture" 2>&1)"; then
  die "Initializer unexpectedly accepted a pre-existing API image"
fi
printf '%s\n' "$preexisting_output" | grep -Fq 'Source-tagged API image already exists; initializer cannot prove ownership' \
  || die "Initializer did not explain the pre-existing image refusal"
[ "$(cat "$preexisting_fixture/image-state")" = sha256:preexisting ] \
  || die "Initializer altered a pre-existing API image"
if grep -Eq '^(BUILD|REMOVE) ' "$preexisting_fixture/image.log"; then
  die "Initializer built or removed an API image it did not create"
fi

failed_fixture="$(make_fixture failed-init)"
if failed_output="$(run_initializer "$failed_fixture" FAIL_ON_INIT=true 2>&1)"; then
  die "Initializer unexpectedly succeeded when journal initialization failed"
fi
[ ! -e "$failed_fixture/image-state" ] \
  || die "Initializer left its exact image behind after a later failure"
[ "$(grep -Fxc "REMOVE connectmd-api:$EXPECTED_TAG" "$failed_fixture/image.log")" = 1 ] \
  || die "Failure cleanup did not remove exactly the initializer-owned image"

retargeted_fixture="$(make_fixture retargeted)"
if retargeted_output="$(run_initializer "$retargeted_fixture" RETARGET_ON_CHECKPOINT=true 2>&1)"; then
  die "Initializer unexpectedly succeeded after its image tag changed identity"
fi
printf '%s\n' "$retargeted_output" | grep -Fq 'Initializer-created API image identity changed; refusing removal' \
  || die "Initializer did not explain the identity mismatch"
[ "$(cat "$retargeted_fixture/image-state")" = sha256:retargeted ] \
  || die "Initializer removed or changed an image after losing ownership proof"
if grep -Fq "REMOVE connectmd-api:$EXPECTED_TAG" "$retargeted_fixture/image.log"; then
  die "Initializer removed a retargeted image"
fi

printf 'INIT_DELETION_JOURNAL_IMAGE_CONTRACT=PASS\n'
