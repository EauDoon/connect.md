#!/usr/bin/env bash
# Exercise the production recovery path in an isolated local Linux authority.
set -Eeuo pipefail

readonly CONTAINER_UID=10001
readonly CONTAINER_GID=10001

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "Required command is unavailable: $1"
}

require_clean_head() {
  local repository="$1" status
  git -C "$repository" rev-parse --verify HEAD >/dev/null 2>&1 || die "Recovery roundtrip requires an actual committed checkout HEAD"
  status="$(git -C "$repository" status --porcelain=v1 --untracked-files=normal)"
  [ -z "$status" ] || die "Recovery roundtrip requires a clean checkout"
}

require_project_name() {
  printf '%s' "$1" | grep -Eq '^[a-z0-9][a-z0-9_-]{0,62}$' || die "Recovery Compose project name is invalid"
}

assert_child_path() {
  local parent="$1" child="$2" canonical_parent canonical_child
  canonical_parent="$(realpath -e "$parent")"
  canonical_child="$(realpath -e "$child")"
  case "$canonical_child" in
    "$canonical_parent"/*) ;;
    *) die "Recovery path escaped its scratch root: $canonical_child" ;;
  esac
}

run_outer() {
  local script_dir repo_root temp_parent source_revision run_token docker_socket_group

  scratch=""
  worktree=""
  project_name=""
  temp_root=""
  host_uid=""
  host_gid=""
  project_created=false
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
  repo_root="$(cd "$script_dir/../.." && pwd -P)"
  host_uid="$(id -u)"
  host_gid="$(id -g)"
  temp_parent="${RUNNER_TEMP:-${TMPDIR:-/tmp}}"
  temp_root="$(realpath -e "$temp_parent")"

  cleanup() {
    local status=$?
    trap - EXIT
    set +e
    if [ "$project_created" = true ] && [ -n "$worktree" ] && [ -f "$worktree/.env" ]; then
      sudo --non-interactive docker compose --project-name "$project_name" --env-file "$worktree/.env" \
        -f "$worktree/compose.yaml" -f "$worktree/compose.prod.yaml" down --volumes --remove-orphans >/dev/null 2>&1
    fi
    if [ -n "$scratch" ] && [ -d "$scratch" ]; then
      case "$scratch" in
        "$temp_root"/connectmd-ci-recovery.*)
          sudo --non-interactive chown -R "$host_uid:$host_gid" -- "$scratch" >/dev/null 2>&1 || true
          rm -rf -- "$scratch"
          ;;
      esac
    fi
    exit "$status"
  }
  trap cleanup EXIT

  for command in docker git mktemp realpath stat sudo setpriv tr; do
    require_command "$command"
  done
  sudo --non-interactive true || die "Passwordless sudo is required for the recovery ownership boundary"
  require_clean_head "$repo_root"
  [ -S /var/run/docker.sock ] || die "Docker socket is unavailable"
  docker_socket_group="$(stat -c '%g' /var/run/docker.sock)"
  case "$docker_socket_group" in '' | *[!0-9]*) die "Docker socket group is invalid" ;; esac

  # RUNNER_TEMP may be private to the runner account. Recovery runs as UID
  # 10001, so select a root that the dropped process can traverse.
  can_access_temp_root() {
    sudo --non-interactive setpriv --reuid "$CONTAINER_UID" --regid "$CONTAINER_GID" --groups "$docker_socket_group" -- /usr/bin/test -d "$1" \
      && sudo --non-interactive setpriv --reuid "$CONTAINER_UID" --regid "$CONTAINER_GID" --groups "$docker_socket_group" -- /usr/bin/test -x "$1"
  }
  if ! can_access_temp_root "$temp_root"; then
    temp_root="$(realpath -e /tmp)"
  fi
  can_access_temp_root "$temp_root" || die "Recovery scratch root is not traversable by UID 10001"

  run_token="$(printf '%s' "${GITHUB_RUN_ID:-$$}-${GITHUB_RUN_ATTEMPT:-0}" | tr -cd 'a-z0-9')"
  [ -n "$run_token" ] || die "Recovery run token is invalid"
  project_name="connectmd-ci-recovery-$run_token"
  require_project_name "$project_name"
  scratch="$(mktemp -d "$temp_root/connectmd-ci-recovery.XXXXXX")"
  scratch="$(realpath -e "$scratch")"
  case "$scratch" in "$temp_root"/connectmd-ci-recovery.*) ;; *) die "Recovery scratch path is unsafe" ;; esac
  worktree="$scratch/app"
  source_revision="$(git -C "$repo_root" rev-parse HEAD)"

  git clone --quiet --no-hardlinks "$repo_root" "$worktree"
  [ "$(git -C "$worktree" rev-parse HEAD)" = "$source_revision" ] || die "Recovery clone did not preserve the checkout revision"
  require_clean_head "$worktree"
  assert_child_path "$scratch" "$worktree"
  for state_file in .env .connectmd-release.env .connectmd-restore-state.env .connectmd-operations.lock; do
    [ ! -e "$worktree/$state_file" ] && [ ! -L "$worktree/$state_file" ] || die "Recovery clone unexpectedly contains runtime state: $state_file"
  done

  sudo --non-interactive chown -R "$CONTAINER_UID:$CONTAINER_GID" -- "$scratch"
  sudo --non-interactive setpriv --reuid "$CONTAINER_UID" --regid "$CONTAINER_GID" --groups "$docker_socket_group" -- /usr/bin/test -r "$worktree/infra/tests/recovery-roundtrip.sh" \
    || die "Recovery child script is not readable by UID 10001"
  project_created=true
  sudo --non-interactive setpriv --reuid "$CONTAINER_UID" --regid "$CONTAINER_GID" --groups "$docker_socket_group" -- \
    env -u HOME \
      CONNECTMD_RECOVERY_INNER=1 \
      CONNECTMD_RECOVERY_SCRATCH="$scratch" \
      CONNECTMD_RECOVERY_TOKEN="$run_token" \
      CONNECTMD_COMPOSE_PROJECT_NAME="$project_name" \
      bash "$worktree/infra/tests/recovery-roundtrip.sh"
}

set_env_value() {
  local key="$1" value="$2" escaped_value
  escaped_value="$(printf '%s' "$value" | sed -e 's/[\\&|]/\\&/g')"
  sed -i -E "s|^${key}=.*$|${key}=${escaped_value}|" "$ENV_FILE"
}

compose_test() {
  docker compose --project-name "$CONNECTMD_COMPOSE_PROJECT_NAME" --env-file "$ENV_FILE" \
    -f "$REPO_ROOT/compose.yaml" -f "$REPO_ROOT/compose.prod.yaml" "$@"
}

wait_for_service() {
  local service="$1" container status attempt
  container="$(compose_test ps -q "$service")"
  [ -n "$container" ] || die "No recovery container found for $service"
  for attempt in $(seq 1 60); do
    status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container")"
    case "$status" in
      healthy | running) return 0 ;;
      unhealthy | exited | dead) die "Recovery service entered state: $service=$status" ;;
    esac
    sleep 2
  done
  die "Timed out waiting for recovery service: $service"
}

bootstrap_key() {
  local purpose="$1" expected_key="$2" output value
  output="$(compose_test --profile search-bootstrap run --rm --no-deps -T search-key-bootstrap python -m app.search_key_bootstrap "$purpose")"
  [ "$(printf '%s\n' "$output" | wc -l | tr -d ' ')" = "1" ] || die "Scoped-key bootstrap output was not one line"
  case "$output" in
    "${expected_key}"=*) ;;
    *) die "Scoped-key bootstrap output did not match $expected_key" ;;
  esac
  value="${output#*=}"
  [ "${#value}" -ge 16 ] || die "Scoped-key bootstrap returned a short key"
  set_env_value "$expected_key" "$value"
}

assert_database_marker() {
  local expected="$1" actual
  actual="$(compose_test exec -T postgres psql -At -U postgres -d connectmd -c 'SELECT marker FROM ci_recovery_probe')"
  [ "$actual" = "$expected" ] || die "PostgreSQL recovery marker was not restored"
}

assert_storage_marker() {
  local expected="$1"
  compose_test --profile ops run --rm --no-deps -T -e "EXPECTED_MARKER=$expected" storage-restore sh -ceu '
    test "$(cat /storage/ci-recovery/roundtrip.md)" = "$EXPECTED_MARKER"
  '
}

assert_not_running() {
  local service="$1" profile="${2:-}" running
  if [ -n "$profile" ]; then
    running="$(compose_test --profile "$profile" ps -q "$service")"
  else
    running="$(compose_test ps -q "$service")"
  fi
  [ -z "$running" ] || die "Recovery restore left $service running"
}

assert_running() {
  local service="$1" profile="${2:-}" container
  if [ -n "$profile" ]; then
    container="$(compose_test --profile "$profile" ps -q "$service")"
  else
    container="$(compose_test ps -q "$service")"
  fi
  [ -n "$container" ] || die "Recovery preflight unexpectedly stopped $service"
}

set_metadata_value() {
  local file="$1" key="$2" value="$3" escaped_value
  escaped_value="$(printf '%s' "$value" | sed -e 's/[\\&|]/\\&/g')"
  sed -i -E "s|^${key}=.*$|${key}=${escaped_value}|" "$file"
}

refresh_backup_checksum() {
  local directory="$1"
  (
    cd "$directory"
    sha256sum metadata.env postgres.dump markdown-storage.tar.gz > SHA256SUMS
  )
}

assert_restore_preflight_rejected() {
  local directory="$1" expected_message="$2" output
  if output="$(bash infra/scripts/restore.sh "$directory" --yes-restore 2>&1)"; then
    die "Recovery restore unexpectedly accepted corrupted preflight input"
  fi
  printf '%s\n' "$output" | grep -Fq "$expected_message" \
    || die "Recovery restore rejected corrupted input for the wrong reason: $output"
  assert_running nginx
  assert_running frontend
  assert_running api
  assert_running converter
  assert_running search-projection-worker
}

# Recovery is a hermetic Docker drill on an invented .test name. It cannot
# provide public DNS/CA evidence and must never call release-accept.sh. Seed
# only the minimal immutable acceptance fixture required to exercise the
# accepted-authority backup and restore paths; production promotion remains
# exclusively release-accept.sh --yes-accept.
seed_test_only_accepted_authority() {
  local evidence key acceptance
  [ "${CONNECTMD_RECOVERY_INNER:-}" = 1 ] || die "Test-only acceptance fixture is restricted to recovery inner mode"
  source "$REPO_ROOT/infra/scripts/lib.sh"
  load_staged_release >/dev/null
  evidence="$(mktemp "$REPO_ROOT/.connectmd-recovery-acceptance.XXXXXX")"
  chmod 600 "$evidence"
  {
    printf 'https_origin=https://%s\n' "$(require_hostname)"
    for key in tls_leaf_sha256 http_redirect_sha256 hsts_sha256 openapi_sha256 llms_sha256 llms_full_sha256 agent_card_sha256 oauth_sha256 mcp_oauth_sha256 public_search_sha256 mcp_initialize_tools_search_sha256 a2a_search_sha256; do
      printf '%s=%064d\n' "$key" 0
    done
  } > "$evidence"
  acceptance="$(write_release_acceptance "$STAGED_SOURCE_REVISION" "$STAGED_IMAGE_TAG" "$STAGED_API_IMAGE_ID" "$STAGED_WEB_IMAGE_ID" "$STAGED_NGINX_IMAGE_ID" "$STAGED_RELEASE_DIGEST" "$evidence")"
  rm -f -- "$evidence"
  persist_image_tag "$STAGED_IMAGE_TAG" "$STAGED_SOURCE_REVISION" "$STAGED_API_IMAGE_ID" "$STAGED_WEB_IMAGE_ID" "$STAGED_NGINX_IMAGE_ID" "$STAGED_RELEASE_DIGEST"
  clear_staged_release_after_acceptance "$STAGED_RELEASE_DIGEST"
  clear_matching_completed_restore_state "$STAGED_SOURCE_REVISION" "$STAGED_IMAGE_TAG" "$STAGED_API_IMAGE_ID" "$STAGED_WEB_IMAGE_ID" "$STAGED_NGINX_IMAGE_ID" "$STAGED_RELEASE_RECEIPT_DIGEST"
  [ -f "$acceptance" ] && [ ! -L "$acceptance" ] || die "Test-only acceptance fixture did not create an immutable receipt"
}

run_inner() {
  local script_dir scratch token backup_dir witness_dir public_base domain backup_output backup_line backup_path
  local generation receipt verify_output restore_output backup_tag release_tag
  local backup_source backup_api_id backup_web_id backup_nginx_id backup_receipt_digest corrupt_source corrupt_image
  local legacy_backup legacy_restore_output legacy_deploy_output

  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
  REPO_ROOT="$(cd "$script_dir/../.." && pwd -P)"
  ENV_FILE="$REPO_ROOT/.env"
  scratch="${CONNECTMD_RECOVERY_SCRATCH:-}"
  token="${CONNECTMD_RECOVERY_TOKEN:-}"
  [ -n "$scratch" ] && [ -n "$token" ] || die "Recovery inner scope is missing"
  scratch="$(realpath -e "$scratch")"
  case "$scratch" in */connectmd-ci-recovery.*) ;; *) die "Recovery inner scratch path is invalid" ;; esac
  assert_child_path "$scratch" "$REPO_ROOT"
  [ "$REPO_ROOT" = "$scratch/app" ] || die "Recovery inner repository is outside the cloned worktree"
  require_project_name "${CONNECTMD_COMPOSE_PROJECT_NAME:-}"
  [ "$(id -u)" = "$CONTAINER_UID" ] || die "Recovery scripts must run as UID 10001"
  [ "$(id -g)" = "$CONTAINER_GID" ] || die "Recovery scripts must run as GID 10001"
  [ "$(stat -c '%u:%g' "$REPO_ROOT")" = "$CONTAINER_UID:$CONTAINER_GID" ] || die "Recovery worktree ownership is invalid"
  docker version --format '{{.Server.Version}}' >/dev/null || die "UID 10001 cannot use the Docker daemon"
  require_clean_head "$REPO_ROOT"

  backup_dir="$scratch/backups"
  witness_dir="$scratch/deletion-head-witness"
  case "$backup_dir" in "$scratch"/*) ;; *) die "Recovery backup path escaped scratch scope" ;; esac
  case "$witness_dir" in "$scratch"/*) ;; *) die "Recovery witness path escaped scratch scope" ;; esac
  mkdir -p "$backup_dir/.connectmd-lifecycle/deletion-journal" "$witness_dir"
  assert_child_path "$scratch" "$backup_dir"
  assert_child_path "$scratch" "$witness_dir"
  [ ! -e "$ENV_FILE" ] && [ ! -L "$ENV_FILE" ] || die "Recovery environment file already exists"
  for state_file in .connectmd-release.env .connectmd-restore-state.env .connectmd-operations.lock; do
    [ ! -e "$REPO_ROOT/$state_file" ] && [ ! -L "$REPO_ROOT/$state_file" ] || die "Recovery runtime state already exists: $state_file"
  done

  domain="recovery-$token.test"
  public_base="https://$domain"
  cp "$REPO_ROOT/.env.example" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  set_env_value POSTGRES_PASSWORD 1111111111111111111111111111111111111111111111111111111111111111
  set_env_value CONNECTMD_MIGRATOR_DB_PASSWORD 2222222222222222222222222222222222222222222222222222222222222222
  set_env_value CONNECTMD_API_DB_PASSWORD 3333333333333333333333333333333333333333333333333333333333333333
  set_env_value CONNECTMD_PROJECTION_ADMIN_DB_PASSWORD 4444444444444444444444444444444444444444444444444444444444444444
  set_env_value CONNECTMD_ACCOUNT_ERASURE_DB_PASSWORD 5555555555555555555555555555555555555555555555555555555555555555
  set_env_value CONNECTMD_BACKUP_DB_PASSWORD 6666666666666666666666666666666666666666666666666666666666666666
  set_env_value MEILI_MASTER_KEY ci-recovery-meili-master-key-0123456789
  set_env_value CONNECTMD_MEILISEARCH_SEARCH_KEY ci-recovery-search-key-pending-0123456789
  set_env_value CONNECTMD_SEARCH_PROJECTION_MEILI_KEY ci-recovery-projection-key-pending-0123456789
  set_env_value CONNECTMD_SEARCH_PROJECTION_DB_PASSWORD 7777777777777777777777777777777777777777777777777777777777777777
  set_env_value CONNECTMD_CLERK_JWKS_URL https://clerk.recovery.test/.well-known/jwks.json
  set_env_value CONNECTMD_CLERK_ISSUER https://clerk.recovery.test
  set_env_value CONNECTMD_CLERK_AUTHORIZED_PARTIES "[\"$public_base\"]"
  set_env_value CONNECTMD_EXACT_SEARCH_CURSOR_KEYRING '[{"kid":"v1","secret":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"}]'
  set_env_value CONNECTMD_API_KEY_PEPPER ci-recovery-api-key-pepper-0123456789
  set_env_value CONNECTMD_VERIFICATION_REVIEWER_ID ci-recovery-verification-authority
  set_env_value CONNECTMD_POST_MODERATOR_ID ci-recovery-moderation-authority
  set_env_value CONNECTMD_APPEAL_REVIEWER_ID ci-recovery-appeal-authority
  set_env_value NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY pk_test_Zm9vLWJhci0xLmNsZXJrLmFjY291bnRzLmRldiQ=
  set_env_value CLERK_SECRET_KEY "sk_test_$(printf '%s' ci-recovery-clerk-secret-0123456789)"
  set_env_value CONNECTMD_DOMAIN "$domain"
  set_env_value CONNECTMD_PUBLIC_BASE_URL "$public_base"
  set_env_value NEXT_PUBLIC_SITE_URL "$public_base"
  set_env_value CONNECTMD_HTTP_PORT 18081
  set_env_value CONNECTMD_HTTPS_PORT 18444
  set_env_value ACME_EMAIL ci-recovery@invalid.test
  set_env_value CONNECTMD_BACKUP_DIR "$backup_dir"
  set_env_value CONNECTMD_BACKUP_MIN_FREE_BYTES 1
  set_env_value CONNECTMD_ACCOUNT_LIFECYCLE_ENABLED false
  set_env_value NEXT_PUBLIC_ACCOUNT_LIFECYCLE_ENABLED false
  set_env_value CONNECTMD_LIFECYCLE_HMAC_KEY ci-recovery-lifecycle-hmac-key-0123456789
  set_env_value CONNECTMD_LIFECYCLE_AEAD_KEY ci-recovery-lifecycle-aead-key-0123456789
  set_env_value CONNECTMD_DELETION_WITNESS_DIR "$witness_dir"
  set_env_value CONNECTMD_DELETION_WITNESS_HMAC_KEY ci-recovery-deletion-witness-key-0123456789

  chmod 700 "$backup_dir/.connectmd-lifecycle" "$backup_dir/.connectmd-lifecycle/deletion-journal" "$witness_dir"
  [ "$(stat -c '%u:%g:%a' "$backup_dir/.connectmd-lifecycle/deletion-journal")" = "$CONTAINER_UID:$CONTAINER_GID:700" ] || die "Recovery journal authority ownership is invalid"
  [ "$(stat -c '%u:%g:%a' "$witness_dir")" = "$CONTAINER_UID:$CONTAINER_GID:700" ] || die "Recovery witness authority ownership is invalid"

  compose_test up -d postgres meilisearch
  wait_for_service postgres
  wait_for_service meilisearch
  bootstrap_key search CONNECTMD_MEILISEARCH_SEARCH_KEY
  bootstrap_key projection CONNECTMD_SEARCH_PROJECTION_MEILI_KEY

  cd "$REPO_ROOT"
  bash infra/scripts/init-deletion-journal.sh
  bash infra/scripts/deploy.sh
  seed_test_only_accepted_authority
  bash infra/scripts/health.sh

  compose_test exec -T postgres psql -v ON_ERROR_STOP=1 -U postgres -d connectmd -c "CREATE TABLE ci_recovery_probe (marker text PRIMARY KEY); INSERT INTO ci_recovery_probe (marker) VALUES ('before_restore');" >/dev/null
  compose_test exec -T api sh -ceu '
    mkdir -p /app/storage/ci-recovery
    printf "%s\\n" before_restore > /app/storage/ci-recovery/roundtrip.md
  '
  assert_database_marker before_restore
  assert_storage_marker before_restore

  backup_output="$(bash infra/scripts/backup.sh)"
  backup_line="$(printf '%s\n' "$backup_output" | grep -E '^BACKUP=' || true)"
  [ "$(printf '%s\n' "$backup_line" | wc -l | tr -d ' ')" = "1" ] || die "Recovery backup output was invalid"
  backup_path="${backup_line#BACKUP=}"
  backup_path="$(realpath -e "$backup_path")"
  [ "$(dirname "$backup_path")" = "$backup_dir" ] || die "Recovery backup escaped its scratch authority"
  generation="$(basename "$backup_path")"
  printf '%s' "$generation" | grep -Eq '^connectmd-[0-9]{8}T[0-9]{6}Z$' || die "Recovery backup generation is invalid"
  receipt="$backup_dir/.connectmd-lifecycle/registrations/$generation.env"
  [ -f "$receipt" ] && [ ! -L "$receipt" ] || die "Recovery backup registration receipt is missing"
  [ "$(stat -c '%a' "$receipt")" = 600 ] || die "Recovery backup registration receipt permissions are invalid"
  verify_output="$(bash infra/scripts/restore.sh "$backup_path" --verify-only)"
  printf '%s\n' "$verify_output" | grep -Fxq 'RESTORE_INPUT=VERIFIED' || die "Recovery backup verification did not pass"

  backup_tag="$(sed -n 's/^image_tag=//p' "$backup_path/metadata.env")"
  backup_source="$(sed -n 's/^source_revision=//p' "$backup_path/metadata.env")"
  backup_api_id="$(sed -n 's/^api_image_id=//p' "$backup_path/metadata.env")"
  backup_web_id="$(sed -n 's/^web_image_id=//p' "$backup_path/metadata.env")"
  backup_nginx_id="$(sed -n 's/^nginx_image_id=//p' "$backup_path/metadata.env")"
  backup_receipt_digest="$(sed -n 's/^release_receipt_digest=//p' "$backup_path/metadata.env")"
  [ -n "$backup_tag" ] || die "Recovery backup image tag is missing"
  printf '%s' "$backup_source" | grep -Eq '^[0-9a-f]{40}([0-9a-f]{24})?$' || die "Recovery backup source revision is invalid"
  for identity in "$backup_api_id" "$backup_web_id" "$backup_nginx_id"; do
    printf '%s' "$identity" | grep -Eq '^sha256:[0-9a-f]{64}$' || die "Recovery backup image identity is invalid"
  done
  printf '%s' "$backup_receipt_digest" | grep -Eq '^[0-9a-f]{64}$' || die "Recovery backup release receipt digest is invalid"
  [ "$(sed -n 's/^CONNECTMD_SOURCE_REVISION=//p' "$REPO_ROOT/.connectmd-release.env")" = "$backup_source" ] || die "Recovery active release source was not preserved in backup metadata"
  [ "$(sed -n 's/^CONNECTMD_API_IMAGE_ID=//p' "$REPO_ROOT/.connectmd-release.env")" = "$backup_api_id" ] || die "Recovery active API identity was not preserved in backup metadata"
  [ "$(sed -n 's/^CONNECTMD_WEB_IMAGE_ID=//p' "$REPO_ROOT/.connectmd-release.env")" = "$backup_web_id" ] || die "Recovery active web identity was not preserved in backup metadata"
  [ "$(sed -n 's/^CONNECTMD_NGINX_IMAGE_ID=//p' "$REPO_ROOT/.connectmd-release.env")" = "$backup_nginx_id" ] || die "Recovery active Nginx identity was not preserved in backup metadata"

  corrupt_source="$backup_dir/connectmd-20990101T000000Z"
  cp -a "$backup_path" "$corrupt_source"
  set_metadata_value "$corrupt_source/metadata.env" source_revision "0000000000000000000000000000000000000000"
  refresh_backup_checksum "$corrupt_source"
  assert_restore_preflight_rejected "$corrupt_source" "Checked-out source revision does not match the backup generation"

  corrupt_image="$backup_dir/connectmd-20990101T000001Z"
  cp -a "$backup_path" "$corrupt_image"
  set_metadata_value "$corrupt_image/metadata.env" api_image_id "sha256:0000000000000000000000000000000000000000000000000000000000000000"
  refresh_backup_checksum "$corrupt_image"
  assert_restore_preflight_rejected "$corrupt_image" "Release receipt API image identity does not match"

  if ! (
    docker image tag "$backup_web_id" "connectmd-api:$backup_tag"
    assert_restore_preflight_rejected "$backup_path" "Release image tag does not match its recorded identity"
  ); then
    docker image tag "$backup_api_id" "connectmd-api:$backup_tag" >/dev/null
    die "Recovery image-substitution preflight counterexample failed"
  fi
  docker image tag "$backup_api_id" "connectmd-api:$backup_tag" >/dev/null
  [ "$(docker image inspect --format '{{.Id}}' "connectmd-api:$backup_tag")" = "$backup_api_id" ] || die "Recovery API tag was not restored after substitution counterexample"

  if ! (
    docker image rm "connectmd-api:$backup_tag" >/dev/null
    assert_restore_preflight_rejected "$backup_path" "Required release image is unavailable"
  ); then
    docker image tag "$backup_api_id" "connectmd-api:$backup_tag" >/dev/null
    die "Recovery missing-image preflight counterexample failed"
  fi
  docker image tag "$backup_api_id" "connectmd-api:$backup_tag" >/dev/null
  [ "$(docker image inspect --format '{{.Id}}' "connectmd-api:$backup_tag")" = "$backup_api_id" ] || die "Recovery API tag was not restored after missing-image counterexample"

  compose_test exec -T postgres psql -v ON_ERROR_STOP=1 -U postgres -d connectmd -c "TRUNCATE ci_recovery_probe; INSERT INTO ci_recovery_probe (marker) VALUES ('after_backup');" >/dev/null
  compose_test exec -T api sh -ceu 'printf "%s\\n" after_backup > /app/storage/ci-recovery/roundtrip.md'
  assert_database_marker after_backup
  assert_storage_marker after_backup

  restore_output="$(bash infra/scripts/restore.sh "$backup_path" --yes-restore)"
  printf '%s\n' "$restore_output" | grep -Fxq 'RESTORE=COMPLETE' || die "Recovery restore did not complete"
  assert_not_running nginx
  assert_not_running frontend
  assert_not_running api
  assert_not_running converter
  assert_not_running search-projection-worker
  assert_not_running account-erasure-worker account-lifecycle
  grep -Fxq 'format=connectmd-restore-state-v3' "$REPO_ROOT/.connectmd-restore-state.env" || die "Recovery restore did not record topology-aware state"
  for prior_state in api converter projection frontend nginx; do
    grep -Fxq "${prior_state}_prior_state=running" "$REPO_ROOT/.connectmd-restore-state.env" \
      || die "Recovery restore lost prior running state: $prior_state"
  done
  grep -Fxq 'worker_prior_state=absent' "$REPO_ROOT/.connectmd-restore-state.env" \
    || die "Recovery restore lost prior lifecycle-worker absence"
  assert_database_marker before_restore
  assert_storage_marker before_restore

  bash infra/scripts/deploy.sh
  bash infra/scripts/health.sh
  [ ! -e "$REPO_ROOT/.connectmd-restore-state.env" ] && [ ! -L "$REPO_ROOT/.connectmd-restore-state.env" ] || die "Recovery deploy did not clear durable restore state"
  release_tag="$(sed -n 's/^CONNECTMD_IMAGE_TAG=//p' "$REPO_ROOT/.connectmd-release.env")"
  [ "$release_tag" = "$backup_tag" ] || die "Recovery deploy did not restore the recorded image tag"
  [ "$(sed -n 's/^CONNECTMD_SOURCE_REVISION=//p' "$REPO_ROOT/.connectmd-release.env")" = "$backup_source" ] || die "Recovery deploy did not restore the recorded source revision"
  [ "$(sed -n 's/^CONNECTMD_API_IMAGE_ID=//p' "$REPO_ROOT/.connectmd-release.env")" = "$backup_api_id" ] || die "Recovery deploy did not restore the recorded API identity"
  [ "$(sed -n 's/^CONNECTMD_WEB_IMAGE_ID=//p' "$REPO_ROOT/.connectmd-release.env")" = "$backup_web_id" ] || die "Recovery deploy did not restore the recorded web identity"
  [ "$(sed -n 's/^CONNECTMD_NGINX_IMAGE_ID=//p' "$REPO_ROOT/.connectmd-release.env")" = "$backup_nginx_id" ] || die "Recovery deploy did not restore the recorded Nginx identity"
  assert_database_marker before_restore
  assert_storage_marker before_restore
  [ -z "$(compose_test --profile account-lifecycle ps --all -q account-erasure-worker)" ] || die "Disabled lifecycle worker was created during recovery"

  # A legacy v2 generation deliberately has no acceptance authority. Even if
  # the matching v3 marker survives locally, restore/deploy must create a new
  # pending stage rather than relaunching that marker as accepted authority.
  legacy_backup="$backup_dir/connectmd-20990101T000002Z"
  cp -a "$backup_path" "$legacy_backup"
  set_metadata_value "$legacy_backup/metadata.env" format connectmd-backup-v2
  sed -i '/^acceptance_receipt_digest=/d' "$legacy_backup/metadata.env"
  refresh_backup_checksum "$legacy_backup"
  legacy_restore_output="$(bash infra/scripts/restore.sh "$legacy_backup" --yes-restore)"
  printf '%s\n' "$legacy_restore_output" | grep -Fxq 'RESTORE=COMPLETE' || die "Legacy v2 recovery restore did not complete"
  legacy_deploy_output="$(bash infra/scripts/deploy.sh)"
  printf '%s\n' "$legacy_deploy_output" | grep -Fxq "STAGED_IMAGE_TAG=$backup_tag" || die "Legacy v2 recovery was silently accepted"
  if printf '%s\n' "$legacy_deploy_output" | grep -Fq 'RESTORED_ACCEPTED_IMAGE_TAG='; then
    die "Legacy v2 recovery reused a retained accepted marker"
  fi
  [ -f "$REPO_ROOT/.connectmd-staged-release.env" ] && [ ! -L "$REPO_ROOT/.connectmd-staged-release.env" ] || die "Legacy v2 recovery did not retain a staged candidate"
  [ -f "$REPO_ROOT/.connectmd-restore-state.env" ] && [ ! -L "$REPO_ROOT/.connectmd-restore-state.env" ] || die "Legacy v2 recovery cleared its durable restore state before public acceptance"
  grep -Fxq 'backup_format=connectmd-backup-v2' "$REPO_ROOT/.connectmd-restore-state.env" || die "Legacy v2 restore state lost its backup format"
  grep -Fxq 'backup_acceptance_receipt_digest=none' "$REPO_ROOT/.connectmd-restore-state.env" || die "Legacy v2 restore state fabricated acceptance authority"
  printf 'RECOVERY_ROUNDTRIP=PASS\n'
}

if [ "${CONNECTMD_RECOVERY_INNER:-}" = 1 ]; then
  run_inner
else
  run_outer
fi
