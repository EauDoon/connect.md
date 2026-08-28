#!/usr/bin/env bash
set -Eeuo pipefail

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
readonly ENV_FILE="$REPO_ROOT/.env"
readonly RELEASE_ENV_FILE="$REPO_ROOT/.connectmd-release.env"
readonly STAGED_RELEASE_FILE="$REPO_ROOT/.connectmd-staged-release.env"
readonly RESTORE_STATE_FILE="$REPO_ROOT/.connectmd-restore-state.env"
readonly COMPOSE_BASE="$REPO_ROOT/compose.yaml"
readonly COMPOSE_PROD="$REPO_ROOT/compose.prod.yaml"
# Release acceptance must use the deploy host's ordinary resolver and CA
# store. These variables can redirect curl/OpenSSL trust, proxying, DNS, or
# provider loading, so an acceptance run refuses an inherited override.
readonly DIRECT_SYSTEM_TRUST_ENVIRONMENT_VARIABLES=(
  CURL_CA_BUNDLE CURL_HOME CURL_SSL_BACKEND XDG_CONFIG_HOME
  SSL_CERT_FILE SSL_CERT_DIR
  OPENSSL_CONF OPENSSL_CONF_INCLUDE OPENSSL_MODULES OPENSSL_ENGINES
  http_proxy https_proxy all_proxy no_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY
  RES_OPTIONS LOCALDOMAIN HOSTALIASES
  LD_PRELOAD LD_LIBRARY_PATH LD_AUDIT
)
readonly DIRECT_SYSTEM_COMMAND_PATH='/usr/sbin:/usr/bin:/sbin:/bin'

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "Required command is unavailable: $1"
}

assert_direct_system_trust_environment() {
  local variable
  for variable in "${DIRECT_SYSTEM_TRUST_ENVIRONMENT_VARIABLES[@]}"; do
    if [[ -v "$variable" ]]; then
      die "Release acceptance refuses inherited trust, proxy, DNS, or loader override: $variable"
    fi
  done
}

run_with_direct_system_trust() (
  local variable
  # Re-clear this bounded set in the child even after the parent fail-closed
  # guard. Resetting PATH and using `command` bypasses an inherited shell
  # function or a caller-controlled executable named curl/openssl.
  for variable in "${DIRECT_SYSTEM_TRUST_ENVIRONMENT_VARIABLES[@]}"; do
    unset "$variable"
  done
  PATH="$DIRECT_SYSTEM_COMMAND_PATH"
  export PATH
  command "$@"
)

require_file() {
  [ -f "$1" ] || die "Required file is missing: $1"
}

# Release records are authority-bearing local files.  Every reader below uses
# this guard before parsing a record; the exact-key checks in the individual
# validators then reject duplicate and unknown fields as well.
require_secure_record_file() {
  local record="$1" label="$2" record_type record_mode record_links
  require_command stat
  [ -f "$record" ] && [ ! -L "$record" ] || die "$label is missing or unsafe: $record"
  record_type="$(stat -c '%F' -- "$record" 2>/dev/null)" || die "$label cannot be inspected: $record"
  [ "$record_type" = "regular file" ] || die "$label must be a regular file: $record"
  record_mode="$(stat -c '%a' -- "$record" 2>/dev/null)" || die "$label permissions cannot be inspected: $record"
  [ "$record_mode" = "600" ] || die "$label permissions are unsafe: $record"
  record_links="$(stat -c '%h' -- "$record" 2>/dev/null)" || die "$label link count cannot be inspected: $record"
  [ "$record_links" = "1" ] || die "$label must be an immutable single-link record: $record"
}

digest_of_file() {
  local record="$1" digest
  require_command sha256sum
  digest="$(sha256sum -- "$record" | awk '{print $1}')" || die "Could not calculate record digest: $record"
  printf '%s' "$digest" | grep -Eq '^[0-9a-f]{64}$' || die "Record digest is invalid: $record"
  printf '%s' "$digest"
}

require_secure_env_file() {
  local expected_uid actual_uid file_type file_mode
  require_command id
  require_command stat
  require_command uname
  [ -f "$ENV_FILE" ] || die "Required file is missing: $ENV_FILE"
  [ ! -L "$ENV_FILE" ] || die ".env must not be a symlink"
  file_type="$(stat -c '%F' -- "$ENV_FILE" 2>/dev/null)" \
    || die ".env cannot be inspected"
  [ "$file_type" = "regular file" ] || die ".env must be a regular file"
  expected_uid="$(id -u)"
  actual_uid="$(stat -c '%u' -- "$ENV_FILE" 2>/dev/null)" \
    || die ".env ownership cannot be inspected"
  [ "$actual_uid" = "$expected_uid" ] \
    || die ".env must be owned by the effective deploy account"
  if [ "$(uname -s)" = "Linux" ]; then
    file_mode="$(stat -c '%a' -- "$ENV_FILE" 2>/dev/null)" \
      || die ".env permissions cannot be inspected"
    [ "$file_mode" = "600" ] || die ".env permissions must be exactly 600 on Linux"
  fi
}

ensure_repo() {
  require_command docker
  require_file "$COMPOSE_BASE"
  require_file "$COMPOSE_PROD"
  require_secure_env_file
  docker compose version >/dev/null 2>&1 || die "Docker Compose v2 plugin is required"
}

ensure_clean_source() {
  local status
  require_command git
  git -C "$REPO_ROOT" rev-parse --verify HEAD >/dev/null 2>&1 || die "A committed Git revision is required"
  status="$(git -C "$REPO_ROOT" status --porcelain=v1 --untracked-files=normal)"
  [ -z "$status" ] || die "Refusing release: tracked, staged, or untracked source changes are present"
}

acquire_operation_lock() {
  local lock_file inherited_lock lock_identity descriptor_identity
  lock_file="$REPO_ROOT/.connectmd-operations.lock"
  require_command flock
  require_command stat
  [ ! -L "$lock_file" ] || die "Connect.md operation lock path must not be a symlink"

  validate_operation_lock_descriptor() {
    [ ! -L "$lock_file" ] || die "Connect.md operation lock path must not be a symlink"
    lock_identity="$(stat -Lc '%F:%h:%d:%i' -- "$lock_file" 2>/dev/null)" \
      || die "Connect.md operation lock path cannot be inspected"
    [ ! -L "$lock_file" ] || die "Connect.md operation lock path must not be a symlink"
    descriptor_identity="$(stat -Lc '%F:%h:%d:%i' -- "/proc/$$/fd/9" 2>/dev/null)" \
      || die "Connect.md operation lock descriptor cannot be inspected"
    case "$lock_identity" in regular\ file:1:*:*) ;; *) die "Connect.md operation lock must be a regular single-link file" ;; esac
    case "$descriptor_identity" in regular\ file:1:*:*) ;; *) die "Connect.md operation lock descriptor must be a regular single-link file" ;; esac
    [ "$descriptor_identity" = "$lock_identity" ] \
      || die "Connect.md operation lock descriptor no longer matches the lock path"
  }

  if [ "${CONNECTMD_OPERATION_LOCK_HELD:-}" = "1" ] && [ -e "/proc/$$/fd/9" ]; then
    require_command readlink
    inherited_lock="$(readlink -f "/proc/$$/fd/9" 2>/dev/null || true)"
    if [ "$inherited_lock" = "$lock_file" ]; then
      flock -n 9 || die "Inherited connect.md operation lock is not held"
      validate_operation_lock_descriptor
      return
    fi
  fi
  exec 9>>"$lock_file"
  flock -n 9 || die "Another connect.md operational workflow is already running"
  validate_operation_lock_descriptor
  export CONNECTMD_OPERATION_LOCK_HELD=1
}

compose() {
  local env_args=(--env-file "$ENV_FILE") project_args=() project_name
  # Keep sourceable helpers fail-closed even if a future operational script
  # accidentally calls compose() without first calling ensure_repo().
  require_secure_env_file
  assert_env_file_matches_process_environment
  project_name="${CONNECTMD_COMPOSE_PROJECT_NAME:-}"
  if [ -n "$project_name" ]; then
    printf '%s' "$project_name" | grep -Eq '^[a-z0-9][a-z0-9_-]{0,62}$' || die "CONNECTMD_COMPOSE_PROJECT_NAME must be lowercase and valid for Docker Compose"
    project_args=(--project-name "$project_name")
  fi
  if [ -f "$RELEASE_ENV_FILE" ]; then
    env_args+=(--env-file "$RELEASE_ENV_FILE")
  fi
  # TLS and health are intentionally allowed while a candidate is staged. If
  # they do not explicitly select an image, keep their Compose recreation on
  # the staged Nginx/API/Web tuple rather than silently falling back to the
  # prior accepted marker. Explicit callers (notably staged rollback) retain
  # their selected CONNECTMD_IMAGE_TAG.
  if [ -z "${CONNECTMD_IMAGE_TAG:-}" ] && { [ -e "$STAGED_RELEASE_FILE" ] || [ -L "$STAGED_RELEASE_FILE" ]; }; then
    load_staged_release >/dev/null
    export CONNECTMD_IMAGE_TAG="$STAGED_IMAGE_TAG"
  fi
  docker compose "${project_args[@]}" "${env_args[@]}" -f "$COMPOSE_BASE" -f "$COMPOSE_PROD" "$@"
}

assert_env_file_matches_process_environment() {
  local line key raw_value inherited_value
  local -a env_lines=()
  local -A seen_keys=()
  require_secure_env_file
  mapfile -t env_lines < "$ENV_FILE" || die ".env could not be read"

  # Parse the complete file before checking inherited values so malformed or
  # duplicate entries cannot be hidden behind a process-environment mismatch.
  for line in "${env_lines[@]}"; do
    case "$line" in
      "" | \#*) continue ;;
    esac
    case "$line" in
      *$'\r'*) die ".env contains a malformed entry" ;;
    esac
    [[ "$line" == *=* ]] || die ".env contains a malformed entry"
    key="${line%%=*}"
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || die ".env contains an invalid variable name"
    [[ ! -v "seen_keys[$key]" ]] || die ".env contains a duplicate variable name"
    seen_keys["$key"]=1
  done

  for line in "${env_lines[@]}"; do
    case "$line" in
      "" | \#*) continue ;;
    esac
    key="${line%%=*}"
    raw_value="${line#*=}"
    if [[ -v "$key" ]] && declare -xp "$key" >/dev/null 2>&1; then
      inherited_value="${!key}"
      [ "$inherited_value" = "$raw_value" ] || die "$key environment override must match .env"
    fi
  done
}

read_env_value() {
  local key="$1"
  local lines value
  lines="$(grep -E "^${key}=" "$ENV_FILE" || true)"
  [ -n "$lines" ] || return 1
  [ "$(printf '%s\n' "$lines" | wc -l | tr -d ' ')" = "1" ] || return 1
  value="${lines#*=}"
  [ -n "$value" ] || return 1
  printf '%s' "$value"
}

read_env_optional_value() {
  local key="$1"
  local lines value count
  lines="$(grep -E "^${key}=" "$ENV_FILE" || true)"
  [ -n "$lines" ] || return 0
  count="$(printf '%s\n' "$lines" | wc -l | tr -d ' ')"
  [ "$count" = "1" ] || return 1
  value="${lines#*=}"
  printf '%s' "$value"
}

normalize_recruiting_enabled() {
  local value
  value="$(read_env_optional_value CONNECTMD_RECRUITING_ENABLED)" \
    || die "CONNECTMD_RECRUITING_ENABLED must appear at most once in .env"
  value="${value:-false}"
  case "$value" in
    true | false) ;;
    *) die "CONNECTMD_RECRUITING_ENABLED must be true or false" ;;
  esac
  if [[ -v CONNECTMD_RECRUITING_ENABLED && "$CONNECTMD_RECRUITING_ENABLED" != "$value" ]]; then
    die "CONNECTMD_RECRUITING_ENABLED environment override must match .env"
  fi
  printf '%s' "$value"
}

require_secret_value() {
  local key="$1"
  local value
  value="$(read_env_value "$key")" || die "$key must be set exactly once in .env"
  case "$value" in
    change-this* | example | example.* | *example.com* | https://example.clerk.accounts.dev | https://example.clerk.accounts.dev/.well-known/jwks.json | replace-me* | pk_test_replace* | pk_live_replace* | sk_test_replace* | sk_live_replace*)
      die "$key still has an example value"
      ;;
  esac
  printf '%s' "$value"
}

validate_clerk_secret_key() {
  local value="$1"
  printf '%s' "$value" | grep -Eq '^sk_(test|live)_[A-Za-z0-9_-]{16,}$' \
    || die "CLERK_SECRET_KEY must be a well-formed Clerk secret key"
}

validate_clerk_authorized_site_origin() {
  local authorized_parties="$1"
  local site_origin="$2"
  require_command python3
  if ! printf '%s' "$authorized_parties" | python3 -c '
import json
import sys

site_origin = sys.argv[1]
try:
    parties = json.load(sys.stdin)
except (json.JSONDecodeError, UnicodeDecodeError):
    raise SystemExit(1)
if (
    not isinstance(parties, list)
    or any(not isinstance(party, str) for party in parties)
    or site_origin not in parties
):
    raise SystemExit(1)
' "$site_origin"; then
    die "CONNECTMD_CLERK_AUTHORIZED_PARTIES must include the canonical NEXT_PUBLIC_SITE_URL origin"
  fi
}

validate_public_api_base() {
  local api_base="$1"
  local canonical_origin="$2"
  case "$api_base" in
    "" | "$canonical_origin") ;;
    *) die "NEXT_PUBLIC_API_BASE_URL must be empty or exactly the canonical CONNECTMD_PUBLIC_BASE_URL HTTPS origin" ;;
  esac
}

validate_public_api_base_environment_override() {
  local configured_base="$1"
  if [[ -v NEXT_PUBLIC_API_BASE_URL && "$NEXT_PUBLIC_API_BASE_URL" != "$configured_base" ]]; then
    die "NEXT_PUBLIC_API_BASE_URL environment override must match .env"
  fi
}

validate_clerk_publishable_key() {
  require_command python3
  if ! printf '%s' "$1" | python3 -c '
import base64
import binascii
import re
import sys

try:
    key = sys.stdin.read()
    if re.fullmatch(
        r"pk_(?:test|live)_(?:[A-Za-z0-9+/]{4})*"
        r"(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?",
        key,
    ) is None:
        raise ValueError
    encoded = key.rsplit("_", 1)[1]
    decoded = base64.b64decode(encoded.encode("ascii"), validate=True).decode("ascii")
    if not decoded.endswith("$"):
        raise ValueError
    host = decoded[:-1]
    if "$" in host or len(host) > 253:
        raise ValueError
    if re.fullmatch(
        r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
        r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+",
        host,
    ) is None:
        raise ValueError
except (IndexError, UnicodeEncodeError, UnicodeDecodeError, ValueError, binascii.Error):
    raise SystemExit(1)
'; then
    die "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY must be a well-formed Clerk publishable key"
  fi
}

is_lowercase_dns_hostname() (
  hostname="${1:-}"
  [ -n "$hostname" ] && [ "${#hostname}" -le 253 ] || return 1
  case "$hostname" in
    *[!a-z0-9.-]* | .* | *. | *..*) return 1 ;;
  esac

  remainder="$hostname"
  while [ -n "$remainder" ]; do
    case "$remainder" in
      *.*)
        label="${remainder%%.*}"
        remainder="${remainder#*.}"
        ;;
      *)
        label="$remainder"
        remainder=""
        ;;
    esac
    [ "${#label}" -le 63 ] || return 1
    case "$label" in "" | -* | *-) return 1 ;; esac
  done
)

require_hostname() {
  local domain
  domain="$(require_secret_value CONNECTMD_DOMAIN)"
  is_lowercase_dns_hostname "$domain" \
    || die "CONNECTMD_DOMAIN must be a valid lowercase DNS hostname"
  printf '%s' "$domain"
}

validate_exact_search_cursor_authority() {
  local keyring ttl
  require_command python3
  keyring="$(require_secret_value CONNECTMD_EXACT_SEARCH_CURSOR_KEYRING)"
  ttl="$(read_env_optional_value CONNECTMD_EXACT_SEARCH_CURSOR_TTL_SECONDS)" \
    || die "CONNECTMD_EXACT_SEARCH_CURSOR_TTL_SECONDS must appear at most once in .env"
  ttl="${ttl:-900}"
  case "$ttl" in "" | *[!0-9]*) die "CONNECTMD_EXACT_SEARCH_CURSOR_TTL_SECONDS must be an integer" ;; esac
  [ "$ttl" -ge 60 ] && [ "$ttl" -le 3600 ] \
    || die "CONNECTMD_EXACT_SEARCH_CURSOR_TTL_SECONDS must be between 60 and 3600"
  if ! printf '%s' "$keyring" | python3 -c '
import base64
import binascii
import json
import re
import sys

try:
    parsed = json.load(sys.stdin)
    values = parsed if isinstance(parsed, list) else [parsed]
    assert 1 <= len(values) <= 3
    seen = set()
    for value in values:
        assert isinstance(value, dict) and set(value) == {"kid", "secret"}
        kid = value["kid"]
        secret = value["secret"]
        assert isinstance(kid, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,31}", kid)
        assert kid not in seen
        assert isinstance(secret, str)
        assert re.fullmatch(r"[A-Za-z0-9_-]+", secret)
        padded = secret + "=" * (-len(secret) % 4)
        decoded = base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)
        assert len(decoded) >= 32
        seen.add(kid)
except (AssertionError, UnicodeEncodeError, ValueError, TypeError, json.JSONDecodeError, binascii.Error):
    raise SystemExit(1)
'; then
    die "CONNECTMD_EXACT_SEARCH_CURSOR_KEYRING must be strict JSON containing one to three unique kid/secret objects with unpadded URL-safe Base64 secrets of at least 32 decoded bytes"
  fi
}

validate_production_env() {
  local postgres_user postgres_password migrator_db_password api_db_password projection_db_password projection_admin_db_password erasure_db_password backup_db_password meili_key meili_search_key projection_meili_key erasure_meili_key clerk_jwks clerk_issuer clerk_audience clerk_parties api_key_pepper clerk_publishable_key clerk_secret_key domain public_base site_url api_base verification_reviewer_id verification_reviewer_role post_moderator_id post_moderator_role appeal_reviewer_id appeal_reviewer_role api_readiness_path recruiting_enabled lifecycle_enabled lifecycle_frontend lifecycle_hmac lifecycle_aead witness_hmac witness_dir witness_path backup_path clerk_backend_secret clerk_backend_base_url database_secret other_database_secret
  assert_env_file_matches_process_environment
  postgres_user="$(read_env_value POSTGRES_USER)" || die "POSTGRES_USER must be set exactly once in .env"
  [ "$postgres_user" = postgres ] || die "POSTGRES_USER must remain the operator-only postgres bootstrap identity"
  postgres_password="$(require_secret_value POSTGRES_PASSWORD)"
  migrator_db_password="$(require_secret_value CONNECTMD_MIGRATOR_DB_PASSWORD)"
  api_db_password="$(require_secret_value CONNECTMD_API_DB_PASSWORD)"
  meili_key="$(require_secret_value MEILI_MASTER_KEY)"
  meili_search_key="$(require_secret_value CONNECTMD_MEILISEARCH_SEARCH_KEY)"
  projection_meili_key="$(require_secret_value CONNECTMD_SEARCH_PROJECTION_MEILI_KEY)"
  projection_db_password="$(require_secret_value CONNECTMD_SEARCH_PROJECTION_DB_PASSWORD)"
  projection_admin_db_password="$(require_secret_value CONNECTMD_PROJECTION_ADMIN_DB_PASSWORD)"
  erasure_db_password="$(require_secret_value CONNECTMD_ACCOUNT_ERASURE_DB_PASSWORD)"
  backup_db_password="$(require_secret_value CONNECTMD_BACKUP_DB_PASSWORD)"
  clerk_jwks="$(require_secret_value CONNECTMD_CLERK_JWKS_URL)"
  clerk_issuer="$(require_secret_value CONNECTMD_CLERK_ISSUER)"
  clerk_audience="$(read_env_optional_value CONNECTMD_CLERK_AUDIENCE)" || die "CONNECTMD_CLERK_AUDIENCE must appear at most once in .env"
  clerk_parties="$(require_secret_value CONNECTMD_CLERK_AUTHORIZED_PARTIES)"
  api_key_pepper="$(require_secret_value CONNECTMD_API_KEY_PEPPER)"
  clerk_publishable_key="$(require_secret_value NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY)"
  clerk_secret_key="$(require_secret_value CLERK_SECRET_KEY)"
  domain="$(require_hostname)"
  public_base="$(require_secret_value CONNECTMD_PUBLIC_BASE_URL)"
  site_url="$(require_secret_value NEXT_PUBLIC_SITE_URL)"
  verification_reviewer_id="$(require_secret_value CONNECTMD_VERIFICATION_REVIEWER_ID)"
  verification_reviewer_role="$(read_env_value CONNECTMD_VERIFICATION_REVIEWER_ROLE)" || die "CONNECTMD_VERIFICATION_REVIEWER_ROLE must be set exactly once in .env"
  post_moderator_id="$(require_secret_value CONNECTMD_POST_MODERATOR_ID)"
  post_moderator_role="$(read_env_value CONNECTMD_POST_MODERATOR_ROLE)" || die "CONNECTMD_POST_MODERATOR_ROLE must be set exactly once in .env"
  appeal_reviewer_id="$(require_secret_value CONNECTMD_APPEAL_REVIEWER_ID)"
  appeal_reviewer_role="$(read_env_value CONNECTMD_APPEAL_REVIEWER_ROLE)" || die "CONNECTMD_APPEAL_REVIEWER_ROLE must be set exactly once in .env"
  for database_secret in "$postgres_password" "$migrator_db_password" "$api_db_password" "$projection_db_password" "$projection_admin_db_password" "$erasure_db_password" "$backup_db_password"; do
    [ "${#database_secret}" -ge 32 ] || die "Every PostgreSQL password must be at least 32 characters"
    case "$database_secret" in *[!A-Fa-f0-9]*) die "Every PostgreSQL password must be hexadecimal" ;; esac
    for other_database_secret in "$postgres_password" "$migrator_db_password" "$api_db_password" "$projection_db_password" "$projection_admin_db_password" "$erasure_db_password" "$backup_db_password"; do
      if [ "$database_secret" = "$other_database_secret" ]; then
        [ "$(printf '%s\n' "$postgres_password" "$migrator_db_password" "$api_db_password" "$projection_db_password" "$projection_admin_db_password" "$erasure_db_password" "$backup_db_password" | grep -Fxc "$database_secret")" -eq 1 ] \
          || die "Every PostgreSQL role password must be independent"
      fi
    done
  done
  [ "${#meili_key}" -ge 16 ] || die "MEILI_MASTER_KEY must be at least 16 characters"
  [ "${#meili_search_key}" -ge 16 ] || die "CONNECTMD_MEILISEARCH_SEARCH_KEY must be at least 16 characters"
  [ "${#projection_meili_key}" -ge 16 ] || die "CONNECTMD_SEARCH_PROJECTION_MEILI_KEY must be at least 16 characters"
  [ "$meili_search_key" != "$meili_key" ] || die "API search key must differ from MEILI_MASTER_KEY"
  [ "$projection_meili_key" != "$meili_key" ] || die "Projection key must differ from MEILI_MASTER_KEY"
  [ "$meili_search_key" != "$projection_meili_key" ] || die "API search and projection keys must be distinct"
  case "$clerk_jwks" in https://*) ;; *) die "CLERK_JWKS_URL must use HTTPS" ;; esac
  case "$clerk_issuer" in https://*) ;; *) die "CONNECTMD_CLERK_ISSUER must use HTTPS" ;; esac
  case "$clerk_audience" in *[!A-Za-z0-9:./_-]*) die "CONNECTMD_CLERK_AUDIENCE contains invalid characters" ;; esac
  case "$clerk_parties" in \[*\]) ;; *) die "CONNECTMD_CLERK_AUTHORIZED_PARTIES must be a JSON list" ;; esac
  [ "${#api_key_pepper}" -ge 32 ] || die "CONNECTMD_API_KEY_PEPPER must be at least 32 characters"
  validate_clerk_publishable_key "$clerk_publishable_key"
  validate_clerk_secret_key "$clerk_secret_key"
  case "$public_base" in "https://$domain" | "https://$domain/") ;; *) die "CONNECTMD_PUBLIC_BASE_URL must be the canonical CONNECTMD_DOMAIN HTTPS origin" ;; esac
  case "$site_url" in "https://$domain" | "https://$domain/") ;; *) die "NEXT_PUBLIC_SITE_URL must be the canonical CONNECTMD_DOMAIN HTTPS origin" ;; esac
  api_base="$(read_env_optional_value NEXT_PUBLIC_API_BASE_URL)" || die "NEXT_PUBLIC_API_BASE_URL must appear at most once in .env"
  validate_public_api_base_environment_override "$api_base"
  validate_public_api_base "$api_base" "https://$domain"
  validate_clerk_authorized_site_origin "$clerk_parties" "https://$domain"
  [ "$verification_reviewer_role" = "recruiting_verifier" ] || die "CONNECTMD_VERIFICATION_REVIEWER_ROLE must be recruiting_verifier"
  [ "$post_moderator_role" = "content_moderator" ] || die "CONNECTMD_POST_MODERATOR_ROLE must be content_moderator"
  [ "$appeal_reviewer_role" = "appeal_reviewer" ] || die "CONNECTMD_APPEAL_REVIEWER_ROLE must be appeal_reviewer"
  [ "$appeal_reviewer_id" != "$post_moderator_id" ] || die "CONNECTMD_APPEAL_REVIEWER_ID must differ from CONNECTMD_POST_MODERATOR_ID"
  validate_exact_search_cursor_authority

  api_readiness_path="$(read_env_optional_value API_READINESS_PATH)" || die "API_READINESS_PATH must appear at most once in .env"
  api_readiness_path="${api_readiness_path:-/readyz}"
  case "$api_readiness_path" in /readyz) ;; *) die "API_READINESS_PATH must remain /readyz" ;; esac
  recruiting_enabled="$(normalize_recruiting_enabled)"

  lifecycle_enabled="$(read_env_optional_value CONNECTMD_ACCOUNT_LIFECYCLE_ENABLED)" || die "CONNECTMD_ACCOUNT_LIFECYCLE_ENABLED must appear at most once in .env"
  lifecycle_frontend="$(read_env_optional_value NEXT_PUBLIC_ACCOUNT_LIFECYCLE_ENABLED)" || die "NEXT_PUBLIC_ACCOUNT_LIFECYCLE_ENABLED must appear at most once in .env"
  lifecycle_enabled="${lifecycle_enabled:-false}"
  lifecycle_frontend="${lifecycle_frontend:-false}"
  case "$lifecycle_enabled" in true | false) ;; *) die "CONNECTMD_ACCOUNT_LIFECYCLE_ENABLED must be true or false" ;; esac
  case "$lifecycle_frontend" in true | false) ;; *) die "NEXT_PUBLIC_ACCOUNT_LIFECYCLE_ENABLED must be true or false" ;; esac
  [ "$lifecycle_enabled" = "$lifecycle_frontend" ] || die "Account lifecycle API and frontend flags must match"
  lifecycle_hmac="$(require_secret_value CONNECTMD_LIFECYCLE_HMAC_KEY)"
  lifecycle_aead="$(require_secret_value CONNECTMD_LIFECYCLE_AEAD_KEY)"
  witness_hmac="$(require_secret_value CONNECTMD_DELETION_WITNESS_HMAC_KEY)"
  witness_dir="$(read_env_value CONNECTMD_DELETION_WITNESS_DIR)" || die "CONNECTMD_DELETION_WITNESS_DIR must be set exactly once in .env"
  [ "${#lifecycle_hmac}" -ge 32 ] || die "CONNECTMD_LIFECYCLE_HMAC_KEY must be at least 32 characters"
  [ "${#lifecycle_aead}" -ge 32 ] || die "CONNECTMD_LIFECYCLE_AEAD_KEY must be at least 32 characters"
  [ "${#witness_hmac}" -ge 32 ] || die "CONNECTMD_DELETION_WITNESS_HMAC_KEY must be at least 32 characters"
  [ "$lifecycle_hmac" != "$lifecycle_aead" ] || die "Lifecycle HMAC and AEAD keys must be distinct"
  [ "$lifecycle_hmac" != "$witness_hmac" ] || die "Lifecycle HMAC and deletion witness HMAC keys must be distinct"
  [ "$lifecycle_aead" != "$witness_hmac" ] || die "Lifecycle AEAD and deletion witness HMAC keys must be distinct"
  case "$witness_dir" in /*) ;; *) die "CONNECTMD_DELETION_WITNESS_DIR must be an absolute path" ;; esac
  witness_path="$(realpath -m "$witness_dir")"
  [ "$witness_path" = "$witness_dir" ] || die "CONNECTMD_DELETION_WITNESS_DIR must be canonical"
  [ "$witness_path" != / ] || die "CONNECTMD_DELETION_WITNESS_DIR must not be the filesystem root"
  backup_path="$(backup_root)"
  case "$witness_path" in "$backup_path" | "$backup_path"/*) die "Deletion witness authority must be outside CONNECTMD_BACKUP_DIR" ;; esac
  case "$backup_path" in "$witness_path" | "$witness_path"/*) die "Deletion witness authority must not contain CONNECTMD_BACKUP_DIR" ;; esac
  if [ "$lifecycle_enabled" = true ]; then
    erasure_meili_key="$(require_secret_value CONNECTMD_ACCOUNT_ERASURE_MEILI_KEY)"
    clerk_backend_secret="$(require_secret_value CONNECTMD_CLERK_BACKEND_SECRET)"
    clerk_backend_base_url="$(require_secret_value CONNECTMD_CLERK_BACKEND_BASE_URL)"
    [ "${#clerk_backend_secret}" -ge 32 ] || die "CONNECTMD_CLERK_BACKEND_SECRET must be at least 32 characters"
    [ "${#erasure_meili_key}" -ge 16 ] || die "CONNECTMD_ACCOUNT_ERASURE_MEILI_KEY must be at least 16 characters"
    [ "$erasure_meili_key" != "$meili_key" ] || die "Erasure key must differ from MEILI_MASTER_KEY"
    [ "$erasure_meili_key" != "$meili_search_key" ] && [ "$erasure_meili_key" != "$projection_meili_key" ] || die "Erasure key must be distinct from other runtime keys"
    case "$clerk_backend_base_url" in https://*) ;; *) die "CONNECTMD_CLERK_BACKEND_BASE_URL must use HTTPS" ;; esac
  fi
  if [ -f "$RELEASE_ENV_FILE" ]; then
    assert_lifecycle_authority_unchanged
    assert_recruiting_authority_unchanged
    assert_api_key_pepper_unchanged
  fi
}

assert_exact_search_image_contract() {
  local image_tag="$1"
  docker run --rm --network none --entrypoint python "connectmd-api:$image_tag" -c "import hashlib,sys; from pathlib import Path; from app.cli import parse_args; from app.services.exact_search import EXACT_SEARCH_CONTRACT_DIGEST; assert Path('/app/alembic/versions/0025_exact_public_search.py').is_file(); assert EXACT_SEARCH_CONTRACT_DIGEST == hashlib.sha256(b'connect.md:exact-public-search:v1').hexdigest(); sys.argv=['python -m app.cli','exact-search','verify']; args=parse_args(); assert args.command == 'exact-search' and args.exact_search_action == 'verify'; sys.argv=['python -m app.cli','exact-search','backfill','--if-required']; args=parse_args(); assert args.command == 'exact-search' and args.exact_search_action == 'backfill' and args.if_required is True"
}

wait_for_service() {
  local service="$1"
  local attempts="${2:-60}"
  local container status
  container="$(compose ps -q "$service")"
  [ -n "$container" ] || die "No container found for $service"

  while [ "$attempts" -gt 0 ]; do
    status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container" 2>/dev/null || true)"
    case "$status" in
      healthy | running)
        return 0
        ;;
      unhealthy | exited | dead)
        die "$service entered state: $status"
        ;;
    esac
    sleep 2
    attempts=$((attempts - 1))
  done
  die "Timed out waiting for $service to become healthy"
}

service_is_running() {
  local container status
  container="$(compose ps -q "$1")"
  [ -n "$container" ] || return 1
  status="$(docker inspect --format '{{.State.Status}}' "$container" 2>/dev/null || true)"
  [ "$status" = "running" ]
}

service_is_active() {
  local container status
  container="$(compose ps --all -q "$1")"
  [ -n "$container" ] || return 1
  status="$(docker inspect --format '{{.State.Status}}' "$container" 2>/dev/null || true)"
  case "$status" in running | restarting | paused) return 0 ;; *) return 1 ;; esac
}

service_state() {
  local container
  container="$(compose ps --all -q "$1")"
  [ -n "$container" ] || return 0
  docker inspect --format '{{.State.Status}}' "$container" 2>/dev/null || true
}

service_image() {
  local container
  container="$(compose ps --all -q "$1")"
  [ -n "$container" ] || return 1
  docker inspect --format '{{.Config.Image}}' "$container" 2>/dev/null
}

profiled_service_is_running() {
  local profile="$1" service="$2" container status
  container="$(compose --profile "$profile" ps -q "$service")"
  [ -n "$container" ] || return 1
  status="$(docker inspect --format '{{.State.Status}}' "$container" 2>/dev/null || true)"
  [ "$status" = "running" ]
}

profiled_service_is_active() {
  local profile="$1" service="$2" container status
  container="$(compose --profile "$profile" ps --all -q "$service")"
  [ -n "$container" ] || return 1
  status="$(docker inspect --format '{{.State.Status}}' "$container" 2>/dev/null || true)"
  case "$status" in running | restarting | paused) return 0 ;; *) return 1 ;; esac
}

profiled_service_state() {
  local profile="$1" service="$2" container
  container="$(compose --profile "$profile" ps --all -q "$service")"
  [ -n "$container" ] || return 0
  docker inspect --format '{{.State.Status}}' "$container" 2>/dev/null || true
}

profiled_service_image() {
  local profile="$1" service="$2" container
  container="$(compose --profile "$profile" ps --all -q "$service")"
  [ -n "$container" ] || return 1
  docker inspect --format '{{.Config.Image}}' "$container" 2>/dev/null
}

wait_for_profiled_service() {
  local profile="$1" service="$2" attempts="${3:-20}" stable_checks=0 container status
  while [ "$attempts" -gt 0 ]; do
    container="$(compose --profile "$profile" ps -q "$service")"
    status=""
    if [ -n "$container" ]; then
      status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container" 2>/dev/null || true)"
    fi
    case "$status" in
      healthy | running)
        stable_checks=$((stable_checks + 1))
        [ "$stable_checks" -ge 3 ] && return 0
        ;;
      unhealthy | exited | dead)
        die "$service entered state: $status"
        ;;
      *)
        stable_checks=0
        ;;
    esac
    sleep 1
    attempts=$((attempts - 1))
  done
  die "Timed out waiting for $service to remain running"
}

current_image_tag() {
  local revision api_base publishable site_url lifecycle_enabled fingerprint
  require_command git
  require_command sha256sum
  revision="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null)" || die "A committed Git revision is required for deployment image tagging"
  api_base="$(grep -E '^NEXT_PUBLIC_API_BASE_URL=' "$ENV_FILE" | cut -d= -f2- || true)"
  publishable="$(grep -E '^NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=' "$ENV_FILE" | cut -d= -f2- || true)"
  site_url="$(grep -E '^NEXT_PUBLIC_SITE_URL=' "$ENV_FILE" | cut -d= -f2- || true)"
  lifecycle_enabled="$(grep -E '^NEXT_PUBLIC_ACCOUNT_LIFECYCLE_ENABLED=' "$ENV_FILE" | cut -d= -f2- || true)"
  fingerprint="$(printf 'api=%s\nclerk=%s\nsite=%s\nlifecycle=%s\n' "$api_base" "$publishable" "$site_url" "$lifecycle_enabled" | sha256sum | cut -c1-16)"
  printf '%s-%s' "$revision" "$fingerprint"
}

is_full_source_revision() {
  printf '%s' "$1" | grep -Eq '^[0-9a-f]{40}([0-9a-f]{24})?$'
}

current_source_revision() {
  local revision
  require_command git
  revision="$(git -C "$REPO_ROOT" rev-parse --verify HEAD 2>/dev/null)" \
    || die "A committed Git revision is required for release identity"
  is_full_source_revision "$revision" \
    || die "Git did not return a canonical full source revision"
  printf '%s' "$revision"
}

is_image_identity() {
  printf '%s' "$1" | grep -Eq '^sha256:[0-9a-f]{64}$'
}

image_identity_for_tag() {
  local image="$1" image_tag="$2" identity
  identity="$(docker image inspect --format '{{.Id}}' "${image}:${image_tag}" 2>/dev/null)" \
    || die "Required release image is unavailable: ${image}:${image_tag}"
  is_image_identity "$identity" \
    || die "Release image identity is malformed: ${image}:${image_tag}"
  printf '%s' "$identity"
}

assert_image_identity() {
  local image="$1" image_tag="$2" expected_identity="$3" actual_identity
  is_image_identity "$expected_identity" \
    || die "Recorded release image identity is malformed: ${image}:${image_tag}"
  docker image inspect "$expected_identity" >/dev/null 2>&1 \
    || die "Recorded release image identity is unavailable locally: $expected_identity"
  actual_identity="$(image_identity_for_tag "$image" "$image_tag")"
  [ "$actual_identity" = "$expected_identity" ] \
    || die "Release image tag does not match its recorded identity: ${image}:${image_tag}"
}

assert_release_images_match() {
  local image_tag="$1" api_identity="$2" web_identity="$3" nginx_identity="$4"
  case "$image_tag" in
    "" | *[!A-Za-z0-9_.-]*) die "Release image tag is invalid" ;;
  esac
  assert_image_identity connectmd-api "$image_tag" "$api_identity"
  assert_image_identity connectmd-web "$image_tag" "$web_identity"
  assert_image_identity connectmd-nginx "$image_tag" "$nginx_identity"
}

service_image_identity() {
  local container identity
  container="$(compose ps --all -q "$1")"
  [ -n "$container" ] || return 1
  identity="$(docker inspect --format '{{.Image}}' "$container" 2>/dev/null)" \
    || return 1
  is_image_identity "$identity" || return 1
  printf '%s' "$identity"
}

profiled_service_image_identity() {
  local profile="$1" service="$2" container identity
  container="$(compose --profile "$profile" ps --all -q "$service")"
  [ -n "$container" ] || return 1
  identity="$(docker inspect --format '{{.Image}}' "$container" 2>/dev/null)" \
    || return 1
  is_image_identity "$identity" || return 1
  printf '%s' "$identity"
}

assert_service_image_identity() {
  local service="$1" expected_identity="$2" actual_identity
  is_image_identity "$expected_identity" \
    || die "Recorded service image identity is malformed: $service"
  actual_identity="$(service_image_identity "$service")" \
    || die "Running service image identity is unavailable: $service"
  [ "$actual_identity" = "$expected_identity" ] \
    || die "Running service image identity does not match the release receipt: $service"
}

release_receipt_root() {
  printf '%s/.connectmd-lifecycle/releases' "$(backup_root)"
}

release_receipt_path() {
  local image_tag="$1" root
  case "$image_tag" in
    "" | *[!A-Za-z0-9_.-]*) die "Release image tag is invalid" ;;
  esac
  root="$(release_receipt_root)"
  printf '%s/release-%s.env' "$root" "$image_tag"
}

record_value() {
  local record="$1" key="$2" lines value
  lines="$(grep -E "^${key}=" "$record" || true)"
  [ -n "$lines" ] || die "Release record is missing $key"
  [ "$(printf '%s\n' "$lines" | wc -l | tr -d ' ')" = "1" ] \
    || die "Release record has multiple $key values"
  value="${lines#*=}"
  [ -n "$value" ] || die "Release record has an empty $key"
  printf '%s' "$value"
}

validate_release_receipt() {
  local receipt="$1" expected_source="$2" expected_tag="$3" expected_api="$4" expected_web="$5" expected_nginx="$6"
  local expected_recruiting actual_keys source_revision image_tag api_identity web_identity nginx_identity recruiting_enabled
  if [ "$#" -ge 7 ] && [ -n "${7:-}" ]; then
    expected_recruiting="$7"
  else
    expected_recruiting="$(normalize_recruiting_enabled)"
  fi
  require_secure_record_file "$receipt" "Release receipt"
  expected_keys="$(printf '%s\n' api_image_id format image_tag nginx_image_id recorded_at recruiting_enabled source_revision web_image_id | LC_ALL=C sort)"
  actual_keys="$(cut -d= -f1 "$receipt" | LC_ALL=C sort)"
  [ "$actual_keys" = "$expected_keys" ] || die "Release receipt contains unsupported fields: $receipt"
  [ "$(record_value "$receipt" format)" = "connectmd-release-receipt-v1" ] \
    || die "Release receipt format is unsupported: $receipt"
  recruiting_enabled="$(record_value "$receipt" recruiting_enabled)"
  case "$recruiting_enabled" in true | false) ;; *) die "Release receipt recruiting state is invalid: $receipt" ;; esac
  [ "$recruiting_enabled" = "$expected_recruiting" ] || die "Release receipt recruiting state does not match"
  source_revision="$(record_value "$receipt" source_revision)"
  image_tag="$(record_value "$receipt" image_tag)"
  api_identity="$(record_value "$receipt" api_image_id)"
  web_identity="$(record_value "$receipt" web_image_id)"
  nginx_identity="$(record_value "$receipt" nginx_image_id)"
  is_full_source_revision "$source_revision" \
    || die "Release receipt source revision is invalid: $receipt"
  is_image_identity "$api_identity" && is_image_identity "$web_identity" && is_image_identity "$nginx_identity" \
    || die "Release receipt image identity is invalid: $receipt"
  grep -Eq '^recorded_at=[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$' "$receipt" \
    || die "Release receipt timestamp is invalid: $receipt"
  [ "$source_revision" = "$expected_source" ] || die "Release receipt source revision does not match"
  [ "$image_tag" = "$expected_tag" ] || die "Release receipt image tag does not match"
  [ "$api_identity" = "$expected_api" ] || die "Release receipt API image identity does not match"
  [ "$web_identity" = "$expected_web" ] || die "Release receipt web image identity does not match"
  [ "$nginx_identity" = "$expected_nginx" ] || die "Release receipt Nginx image identity does not match"
}

write_release_receipt() {
  local source_revision="$1" image_tag="$2" api_identity="$3" web_identity="$4" nginx_identity="$5"
  local root receipt temporary
  local recruiting_enabled
  is_full_source_revision "$source_revision" || die "Release source revision is invalid"
  assert_release_images_match "$image_tag" "$api_identity" "$web_identity" "$nginx_identity"
  recruiting_enabled="$(normalize_recruiting_enabled)"
  root="$(release_receipt_root)"
  [ ! -L "$root" ] || die "Release receipt directory must not be a symlink"
  mkdir -p -- "$root"
  [ -d "$root" ] && [ ! -L "$root" ] || die "Release receipt directory is unsafe"
  [ "$(realpath -e "$root")" = "$root" ] || die "Release receipt directory is not canonical"
  chmod 700 "$root"
  receipt="$(release_receipt_path "$image_tag")"
  if [ -e "$receipt" ] || [ -L "$receipt" ]; then
    validate_release_receipt "$receipt" "$source_revision" "$image_tag" "$api_identity" "$web_identity" "$nginx_identity" "$recruiting_enabled"
    printf '%s' "$receipt"
    return
  fi
  [ "${CONNECTMD_RELEASE_IMAGES_BUILT_FOR_TAG:-}" = "$image_tag" ] \
    || die "Refusing to create a release receipt from pre-existing images without a verified build"
  temporary="$(mktemp "$root/.release.XXXXXX")"
  chmod 600 "$temporary"
  {
    printf 'format=connectmd-release-receipt-v1\n'
    printf 'source_revision=%s\n' "$source_revision"
    printf 'image_tag=%s\n' "$image_tag"
    printf 'api_image_id=%s\n' "$api_identity"
    printf 'web_image_id=%s\n' "$web_identity"
    printf 'nginx_image_id=%s\n' "$nginx_identity"
    printf 'recruiting_enabled=%s\n' "$recruiting_enabled"
    printf 'recorded_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } > "$temporary"
  if ! ln "$temporary" "$receipt"; then
    rm -f -- "$temporary"
    die "Refusing to overwrite historical release receipt: $receipt"
  fi
  rm -f -- "$temporary"
  validate_release_receipt "$receipt" "$source_revision" "$image_tag" "$api_identity" "$web_identity" "$nginx_identity" "$recruiting_enabled"
  printf '%s' "$receipt"
}

load_release_receipt() {
  local image_tag="$1" receipt
  receipt="$(release_receipt_path "$image_tag")"
  [ -f "$receipt" ] && [ ! -L "$receipt" ] || die "Historical release receipt is missing or unsafe: $receipt"
  RELEASE_SOURCE_REVISION="$(record_value "$receipt" source_revision)"
  RELEASE_IMAGE_TAG="$(record_value "$receipt" image_tag)"
  RELEASE_API_IMAGE_ID="$(record_value "$receipt" api_image_id)"
  RELEASE_WEB_IMAGE_ID="$(record_value "$receipt" web_image_id)"
  RELEASE_NGINX_IMAGE_ID="$(record_value "$receipt" nginx_image_id)"
  RELEASE_RECRUITING_ENABLED="$(record_value "$receipt" recruiting_enabled)"
  validate_release_receipt "$receipt" "$RELEASE_SOURCE_REVISION" "$RELEASE_IMAGE_TAG" "$RELEASE_API_IMAGE_ID" "$RELEASE_WEB_IMAGE_ID" "$RELEASE_NGINX_IMAGE_ID" "$RELEASE_RECRUITING_ENABLED"
  RELEASE_RECEIPT_PATH="$receipt"
  RELEASE_RECEIPT_DIGEST="$(sha256sum "$receipt" | cut -d' ' -f1)"
  printf '%s' "$receipt"
}

build_or_reuse_release_images() {
  local image_tag="$1" source_revision="$2" receipt existing_count entry service image
  local api_identity web_identity nginx_identity
  is_full_source_revision "$source_revision" || die "Release source revision is invalid"
  existing_count=0
  for image in connectmd-api connectmd-web connectmd-nginx; do
    if docker image inspect "${image}:${image_tag}" >/dev/null 2>&1; then
      existing_count=$((existing_count + 1))
    fi
  done
  receipt="$(release_receipt_path "$image_tag")"
  if [ "$existing_count" -gt 0 ]; then
    [ "$existing_count" = 3 ] \
      || die "Partial pre-existing release image set is unsafe: $image_tag"
    [ -f "$receipt" ] && [ ! -L "$receipt" ] \
      || die "Pre-existing release images lack an immutable historical receipt: $image_tag"
    load_release_receipt "$image_tag" >/dev/null
    [ "$RELEASE_SOURCE_REVISION" = "$source_revision" ] \
      || die "Pre-existing release receipt source does not match the clean checkout"
    assert_release_images_match "$image_tag" "$RELEASE_API_IMAGE_ID" "$RELEASE_WEB_IMAGE_ID" "$RELEASE_NGINX_IMAGE_ID"
    for image in connectmd-api connectmd-web connectmd-nginx; do
      printf 'REUSING_IMAGE=%s\n' "${image}:${image_tag}"
    done
    return
  fi
  [ ! -e "$receipt" ] && [ ! -L "$receipt" ] \
    || die "Historical release receipt exists but its local images are unavailable: $image_tag"
  for entry in "api:connectmd-api" "frontend:connectmd-web" "nginx:connectmd-nginx"; do
    service="${entry%%:*}"
    image="${entry#*:}"
    compose build "$service"
    docker image inspect "${image}:${image_tag}" >/dev/null 2>&1 \
      || die "Release build did not create the expected image tag: ${image}:${image_tag}"
  done
  api_identity="$(image_identity_for_tag connectmd-api "$image_tag")"
  web_identity="$(image_identity_for_tag connectmd-web "$image_tag")"
  nginx_identity="$(image_identity_for_tag connectmd-nginx "$image_tag")"
  assert_release_images_match "$image_tag" "$api_identity" "$web_identity" "$nginx_identity"
  export CONNECTMD_RELEASE_IMAGES_BUILT_FOR_TAG="$image_tag"
}

apply_database_role_contract() {
  local mode="$1" mutate=false reconcile=false verify=false contract="$REPO_ROOT/infra/postgres/database-role-contract.sql"
  case "$mode" in
    bootstrap) mutate=true ;;
    reconcile) mutate=true; reconcile=true; verify=true ;;
    verify) verify=true ;;
    *) die "Unsupported database role contract mode: $mode" ;;
  esac
  require_file "$contract"
  {
    printf '%s\n' \
      "$(require_secret_value CONNECTMD_MIGRATOR_DB_PASSWORD)" \
      "$(require_secret_value CONNECTMD_API_DB_PASSWORD)" \
      "$(require_secret_value CONNECTMD_SEARCH_PROJECTION_DB_PASSWORD)" \
      "$(require_secret_value CONNECTMD_PROJECTION_ADMIN_DB_PASSWORD)" \
      "$(require_secret_value CONNECTMD_ACCOUNT_ERASURE_DB_PASSWORD)" \
      "$(require_secret_value CONNECTMD_BACKUP_DB_PASSWORD)"
    cat -- "$contract"
  } | compose exec -T postgres sh -eu -c '
    IFS= read -r migrator_password
    IFS= read -r api_password
    IFS= read -r search_projection_password
    IFS= read -r projection_admin_password
    IFS= read -r account_erasure_password
    IFS= read -r backup_password
    export PGOPTIONS="-c connectmd.migrator_password=$migrator_password -c connectmd.api_password=$api_password -c connectmd.search_projection_password=$search_projection_password -c connectmd.projection_admin_password=$projection_admin_password -c connectmd.account_erasure_password=$account_erasure_password -c connectmd.backup_password=$backup_password"
    exec psql --set ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
      --set connectmd_mutate="$1" --set connectmd_reconcile="$2" --set connectmd_verify="$3" -f -
  ' _ "$mutate" "$reconcile" "$verify"
}

bootstrap_database_roles() { apply_database_role_contract bootstrap; }
reconcile_database_roles() { apply_database_role_contract reconcile; }
verify_database_roles() { apply_database_role_contract verify; }

attest_restore_migrator_role() {
  cat <<'SQL' | compose --profile database-operations run --rm --no-deps -T database-restore \
    psql --set ON_ERROR_STOP=1 -f -
DO $attest$
BEGIN
  IF session_user <> 'connectmd_migrator' OR current_user <> 'connectmd_migrator' THEN
    RAISE EXCEPTION 'restore migrator identity attestation failed';
  END IF;
  IF EXISTS (
    SELECT 1 FROM pg_roles
    WHERE rolname = current_user
      AND (NOT rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR rolinherit
           OR rolreplication OR rolbypassrls)
  ) THEN
    RAISE EXCEPTION 'restore migrator role attributes failed';
  END IF;
  IF EXISTS (
    SELECT 1 FROM pg_auth_members membership
    WHERE membership.roleid = (SELECT oid FROM pg_roles WHERE rolname = current_user)
       OR membership.member = (SELECT oid FROM pg_roles WHERE rolname = current_user)
  ) THEN
    RAISE EXCEPTION 'restore migrator role membership failed';
  END IF;
  IF (SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database())
       = current_user
     OR (SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = 'public')
       <> current_user
     OR NOT has_database_privilege(current_user, current_database(), 'CONNECT')
     OR has_database_privilege(current_user, current_database(), 'CREATE')
     OR has_database_privilege(current_user, current_database(), 'TEMPORARY')
     OR NOT has_schema_privilege(current_user, 'public', 'CREATE')
     OR NOT has_schema_privilege(current_user, 'public', 'USAGE') THEN
    RAISE EXCEPTION 'restore migrator database/schema authority failed';
  END IF;
END
$attest$;
SQL
}

acceptance_receipt_root() {
  printf '%s/.connectmd-lifecycle/release-acceptance' "$(backup_root)"
}

acceptance_receipt_path() {
  local image_tag="$1" stage_digest="$2"
  case "$image_tag" in "" | *[!A-Za-z0-9_.-]*) die "Acceptance image tag is invalid" ;; esac
  printf '%s' "$stage_digest" | grep -Eq '^[0-9a-f]{64}$' || die "Acceptance stage digest is invalid"
  printf '%s/acceptance-%s-%s.env' "$(acceptance_receipt_root)" "$image_tag" "$stage_digest"
}

acceptance_evidence_path() {
  local image_tag="$1" stage_digest="$2"
  case "$image_tag" in "" | *[!A-Za-z0-9_.-]*) die "Acceptance image tag is invalid" ;; esac
  printf '%s' "$stage_digest" | grep -Eq '^[0-9a-f]{64}$' || die "Acceptance stage digest is invalid"
  printf '%s/acceptance-%s-%s.evidence' "$(acceptance_receipt_root)" "$image_tag" "$stage_digest"
}

record_keys_exactly() {
  local record="$1" expected_keys="$2" actual_keys
  actual_keys="$(cut -d= -f1 -- "$record" | LC_ALL=C sort)"
  [ "$actual_keys" = "$expected_keys" ] || die "Release record contains unsupported or duplicate fields: $record"
}

staged_release_digest_or_none() {
  if [ ! -e "$STAGED_RELEASE_FILE" ] && [ ! -L "$STAGED_RELEASE_FILE" ]; then
    printf 'none'
    return
  fi
  load_staged_release >/dev/null
  printf '%s' "$STAGED_RELEASE_DIGEST"
}

active_marker_digest_or_none() {
  if [ ! -e "$RELEASE_ENV_FILE" ] && [ ! -L "$RELEASE_ENV_FILE" ]; then
    printf 'none'
    return
  fi
  require_secure_record_file "$RELEASE_ENV_FILE" "Active release marker"
  digest_of_file "$RELEASE_ENV_FILE"
}

validate_staged_release() {
  local stage="${1:-$STAGED_RELEASE_FILE}" expected_keys source image_tag api_identity web_identity nginx_identity release_digest prior_digest receipt recruiting_enabled
  require_secure_record_file "$stage" "Staged release record"
  expected_keys="$(printf '%s\n' api_image_id format image_tag nginx_image_id prior_accepted_marker_digest recruiting_enabled release_receipt_digest source_revision staged_at web_image_id | LC_ALL=C sort)"
  record_keys_exactly "$stage" "$expected_keys"
  [ "$(record_value "$stage" format)" = "connectmd-staged-release-v1" ] || die "Staged release format is unsupported"
  source="$(record_value "$stage" source_revision)"
  image_tag="$(record_value "$stage" image_tag)"
  api_identity="$(record_value "$stage" api_image_id)"
  web_identity="$(record_value "$stage" web_image_id)"
  nginx_identity="$(record_value "$stage" nginx_image_id)"
  recruiting_enabled="$(record_value "$stage" recruiting_enabled)"
  release_digest="$(record_value "$stage" release_receipt_digest)"
  prior_digest="$(record_value "$stage" prior_accepted_marker_digest)"
  is_full_source_revision "$source" || die "Staged release source revision is invalid"
  case "$image_tag" in "" | *[!A-Za-z0-9_.-]*) die "Staged release image tag is invalid" ;; esac
  is_image_identity "$api_identity" && is_image_identity "$web_identity" && is_image_identity "$nginx_identity" || die "Staged release image identity is invalid"
  case "$recruiting_enabled" in true | false) ;; *) die "Staged release recruiting state is invalid" ;; esac
  [ "$recruiting_enabled" = "$(normalize_recruiting_enabled)" ] || die "Staged release recruiting state does not match .env"
  printf '%s' "$release_digest" | grep -Eq '^[0-9a-f]{64}$' || die "Staged release receipt digest is invalid"
  case "$prior_digest" in none) ;; *) printf '%s' "$prior_digest" | grep -Eq '^[0-9a-f]{64}$' || die "Staged release prior accepted marker digest is invalid" ;; esac
  grep -Eq '^staged_at=[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$' "$stage" || die "Staged release timestamp is invalid"
  receipt="$(release_receipt_path "$image_tag")"
  validate_release_receipt "$receipt" "$source" "$image_tag" "$api_identity" "$web_identity" "$nginx_identity" "$recruiting_enabled"
  [ "$(digest_of_file "$receipt")" = "$release_digest" ] || die "Staged release receipt digest does not match local release history"
  STAGED_SOURCE_REVISION="$source"
  STAGED_IMAGE_TAG="$image_tag"
  STAGED_API_IMAGE_ID="$api_identity"
  STAGED_WEB_IMAGE_ID="$web_identity"
  STAGED_NGINX_IMAGE_ID="$nginx_identity"
  STAGED_RECRUITING_ENABLED="$recruiting_enabled"
  STAGED_RELEASE_RECEIPT_DIGEST="$release_digest"
  STAGED_PRIOR_ACCEPTED_MARKER_DIGEST="$prior_digest"
  STAGED_RELEASE_DIGEST="$(digest_of_file "$stage")"
}

load_staged_release() {
  validate_staged_release "$STAGED_RELEASE_FILE"
  printf '%s' "$STAGED_RELEASE_FILE"
}

assert_no_pending_staged_release() {
  if [ -e "$STAGED_RELEASE_FILE" ] || [ -L "$STAGED_RELEASE_FILE" ]; then
    load_staged_release >/dev/null
    die "A staged release requires explicit public acceptance before this operation"
  fi
}

write_staged_release() {
  local source="$1" image_tag="$2" api_identity="$3" web_identity="$4" nginx_identity="$5"
  local receipt receipt_digest prior_digest temporary recruiting_enabled
  is_full_source_revision "$source" || die "Staged release source revision is invalid"
  assert_release_images_match "$image_tag" "$api_identity" "$web_identity" "$nginx_identity"
  recruiting_enabled="$(normalize_recruiting_enabled)"
  receipt="$(write_release_receipt "$source" "$image_tag" "$api_identity" "$web_identity" "$nginx_identity")"
  receipt_digest="$(digest_of_file "$receipt")"
  prior_digest="$(active_marker_digest_or_none)"
  if [ -e "$STAGED_RELEASE_FILE" ] || [ -L "$STAGED_RELEASE_FILE" ]; then
    load_staged_release >/dev/null
    [ "$STAGED_SOURCE_REVISION" = "$source" ] && [ "$STAGED_IMAGE_TAG" = "$image_tag" ] && [ "$STAGED_API_IMAGE_ID" = "$api_identity" ] && [ "$STAGED_WEB_IMAGE_ID" = "$web_identity" ] && [ "$STAGED_NGINX_IMAGE_ID" = "$nginx_identity" ] && [ "$STAGED_RECRUITING_ENABLED" = "$recruiting_enabled" ] && [ "$STAGED_RELEASE_RECEIPT_DIGEST" = "$receipt_digest" ] && [ "$STAGED_PRIOR_ACCEPTED_MARKER_DIGEST" = "$prior_digest" ] || die "A different staged release already exists"
    printf '%s' "$STAGED_RELEASE_FILE"
    return
  fi
  temporary="$(mktemp "$REPO_ROOT/.connectmd-staged-release.XXXXXX")"
  chmod 600 "$temporary"
  {
    printf 'format=connectmd-staged-release-v1\n'
    printf 'source_revision=%s\n' "$source"
    printf 'image_tag=%s\n' "$image_tag"
    printf 'api_image_id=%s\n' "$api_identity"
    printf 'web_image_id=%s\n' "$web_identity"
    printf 'nginx_image_id=%s\n' "$nginx_identity"
    printf 'recruiting_enabled=%s\n' "$recruiting_enabled"
    printf 'release_receipt_digest=%s\n' "$receipt_digest"
    printf 'prior_accepted_marker_digest=%s\n' "$prior_digest"
    printf 'staged_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } > "$temporary"
  if ! ln "$temporary" "$STAGED_RELEASE_FILE"; then
    rm -f -- "$temporary"
    die "Refusing to overwrite staged release record"
  fi
  rm -f -- "$temporary"
  load_staged_release >/dev/null
  printf '%s' "$STAGED_RELEASE_FILE"
}

validate_acceptance_receipt() {
  local receipt="$1" expected_source="$2" expected_tag="$3" expected_api="$4" expected_web="$5" expected_nginx="$6"
  local expected_recruiting expected_keys format source image_tag api_identity web_identity nginx_identity release_digest stage_digest evidence_digest evidence origin recruiting_enabled
  if [ "$#" -ge 7 ] && [ -n "${7:-}" ]; then
    expected_recruiting="$7"
  else
    expected_recruiting="$(normalize_recruiting_enabled)"
  fi
  require_secure_record_file "$receipt" "Release acceptance receipt"
  format="$(record_value "$receipt" format)"
  case "$format" in
    connectmd-release-acceptance-v1)
      expected_keys="$(printf '%s\n' a2a_search_sha256 accepted_at agent_card_sha256 api_image_id evidence_digest format hsts_sha256 http_redirect_sha256 https_origin image_tag llms_full_sha256 llms_sha256 mcp_initialize_tools_search_sha256 mcp_oauth_sha256 nginx_image_id oauth_sha256 openapi_sha256 public_search_sha256 recruiting_enabled release_receipt_digest source_revision stage_digest tls_leaf_sha256 web_image_id | LC_ALL=C sort)"
      ;;
    connectmd-release-acceptance-v2)
      expected_keys="$(printf '%s\n' a2a_search_sha256 accepted_at agent_card_sha256 api_image_id evidence_digest exact_search_sha256 format hsts_sha256 http_redirect_sha256 https_origin image_tag llms_full_sha256 llms_sha256 mcp_initialize_tools_search_sha256 mcp_oauth_sha256 nginx_image_id oauth_sha256 openapi_sha256 public_search_sha256 recruiting_enabled release_receipt_digest source_revision stage_digest tls_leaf_sha256 web_image_id | LC_ALL=C sort)"
      ;;
    *) die "Release acceptance format is unsupported" ;;
  esac
  record_keys_exactly "$receipt" "$expected_keys"
  source="$(record_value "$receipt" source_revision)"; image_tag="$(record_value "$receipt" image_tag)"
  api_identity="$(record_value "$receipt" api_image_id)"; web_identity="$(record_value "$receipt" web_image_id)"; nginx_identity="$(record_value "$receipt" nginx_image_id)"
  recruiting_enabled="$(record_value "$receipt" recruiting_enabled)"
  release_digest="$(record_value "$receipt" release_receipt_digest)"; stage_digest="$(record_value "$receipt" stage_digest)"; evidence_digest="$(record_value "$receipt" evidence_digest)"; origin="$(record_value "$receipt" https_origin)"
  is_full_source_revision "$source" || die "Release acceptance source revision is invalid"
  case "$image_tag" in "" | *[!A-Za-z0-9_.-]*) die "Release acceptance image tag is invalid" ;; esac
  is_image_identity "$api_identity" && is_image_identity "$web_identity" && is_image_identity "$nginx_identity" || die "Release acceptance image identity is invalid"
  case "$recruiting_enabled" in true | false) ;; *) die "Release acceptance recruiting state is invalid" ;; esac
  [ "$recruiting_enabled" = "$expected_recruiting" ] || die "Release acceptance recruiting state does not match"
  for digest in "$release_digest" "$stage_digest" "$evidence_digest"; do printf '%s' "$digest" | grep -Eq '^[0-9a-f]{64}$' || die "Release acceptance digest is invalid"; done
  [ "$origin" = "https://$(require_hostname)" ] || die "Release acceptance origin is not the configured public HTTPS origin"
  grep -Eq '^accepted_at=[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$' "$receipt" || die "Release acceptance timestamp is invalid"
  [ "$source" = "$expected_source" ] && [ "$image_tag" = "$expected_tag" ] && [ "$api_identity" = "$expected_api" ] && [ "$web_identity" = "$expected_web" ] && [ "$nginx_identity" = "$expected_nginx" ] || die "Release acceptance identity does not match"
  validate_release_receipt "$(release_receipt_path "$image_tag")" "$source" "$image_tag" "$api_identity" "$web_identity" "$nginx_identity" "$recruiting_enabled"
  [ "$(digest_of_file "$(release_receipt_path "$image_tag")")" = "$release_digest" ] || die "Release acceptance release digest does not match local history"
  evidence="$(acceptance_evidence_path "$image_tag" "$stage_digest")"
  require_secure_record_file "$evidence" "Release acceptance evidence"
  validate_acceptance_evidence "$evidence"
  if [ "$format" = "connectmd-release-acceptance-v1" ]; then
    ! grep -q '^format=' "$evidence" || die "Legacy release acceptance receipt requires legacy evidence"
  else
    grep -Fxq 'format=connectmd-release-acceptance-evidence-v2' "$evidence" || die "V2 release acceptance receipt requires exact-search evidence"
  fi
  [ "$(digest_of_file "$evidence")" = "$evidence_digest" ] || die "Release acceptance evidence digest does not match"
  local evidence_key
  for evidence_key in https_origin tls_leaf_sha256 http_redirect_sha256 hsts_sha256 openapi_sha256 llms_sha256 llms_full_sha256 agent_card_sha256 oauth_sha256 mcp_oauth_sha256 public_search_sha256 mcp_initialize_tools_search_sha256 a2a_search_sha256; do
    [ "$(record_value "$receipt" "$evidence_key")" = "$(record_value "$evidence" "$evidence_key")" ] \
      || die "Release acceptance receipt does not match its immutable evidence: $evidence_key"
  done
  if [ "$format" = "connectmd-release-acceptance-v2" ]; then
    [ "$(record_value "$receipt" exact_search_sha256)" = "$(record_value "$evidence" exact_search_sha256)" ] \
      || die "Release acceptance receipt does not match its immutable evidence: exact_search_sha256"
  fi
}

load_release_acceptance() {
  local image_tag="$1" expected_digest="${2:-}" expected_stage_digest="${3:-}"
  local root candidate receipt selected="" selected_at="" accepted_at digest matches=0
  load_release_receipt "$image_tag" >/dev/null
  if [ -n "$expected_digest" ]; then
    printf '%s' "$expected_digest" | grep -Eq '^[0-9a-f]{64}$' || die "Expected acceptance digest is invalid"
  fi
  if [ -n "$expected_stage_digest" ]; then
    receipt="$(acceptance_receipt_path "$image_tag" "$expected_stage_digest")"
    validate_acceptance_receipt "$receipt" "$RELEASE_SOURCE_REVISION" "$RELEASE_IMAGE_TAG" "$RELEASE_API_IMAGE_ID" "$RELEASE_WEB_IMAGE_ID" "$RELEASE_NGINX_IMAGE_ID"
    [ -z "$expected_digest" ] || [ "$(digest_of_file "$receipt")" = "$expected_digest" ] || die "Acceptance receipt digest does not match the expected authority"
    ACCEPTANCE_RECEIPT_PATH="$receipt"
    ACCEPTANCE_RECEIPT_DIGEST="$(digest_of_file "$receipt")"
    printf '%s' "$receipt"
    return
  fi
  root="$(acceptance_receipt_root)"
  [ -d "$root" ] && [ ! -L "$root" ] || die "Release acceptance history is missing or unsafe"
  for candidate in "$root"/acceptance-"$image_tag"-*.env; do
    [ -f "$candidate" ] && [ ! -L "$candidate" ] || continue
    digest="$(digest_of_file "$candidate")"
    if [ -n "$expected_digest" ] && [ "$digest" != "$expected_digest" ]; then
      continue
    fi
    validate_acceptance_receipt "$candidate" "$RELEASE_SOURCE_REVISION" "$RELEASE_IMAGE_TAG" "$RELEASE_API_IMAGE_ID" "$RELEASE_WEB_IMAGE_ID" "$RELEASE_NGINX_IMAGE_ID"
    accepted_at="$(record_value "$candidate" accepted_at)"
    if [ -z "$selected" ] || [[ "$accepted_at" > "$selected_at" ]]; then
      selected="$candidate"
      selected_at="$accepted_at"
      matches=1
    elif [ "$accepted_at" = "$selected_at" ]; then
      matches=$((matches + 1))
    fi
  done
  [ -n "$selected" ] || die "Accepted release receipt is missing or unsafe for image tag: $image_tag"
  if [ -n "$expected_digest" ]; then
    [ "$matches" = 1 ] || die "Acceptance digest matched more than one immutable receipt"
  else
    [ "$matches" = 1 ] || die "Multiple equally recent acceptance receipts require explicit recovery"
  fi
  ACCEPTANCE_RECEIPT_PATH="$selected"
  ACCEPTANCE_RECEIPT_DIGEST="$(digest_of_file "$selected")"
  printf '%s' "$selected"
}

validate_acceptance_evidence() {
  local evidence="$1" expected_keys evidence_format
  require_secure_record_file "$evidence" "Release acceptance evidence"
  evidence_format="$(grep -E '^format=' "$evidence" | cut -d= -f2- || true)"
  case "$evidence_format" in
    "")
      expected_keys="$(printf '%s\n' a2a_search_sha256 agent_card_sha256 hsts_sha256 http_redirect_sha256 https_origin llms_full_sha256 llms_sha256 mcp_initialize_tools_search_sha256 mcp_oauth_sha256 oauth_sha256 openapi_sha256 public_search_sha256 tls_leaf_sha256 | LC_ALL=C sort)"
      ;;
    connectmd-release-acceptance-evidence-v2)
      expected_keys="$(printf '%s\n' a2a_search_sha256 agent_card_sha256 exact_search_sha256 format hsts_sha256 http_redirect_sha256 https_origin llms_full_sha256 llms_sha256 mcp_initialize_tools_search_sha256 mcp_oauth_sha256 oauth_sha256 openapi_sha256 public_search_sha256 tls_leaf_sha256 | LC_ALL=C sort)"
      ;;
    *) die "Release acceptance evidence format is unsupported" ;;
  esac
  record_keys_exactly "$evidence" "$expected_keys"
  [ "$(record_value "$evidence" https_origin)" = "https://$(require_hostname)" ] || die "Release acceptance evidence origin is invalid"
  local key value
  for key in a2a_search_sha256 agent_card_sha256 hsts_sha256 http_redirect_sha256 llms_full_sha256 llms_sha256 mcp_initialize_tools_search_sha256 mcp_oauth_sha256 oauth_sha256 openapi_sha256 public_search_sha256 tls_leaf_sha256; do
    value="$(record_value "$evidence" "$key")"
    printf '%s' "$value" | grep -Eq '^[0-9a-f]{64}$' || die "Release acceptance evidence digest is invalid: $key"
  done
  if [ "$evidence_format" = "connectmd-release-acceptance-evidence-v2" ]; then
    value="$(record_value "$evidence" exact_search_sha256)"
    printf '%s' "$value" | grep -Eq '^[0-9a-f]{64}$' || die "Release acceptance evidence digest is invalid: exact_search_sha256"
  fi
}

write_release_acceptance() {
  local source="$1" image_tag="$2" api_identity="$3" web_identity="$4" nginx_identity="$5" stage_digest="$6" evidence_input="$7"
  local root receipt evidence temporary receipt_temporary release_digest evidence_digest existing_stage evidence_format receipt_format recruiting_enabled
  is_full_source_revision "$source" || die "Release acceptance source revision is invalid"
  assert_release_images_match "$image_tag" "$api_identity" "$web_identity" "$nginx_identity"
  load_staged_release >/dev/null
  existing_stage="$STAGED_RELEASE_DIGEST"
  [ "$existing_stage" = "$stage_digest" ] || die "Release acceptance stage digest does not match the pending stage"
  recruiting_enabled="$(normalize_recruiting_enabled)"
  [ "$STAGED_SOURCE_REVISION" = "$source" ] && [ "$STAGED_IMAGE_TAG" = "$image_tag" ] && [ "$STAGED_API_IMAGE_ID" = "$api_identity" ] && [ "$STAGED_WEB_IMAGE_ID" = "$web_identity" ] && [ "$STAGED_NGINX_IMAGE_ID" = "$nginx_identity" ] && [ "$STAGED_RECRUITING_ENABLED" = "$recruiting_enabled" ] || die "Release acceptance stage identity does not match"
  validate_acceptance_evidence "$evidence_input"
  root="$(acceptance_receipt_root)"
  [ ! -L "$root" ] || die "Release acceptance directory must not be a symlink"
  mkdir -p -- "$root"
  [ -d "$root" ] && [ ! -L "$root" ] || die "Release acceptance directory is unsafe"
  [ "$(realpath -e "$root")" = "$root" ] || die "Release acceptance directory is not canonical"
  chmod 700 "$root"
  receipt="$(acceptance_receipt_path "$image_tag" "$stage_digest")"
  evidence="$(acceptance_evidence_path "$image_tag" "$stage_digest")"
  release_digest="$(digest_of_file "$(release_receipt_path "$image_tag")")"
  if [ -e "$receipt" ] || [ -L "$receipt" ]; then
    validate_acceptance_receipt "$receipt" "$source" "$image_tag" "$api_identity" "$web_identity" "$nginx_identity" "$recruiting_enabled"
    [ "$(record_value "$receipt" stage_digest)" = "$stage_digest" ] || die "Existing acceptance receipt was bound to a different stage"
    [ "$(record_value "$receipt" release_receipt_digest)" = "$release_digest" ] || die "Existing acceptance receipt release digest changed"
    printf '%s' "$receipt"
    return
  fi
  if [ -e "$evidence" ] || [ -L "$evidence" ]; then
    validate_acceptance_evidence "$evidence"
    # A process can die after the immutable evidence link and before its
    # receipt. Reuse that already-validated, stage-bound evidence on retry;
    # public protocol responses may contain fresh request IDs or timestamps.
    :
  else
    temporary="$(mktemp "$root/.acceptance-evidence.XXXXXX")"
    chmod 600 "$temporary"
    cat -- "$evidence_input" > "$temporary"
    if ! ln "$temporary" "$evidence"; then
      rm -f -- "$temporary"
      die "Refusing to overwrite immutable acceptance evidence"
    fi
    rm -f -- "$temporary"
  fi
  validate_acceptance_evidence "$evidence"
  evidence_format="$(grep -E '^format=' "$evidence" | cut -d= -f2- || true)"
  if [ "$evidence_format" = "connectmd-release-acceptance-evidence-v2" ]; then
    receipt_format=connectmd-release-acceptance-v2
  else
    receipt_format=connectmd-release-acceptance-v1
  fi
  evidence_digest="$(digest_of_file "$evidence")"
  receipt_temporary="$(mktemp "$root/.acceptance.XXXXXX")"
  chmod 600 "$receipt_temporary"
  {
    printf 'format=%s\n' "$receipt_format"
    printf 'source_revision=%s\n' "$source"
    printf 'image_tag=%s\n' "$image_tag"
    printf 'api_image_id=%s\n' "$api_identity"
    printf 'web_image_id=%s\n' "$web_identity"
    printf 'nginx_image_id=%s\n' "$nginx_identity"
    printf 'recruiting_enabled=%s\n' "$recruiting_enabled"
    printf 'release_receipt_digest=%s\n' "$release_digest"
    printf 'stage_digest=%s\n' "$stage_digest"
    printf 'https_origin=%s\n' "$(record_value "$evidence" https_origin)"
    printf 'tls_leaf_sha256=%s\n' "$(record_value "$evidence" tls_leaf_sha256)"
    printf 'http_redirect_sha256=%s\n' "$(record_value "$evidence" http_redirect_sha256)"
    printf 'hsts_sha256=%s\n' "$(record_value "$evidence" hsts_sha256)"
    printf 'openapi_sha256=%s\n' "$(record_value "$evidence" openapi_sha256)"
    printf 'llms_sha256=%s\n' "$(record_value "$evidence" llms_sha256)"
    printf 'llms_full_sha256=%s\n' "$(record_value "$evidence" llms_full_sha256)"
    printf 'agent_card_sha256=%s\n' "$(record_value "$evidence" agent_card_sha256)"
    printf 'oauth_sha256=%s\n' "$(record_value "$evidence" oauth_sha256)"
    printf 'mcp_oauth_sha256=%s\n' "$(record_value "$evidence" mcp_oauth_sha256)"
    printf 'public_search_sha256=%s\n' "$(record_value "$evidence" public_search_sha256)"
    if [ "$receipt_format" = "connectmd-release-acceptance-v2" ]; then
      printf 'exact_search_sha256=%s\n' "$(record_value "$evidence" exact_search_sha256)"
    fi
    printf 'mcp_initialize_tools_search_sha256=%s\n' "$(record_value "$evidence" mcp_initialize_tools_search_sha256)"
    printf 'a2a_search_sha256=%s\n' "$(record_value "$evidence" a2a_search_sha256)"
    printf 'evidence_digest=%s\n' "$evidence_digest"
    printf 'accepted_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } > "$receipt_temporary"
  if ! ln "$receipt_temporary" "$receipt"; then
    rm -f -- "$receipt_temporary"
    die "Refusing to overwrite immutable release acceptance receipt"
  fi
  rm -f -- "$receipt_temporary"
  validate_acceptance_receipt "$receipt" "$source" "$image_tag" "$api_identity" "$web_identity" "$nginx_identity"
  printf '%s' "$receipt"
}

clear_staged_release_after_acceptance() {
  local expected_stage_digest="$1"
  load_staged_release >/dev/null
  [ "$STAGED_RELEASE_DIGEST" = "$expected_stage_digest" ] || die "Staged release changed before acceptance cleanup"
  load_release_acceptance "$STAGED_IMAGE_TAG" "" "$expected_stage_digest" >/dev/null
  [ "$(record_value "$ACCEPTANCE_RECEIPT_PATH" stage_digest)" = "$expected_stage_digest" ] || die "Acceptance receipt is not bound to this staged release"
  rm -f -- "$STAGED_RELEASE_FILE"
  [ ! -e "$STAGED_RELEASE_FILE" ] && [ ! -L "$STAGED_RELEASE_FILE" ] || die "Staged release record could not be removed after accepted promotion"
}

discard_staged_release_after_rollback() {
  local expected_stage_digest="$1" expected_prior_marker_digest="$2" expected_source="$3" expected_tag="$4" expected_api="$5" expected_web="$6" expected_nginx="$7"
  load_staged_release >/dev/null
  [ "$STAGED_RELEASE_DIGEST" = "$expected_stage_digest" ] || die "Staged release changed before rollback cleanup"
  [ "$STAGED_PRIOR_ACCEPTED_MARKER_DIGEST" = "$expected_prior_marker_digest" ] || die "Staged release prior accepted authority changed before rollback cleanup"
  load_active_release_identity
  [ "$RELEASE_SOURCE_REVISION" = "$expected_source" ] && [ "$RELEASE_IMAGE_TAG" = "$expected_tag" ] && [ "$RELEASE_API_IMAGE_ID" = "$expected_api" ] && [ "$RELEASE_WEB_IMAGE_ID" = "$expected_web" ] && [ "$RELEASE_NGINX_IMAGE_ID" = "$expected_nginx" ] || die "Rollback did not restore the exact prior accepted authority"
  [ "$(active_marker_digest_or_none)" = "$expected_prior_marker_digest" ] || die "Rollback did not restore the exact prior accepted marker authority"
  rm -f -- "$STAGED_RELEASE_FILE"
  [ ! -e "$STAGED_RELEASE_FILE" ] && [ ! -L "$STAGED_RELEASE_FILE" ] || die "Staged release record could not be removed after rollback"
}

clear_matching_completed_restore_state() {
  local source="$1" image_tag="$2" api_identity="$3" web_identity="$4" nginx_identity="$5" receipt_digest="$6"
  local expected_keys backup_format backup_acceptance_digest restore_format prior_state_key prior_state
  if [ ! -e "$RESTORE_STATE_FILE" ] && [ ! -L "$RESTORE_STATE_FILE" ]; then
    return
  fi
  require_secure_record_file "$RESTORE_STATE_FILE" "Completed restore state"
  restore_format="$(record_value "$RESTORE_STATE_FILE" format)"
  case "$restore_format" in
    connectmd-restore-state-v2)
      expected_keys="$(printf '%s\n' api_image_id backup_acceptance_receipt_digest backup_format db_manifest_digest deletion_journal_head_digest deletion_journal_head_sequence format generation_id image_tag markdown_manifest_digest nginx_image_id phase registration_receipt_digest release_receipt_digest search_rebuild_pending source_revision web_image_id worker_prior_state | LC_ALL=C sort)"
      ;;
    connectmd-restore-state-v3)
      expected_keys="$(printf '%s\n' api_image_id api_prior_state backup_acceptance_receipt_digest backup_format converter_prior_state db_manifest_digest deletion_journal_head_digest deletion_journal_head_sequence format frontend_prior_state generation_id image_tag markdown_manifest_digest nginx_image_id nginx_prior_state phase projection_prior_state registration_receipt_digest release_receipt_digest search_rebuild_pending source_revision web_image_id worker_prior_state | LC_ALL=C sort)"
      for prior_state_key in api_prior_state converter_prior_state projection_prior_state worker_prior_state frontend_prior_state nginx_prior_state; do
        prior_state="$(record_value "$RESTORE_STATE_FILE" "$prior_state_key")"
        case "$prior_state" in absent | stopped | running | paused) ;; *) die "Completed restore state has invalid service intent: $prior_state_key" ;; esac
      done
      ;;
    *) die "Restore state format is unsupported" ;;
  esac
  record_keys_exactly "$RESTORE_STATE_FILE" "$expected_keys"
  [ "$(record_value "$RESTORE_STATE_FILE" phase)" = "complete" ] || die "Restore state is incomplete; explicit recovery is required"
  backup_format="$(record_value "$RESTORE_STATE_FILE" backup_format)"
  backup_acceptance_digest="$(record_value "$RESTORE_STATE_FILE" backup_acceptance_receipt_digest)"
  case "$backup_format" in
    connectmd-backup-v2) [ "$backup_acceptance_digest" = none ] || die "Legacy backup restore state must not carry accepted authority" ;;
    connectmd-backup-v3) printf '%s' "$backup_acceptance_digest" | grep -Eq '^[0-9a-f]{64}$' || die "V3 backup restore state acceptance authority is invalid" ;;
    *) die "Restore state backup format is unsupported" ;;
  esac
  [ "$(record_value "$RESTORE_STATE_FILE" source_revision)" = "$source" ] && [ "$(record_value "$RESTORE_STATE_FILE" image_tag)" = "$image_tag" ] && [ "$(record_value "$RESTORE_STATE_FILE" api_image_id)" = "$api_identity" ] && [ "$(record_value "$RESTORE_STATE_FILE" web_image_id)" = "$web_identity" ] && [ "$(record_value "$RESTORE_STATE_FILE" nginx_image_id)" = "$nginx_identity" ] && [ "$(record_value "$RESTORE_STATE_FILE" release_receipt_digest)" = "$receipt_digest" ] || die "Completed restore state is not bound to the accepted release"
  rm -f -- "$RESTORE_STATE_FILE"
  [ ! -e "$RESTORE_STATE_FILE" ] && [ ! -L "$RESTORE_STATE_FILE" ] || die "Completed restore state could not be cleared after accepted promotion"
}

load_active_release_identity() {
  local expected_keys actual_keys lifecycle_enabled recruiting_enabled receipt source_revision image_tag api_identity web_identity nginx_identity receipt_digest acceptance_digest acceptance
  require_secure_record_file "$RELEASE_ENV_FILE" "Active release marker"
  lifecycle_enabled="$(record_value "$RELEASE_ENV_FILE" CONNECTMD_ACCOUNT_LIFECYCLE_PINNED)"
  case "$lifecycle_enabled" in true | false) ;; *) die "Active release lifecycle state is invalid" ;; esac
  expected_keys="$(printf '%s\n' CONNECTMD_ACCEPTANCE_RECEIPT_SHA256 CONNECTMD_ACCOUNT_ERASURE_DB_PASSWORD_SHA256 CONNECTMD_ACCOUNT_LIFECYCLE_PINNED CONNECTMD_API_DB_PASSWORD_SHA256 CONNECTMD_API_IMAGE_ID CONNECTMD_API_KEY_PEPPER_SHA256 CONNECTMD_BACKUP_DB_PASSWORD_SHA256 CONNECTMD_DELETION_WITNESS_DIR_SHA256 CONNECTMD_DELETION_WITNESS_HMAC_KEY_SHA256 CONNECTMD_IMAGE_TAG CONNECTMD_LIFECYCLE_AEAD_KEY_SHA256 CONNECTMD_LIFECYCLE_HMAC_KEY_SHA256 CONNECTMD_MEILISEARCH_SEARCH_KEY_SHA256 CONNECTMD_MIGRATOR_DB_PASSWORD_SHA256 CONNECTMD_NGINX_IMAGE_ID CONNECTMD_PROJECTION_ADMIN_DB_PASSWORD_SHA256 CONNECTMD_RECRUITING_ENABLED_PINNED CONNECTMD_RELEASE_FORMAT CONNECTMD_RELEASE_RECEIPT_SHA256 CONNECTMD_SEARCH_PROJECTION_DB_PASSWORD_SHA256 CONNECTMD_SEARCH_PROJECTION_MEILI_KEY_SHA256 CONNECTMD_SOURCE_REVISION CONNECTMD_WEB_IMAGE_ID MEILI_MASTER_KEY_SHA256 POSTGRES_PASSWORD_SHA256 | LC_ALL=C sort)"
  if [ "$lifecycle_enabled" = true ]; then
    expected_keys="$(printf '%s\n' "$expected_keys" CONNECTMD_ACCOUNT_ERASURE_MEILI_KEY_SHA256 | LC_ALL=C sort)"
  fi
  actual_keys="$(cut -d= -f1 "$RELEASE_ENV_FILE" | LC_ALL=C sort)"
  [ "$actual_keys" = "$expected_keys" ] || die "Active release receipt contains unsupported fields"
  [ "$(record_value "$RELEASE_ENV_FILE" CONNECTMD_RELEASE_FORMAT)" = "connectmd-release-v3" ] \
    || die "Active release marker is not accepted v3 authority; explicitly stage and accept a release"
  recruiting_enabled="$(record_value "$RELEASE_ENV_FILE" CONNECTMD_RECRUITING_ENABLED_PINNED)"
  case "$recruiting_enabled" in true | false) ;; *) die "Active release recruiting state is invalid" ;; esac
  [ "$recruiting_enabled" = "$(normalize_recruiting_enabled)" ] || die "Active release recruiting state does not match .env"
  source_revision="$(record_value "$RELEASE_ENV_FILE" CONNECTMD_SOURCE_REVISION)"
  image_tag="$(record_value "$RELEASE_ENV_FILE" CONNECTMD_IMAGE_TAG)"
  api_identity="$(record_value "$RELEASE_ENV_FILE" CONNECTMD_API_IMAGE_ID)"
  web_identity="$(record_value "$RELEASE_ENV_FILE" CONNECTMD_WEB_IMAGE_ID)"
  nginx_identity="$(record_value "$RELEASE_ENV_FILE" CONNECTMD_NGINX_IMAGE_ID)"
  receipt_digest="$(record_value "$RELEASE_ENV_FILE" CONNECTMD_RELEASE_RECEIPT_SHA256)"
  acceptance_digest="$(record_value "$RELEASE_ENV_FILE" CONNECTMD_ACCEPTANCE_RECEIPT_SHA256)"
  is_full_source_revision "$source_revision" || die "Active release source revision is invalid"
  is_image_identity "$api_identity" && is_image_identity "$web_identity" && is_image_identity "$nginx_identity" \
    || die "Active release image identity is invalid"
  printf '%s' "$receipt_digest" | grep -Eq '^[0-9a-f]{64}$' \
    || die "Active release receipt digest is invalid"
  printf '%s' "$acceptance_digest" | grep -Eq '^[0-9a-f]{64}$' \
    || die "Active release acceptance digest is invalid"
  receipt="$(release_receipt_path "$image_tag")"
  validate_release_receipt "$receipt" "$source_revision" "$image_tag" "$api_identity" "$web_identity" "$nginx_identity"
  [ "$(sha256sum "$receipt" | cut -d' ' -f1)" = "$receipt_digest" ] \
    || die "Active release receipt digest does not match the historical release receipt"
  acceptance="$(load_release_acceptance "$image_tag" "$acceptance_digest")"
  [ "$(digest_of_file "$acceptance")" = "$acceptance_digest" ] \
    || die "Active release acceptance digest does not match the immutable acceptance receipt"
  RELEASE_SOURCE_REVISION="$source_revision"
  RELEASE_IMAGE_TAG="$image_tag"
  RELEASE_API_IMAGE_ID="$api_identity"
  RELEASE_WEB_IMAGE_ID="$web_identity"
  RELEASE_NGINX_IMAGE_ID="$nginx_identity"
  RELEASE_RECRUITING_ENABLED="$recruiting_enabled"
  RELEASE_RECEIPT_PATH="$receipt"
  RELEASE_RECEIPT_DIGEST="$receipt_digest"
  RELEASE_ACCEPTANCE_PATH="$acceptance"
  RELEASE_ACCEPTANCE_DIGEST="$acceptance_digest"
}

assert_active_release_identity() {
  load_active_release_identity
  [ "$(current_source_revision)" = "$RELEASE_SOURCE_REVISION" ] \
    || die "Checked-out source revision does not match the active release receipt"
  assert_release_images_match "$RELEASE_IMAGE_TAG" "$RELEASE_API_IMAGE_ID" "$RELEASE_WEB_IMAGE_ID" "$RELEASE_NGINX_IMAGE_ID"
}

select_release_image_tag() {
  local mode="${1:-}" current_source
  local selected_tag selected_api selected_web selected_nginx
  [[ -v CONNECTMD_IMAGE_TAG ]] \
    && die "CONNECTMD_IMAGE_TAG must not be inherited; release identity must be selected from local authority"
  case "$mode" in
    accepted-only | staged-or-accepted) ;;
    *) die "Release image selection mode is unsupported" ;;
  esac

  if [ "$mode" = accepted-only ]; then
    assert_no_pending_staged_release
    assert_active_release_identity
    selected_tag="$RELEASE_IMAGE_TAG"
    selected_api="$RELEASE_API_IMAGE_ID"
    selected_web="$RELEASE_WEB_IMAGE_ID"
    selected_nginx="$RELEASE_NGINX_IMAGE_ID"
  elif [ -e "$STAGED_RELEASE_FILE" ] || [ -L "$STAGED_RELEASE_FILE" ]; then
    current_source="$(current_source_revision)"
    load_staged_release >/dev/null
    [ "$STAGED_SOURCE_REVISION" = "$current_source" ] \
      || die "Staged release source revision does not match the checked-out source"
    assert_release_images_match "$STAGED_IMAGE_TAG" "$STAGED_API_IMAGE_ID" "$STAGED_WEB_IMAGE_ID" "$STAGED_NGINX_IMAGE_ID"
    selected_tag="$STAGED_IMAGE_TAG"
    selected_api="$STAGED_API_IMAGE_ID"
    selected_web="$STAGED_WEB_IMAGE_ID"
    selected_nginx="$STAGED_NGINX_IMAGE_ID"
  elif [ -e "$RELEASE_ENV_FILE" ] || [ -L "$RELEASE_ENV_FILE" ]; then
    assert_active_release_identity
    selected_tag="$RELEASE_IMAGE_TAG"
    selected_api="$RELEASE_API_IMAGE_ID"
    selected_web="$RELEASE_WEB_IMAGE_ID"
    selected_nginx="$RELEASE_NGINX_IMAGE_ID"
  else
    die "No accepted or staged release identity is available"
  fi

  assert_release_images_match "$selected_tag" "$selected_api" "$selected_web" "$selected_nginx"
  export CONNECTMD_IMAGE_TAG="$selected_tag"
}

persist_image_tag() {
  local image_tag="$1" source_revision="${2:-}" api_identity="${3:-}" web_identity="${4:-}" nginx_identity="${5:-}" acceptance_stage_digest="${6:-}"
  local postgres_hash migrator_db_hash api_db_hash projection_db_hash projection_admin_db_hash erasure_db_hash backup_db_hash meili_hash search_hash projection_meili_hash api_key_pepper_hash lifecycle_enabled recruiting_enabled erasure_meili_hash lifecycle_hmac_hash lifecycle_aead_hash witness_hmac_hash witness_dir_hash
  local temporary receipt receipt_digest acceptance acceptance_digest
  case "$image_tag" in
    "" | *[!A-Za-z0-9_.-]*) die "Image tag contains invalid characters" ;;
  esac
  source_revision="${source_revision:-$(current_source_revision)}"
  api_identity="${api_identity:-$(image_identity_for_tag connectmd-api "$image_tag")}"
  web_identity="${web_identity:-$(image_identity_for_tag connectmd-web "$image_tag")}"
  nginx_identity="${nginx_identity:-$(image_identity_for_tag connectmd-nginx "$image_tag")}"
  is_full_source_revision "$source_revision" || die "Release source revision is invalid"
  assert_release_images_match "$image_tag" "$api_identity" "$web_identity" "$nginx_identity"
  receipt="$(release_receipt_path "$image_tag")"
  validate_release_receipt "$receipt" "$source_revision" "$image_tag" "$api_identity" "$web_identity" "$nginx_identity"
  receipt_digest="$(digest_of_file "$receipt")"
  acceptance="$(load_release_acceptance "$image_tag" "" "$acceptance_stage_digest")"
  acceptance_digest="$(digest_of_file "$acceptance")"
  temporary="$(mktemp "$REPO_ROOT/.connectmd-release.env.XXXXXX")"
  chmod 600 "$temporary"
  postgres_hash="$(printf '%s' "$(require_secret_value POSTGRES_PASSWORD)" | sha256sum | cut -d' ' -f1)"
  migrator_db_hash="$(printf '%s' "$(require_secret_value CONNECTMD_MIGRATOR_DB_PASSWORD)" | sha256sum | cut -d' ' -f1)"
  api_db_hash="$(printf '%s' "$(require_secret_value CONNECTMD_API_DB_PASSWORD)" | sha256sum | cut -d' ' -f1)"
  meili_hash="$(printf '%s' "$(require_secret_value MEILI_MASTER_KEY)" | sha256sum | cut -d' ' -f1)"
  search_hash="$(printf '%s' "$(require_secret_value CONNECTMD_MEILISEARCH_SEARCH_KEY)" | sha256sum | cut -d' ' -f1)"
  projection_meili_hash="$(printf '%s' "$(require_secret_value CONNECTMD_SEARCH_PROJECTION_MEILI_KEY)" | sha256sum | cut -d' ' -f1)"
  projection_db_hash="$(printf '%s' "$(require_secret_value CONNECTMD_SEARCH_PROJECTION_DB_PASSWORD)" | sha256sum | cut -d' ' -f1)"
  projection_admin_db_hash="$(printf '%s' "$(require_secret_value CONNECTMD_PROJECTION_ADMIN_DB_PASSWORD)" | sha256sum | cut -d' ' -f1)"
  erasure_db_hash="$(printf '%s' "$(require_secret_value CONNECTMD_ACCOUNT_ERASURE_DB_PASSWORD)" | sha256sum | cut -d' ' -f1)"
  backup_db_hash="$(printf '%s' "$(require_secret_value CONNECTMD_BACKUP_DB_PASSWORD)" | sha256sum | cut -d' ' -f1)"
  api_key_pepper_hash="$(printf '%s' "$(require_secret_value CONNECTMD_API_KEY_PEPPER)" | sha256sum | cut -d' ' -f1)"
  lifecycle_hmac_hash="$(printf '%s' "$(require_secret_value CONNECTMD_LIFECYCLE_HMAC_KEY)" | sha256sum | cut -d' ' -f1)"
  lifecycle_aead_hash="$(printf '%s' "$(require_secret_value CONNECTMD_LIFECYCLE_AEAD_KEY)" | sha256sum | cut -d' ' -f1)"
  witness_hmac_hash="$(printf '%s' "$(require_secret_value CONNECTMD_DELETION_WITNESS_HMAC_KEY)" | sha256sum | cut -d' ' -f1)"
  witness_dir_hash="$(printf '%s' "$(realpath -m "$(read_env_value CONNECTMD_DELETION_WITNESS_DIR)")" | sha256sum | cut -d' ' -f1)"
  lifecycle_enabled="$(read_env_optional_value CONNECTMD_ACCOUNT_LIFECYCLE_ENABLED)"
  recruiting_enabled="$(normalize_recruiting_enabled)"
  {
    printf 'CONNECTMD_RELEASE_FORMAT=connectmd-release-v3\n'
    printf 'CONNECTMD_SOURCE_REVISION=%s\n' "$source_revision"
    printf 'CONNECTMD_IMAGE_TAG=%s\n' "$image_tag"
    printf 'CONNECTMD_API_IMAGE_ID=%s\n' "$api_identity"
    printf 'CONNECTMD_WEB_IMAGE_ID=%s\n' "$web_identity"
    printf 'CONNECTMD_NGINX_IMAGE_ID=%s\n' "$nginx_identity"
    printf 'CONNECTMD_RECRUITING_ENABLED_PINNED=%s\n' "$recruiting_enabled"
    printf 'CONNECTMD_RELEASE_RECEIPT_SHA256=%s\n' "$receipt_digest"
    printf 'CONNECTMD_ACCEPTANCE_RECEIPT_SHA256=%s\n' "$acceptance_digest"
    printf 'POSTGRES_PASSWORD_SHA256=%s\n' "$postgres_hash"
    printf 'CONNECTMD_MIGRATOR_DB_PASSWORD_SHA256=%s\n' "$migrator_db_hash"
    printf 'CONNECTMD_API_DB_PASSWORD_SHA256=%s\n' "$api_db_hash"
    printf 'MEILI_MASTER_KEY_SHA256=%s\n' "$meili_hash"
    printf 'CONNECTMD_MEILISEARCH_SEARCH_KEY_SHA256=%s\n' "$search_hash"
    printf 'CONNECTMD_SEARCH_PROJECTION_MEILI_KEY_SHA256=%s\n' "$projection_meili_hash"
    printf 'CONNECTMD_SEARCH_PROJECTION_DB_PASSWORD_SHA256=%s\n' "$projection_db_hash"
    printf 'CONNECTMD_PROJECTION_ADMIN_DB_PASSWORD_SHA256=%s\n' "$projection_admin_db_hash"
    printf 'CONNECTMD_ACCOUNT_ERASURE_DB_PASSWORD_SHA256=%s\n' "$erasure_db_hash"
    printf 'CONNECTMD_BACKUP_DB_PASSWORD_SHA256=%s\n' "$backup_db_hash"
    printf 'CONNECTMD_API_KEY_PEPPER_SHA256=%s\n' "$api_key_pepper_hash"
    printf 'CONNECTMD_LIFECYCLE_HMAC_KEY_SHA256=%s\n' "$lifecycle_hmac_hash"
    printf 'CONNECTMD_LIFECYCLE_AEAD_KEY_SHA256=%s\n' "$lifecycle_aead_hash"
    printf 'CONNECTMD_DELETION_WITNESS_HMAC_KEY_SHA256=%s\n' "$witness_hmac_hash"
    printf 'CONNECTMD_DELETION_WITNESS_DIR_SHA256=%s\n' "$witness_dir_hash"
    printf 'CONNECTMD_ACCOUNT_LIFECYCLE_PINNED=%s\n' "${lifecycle_enabled:-false}"
    if [ "${lifecycle_enabled:-false}" = true ]; then
      erasure_meili_hash="$(printf '%s' "$(require_secret_value CONNECTMD_ACCOUNT_ERASURE_MEILI_KEY)" | sha256sum | cut -d' ' -f1)"
      printf 'CONNECTMD_ACCOUNT_ERASURE_MEILI_KEY_SHA256=%s\n' "$erasure_meili_hash"
    fi
  } > "$temporary"
  mv "$temporary" "$RELEASE_ENV_FILE"
}

assert_api_key_pepper_unchanged() {
  local expected actual
  expected="$(grep -E '^CONNECTMD_API_KEY_PEPPER_SHA256=' "$RELEASE_ENV_FILE" | cut -d= -f2-)"
  [ -n "$expected" ] || die "Release credential fingerprint is missing for CONNECTMD_API_KEY_PEPPER"
  actual="$(printf '%s' "$(require_secret_value CONNECTMD_API_KEY_PEPPER)" | sha256sum | cut -d' ' -f1)"
  [ "$actual" = "$expected" ] || die "Stateful release cannot rotate CONNECTMD_API_KEY_PEPPER"
}

assert_recruiting_authority_unchanged() {
  local expected current
  expected="$(grep -E '^CONNECTMD_RECRUITING_ENABLED_PINNED=' "$RELEASE_ENV_FILE" | cut -d= -f2-)"
  [ -n "$expected" ] || die "Release recruiting authority fingerprint is missing"
  case "$expected" in true | false) ;; *) die "Release recruiting authority state is invalid" ;; esac
  current="$(normalize_recruiting_enabled)"
  [ "$current" = "$expected" ] || die "Stateful release cannot change CONNECTMD_RECRUITING_ENABLED"
}

assert_lifecycle_authority_unchanged() {
  local key expected actual expected_enabled current_enabled current_witness_dir
  for key in CONNECTMD_LIFECYCLE_HMAC_KEY CONNECTMD_LIFECYCLE_AEAD_KEY CONNECTMD_DELETION_WITNESS_HMAC_KEY; do
    expected="$(grep -E "^${key}_SHA256=" "$RELEASE_ENV_FILE" | cut -d= -f2-)"
    [ -n "$expected" ] || die "Release credential fingerprint is missing for $key"
    actual="$(printf '%s' "$(require_secret_value "$key")" | sha256sum | cut -d' ' -f1)"
    [ "$actual" = "$expected" ] || die "Lifecycle authority cannot rotate $key"
  done
  expected="$(grep -E '^CONNECTMD_DELETION_WITNESS_DIR_SHA256=' "$RELEASE_ENV_FILE" | cut -d= -f2-)"
  [ -n "$expected" ] || die "Release authority path fingerprint is missing for CONNECTMD_DELETION_WITNESS_DIR"
  current_witness_dir="$(realpath -m "$(read_env_value CONNECTMD_DELETION_WITNESS_DIR)")"
  actual="$(printf '%s' "$current_witness_dir" | sha256sum | cut -d' ' -f1)"
  [ "$actual" = "$expected" ] || die "Lifecycle authority cannot move CONNECTMD_DELETION_WITNESS_DIR"
  expected_enabled="$(grep -E '^CONNECTMD_ACCOUNT_LIFECYCLE_PINNED=' "$RELEASE_ENV_FILE" | cut -d= -f2-)"
  current_enabled="$(read_env_optional_value CONNECTMD_ACCOUNT_LIFECYCLE_ENABLED)"
  current_enabled="${current_enabled:-false}"
  case "$expected_enabled" in true | false) ;; *) die "Release lifecycle activation state is missing or invalid" ;; esac
  if [ "$expected_enabled" = true ] && [ "$current_enabled" != true ]; then
    die "Account lifecycle activation is one-way and cannot be disabled"
  fi
}

assert_stateful_secrets_unchanged() {
  local expected_postgres expected_meili actual_postgres actual_meili key expected actual
  expected_postgres="$(grep -E '^POSTGRES_PASSWORD_SHA256=' "$RELEASE_ENV_FILE" | cut -d= -f2-)"
  expected_meili="$(grep -E '^MEILI_MASTER_KEY_SHA256=' "$RELEASE_ENV_FILE" | cut -d= -f2-)"
  [ -n "$expected_postgres" ] && [ -n "$expected_meili" ] || die "Release credential fingerprints are missing; deploy a fresh release first"
  actual_postgres="$(printf '%s' "$(require_secret_value POSTGRES_PASSWORD)" | sha256sum | cut -d' ' -f1)"
  actual_meili="$(printf '%s' "$(require_secret_value MEILI_MASTER_KEY)" | sha256sum | cut -d' ' -f1)"
  [ "$actual_postgres" = "$expected_postgres" ] || die "Generic reconfigure cannot rotate POSTGRES_PASSWORD"
  [ "$actual_meili" = "$expected_meili" ] || die "Generic reconfigure cannot rotate MEILI_MASTER_KEY"
  for key in CONNECTMD_MEILISEARCH_SEARCH_KEY CONNECTMD_SEARCH_PROJECTION_MEILI_KEY CONNECTMD_MIGRATOR_DB_PASSWORD CONNECTMD_API_DB_PASSWORD CONNECTMD_SEARCH_PROJECTION_DB_PASSWORD CONNECTMD_PROJECTION_ADMIN_DB_PASSWORD CONNECTMD_ACCOUNT_ERASURE_DB_PASSWORD CONNECTMD_BACKUP_DB_PASSWORD; do
    expected="$(grep -E "^${key}_SHA256=" "$RELEASE_ENV_FILE" | cut -d= -f2-)"
    [ -n "$expected" ] || die "Release credential fingerprint is missing for $key"
    actual="$(printf '%s' "$(require_secret_value "$key")" | sha256sum | cut -d' ' -f1)"
    [ "$actual" = "$expected" ] || die "Generic reconfigure cannot rotate $key"
  done
  assert_lifecycle_authority_unchanged
  assert_recruiting_authority_unchanged
  assert_api_key_pepper_unchanged
  if [ "$(read_env_optional_value CONNECTMD_ACCOUNT_LIFECYCLE_ENABLED)" = true ]; then
    key=CONNECTMD_ACCOUNT_ERASURE_MEILI_KEY
    expected="$(grep -E "^${key}_SHA256=" "$RELEASE_ENV_FILE" | cut -d= -f2-)"
    [ -n "$expected" ] || die "Release credential fingerprint is missing for $key"
    actual="$(printf '%s' "$(require_secret_value "$key")" | sha256sum | cut -d' ' -f1)"
    [ "$actual" = "$expected" ] || die "Generic reconfigure cannot rotate $key"
  fi
}

active_image_tag() {
  local value
  if [ -n "${CONNECTMD_IMAGE_TAG:-}" ]; then
    printf '%s' "$CONNECTMD_IMAGE_TAG"
    return
  fi
  load_active_release_identity
  printf '%s' "$RELEASE_IMAGE_TAG"
}

backup_root() {
  local configured configured_root root expected_uid actual_uid root_mode
  configured="${CONNECTMD_BACKUP_DIR:-$(read_env_value CONNECTMD_BACKUP_DIR || printf './backups')}"
  case "$configured" in
    /*) configured_root="$configured" ;;
    *) configured_root="$REPO_ROOT/$configured" ;;
  esac
  [ ! -L "$configured_root" ] || die "CONNECTMD_BACKUP_DIR must not be a symlink"
  root="$(realpath -m "$configured_root")"
  [ "$root" != / ] || die "CONNECTMD_BACKUP_DIR must not be the filesystem root"
  case "$REPO_ROOT" in
    "$root" | "$root"/*)
      die "CONNECTMD_BACKUP_DIR must be a dedicated directory and cannot contain the repository"
      ;;
  esac
  if [ ! -e "$root" ]; then
    # A first-run dedicated root is allowed, but never inherit a permissive
    # shell umask while creating backup and lifecycle authorities.
    (umask 077 && mkdir -p -- "$root") \
      || die "CONNECTMD_BACKUP_DIR could not be created"
  fi
  [ ! -L "$root" ] || die "CONNECTMD_BACKUP_DIR must not be a symlink"
  [ -d "$root" ] || die "CONNECTMD_BACKUP_DIR must be a directory"
  [ "$(realpath -e "$root")" = "$root" ] || die "CONNECTMD_BACKUP_DIR must resolve canonically"
  require_command id
  require_command stat
  require_command uname
  expected_uid="$(id -u)"
  actual_uid="$(stat -c '%u' -- "$root" 2>/dev/null)" \
    || die "CONNECTMD_BACKUP_DIR ownership cannot be inspected"
  [ "$actual_uid" = "$expected_uid" ] \
    || die "CONNECTMD_BACKUP_DIR must be owned by the effective deploy account"
  if [ "$(uname -s)" = "Linux" ]; then
    root_mode="$(stat -c '%a' -- "$root" 2>/dev/null)" \
      || die "CONNECTMD_BACKUP_DIR permissions cannot be inspected"
    [ "$root_mode" = "700" ] \
      || die "CONNECTMD_BACKUP_DIR permissions must be exactly 700 on Linux"
  fi
  printf '%s' "$root"
}

backup_directory() {
  local root candidate
  root="$(backup_root)"
  candidate="$(realpath -e "$1")"
  case "$candidate" in
    "$root"/*) printf '%s' "$candidate" ;;
    *) die "Backup directory must be below CONNECTMD_BACKUP_DIR" ;;
  esac
}

verify_backup() {
  local directory="$1" manifest_names expected_names backup_format
  [ -f "$directory/metadata.env" ] || die "Backup metadata is missing"
  [ -f "$directory/postgres.dump" ] || die "PostgreSQL dump is missing"
  [ -f "$directory/markdown-storage.tar.gz" ] || die "Markdown archive is missing"
  [ -f "$directory/SHA256SUMS" ] || die "Backup checksum manifest is missing"
  for backup_artifact in metadata.env postgres.dump markdown-storage.tar.gz SHA256SUMS; do
    [ ! -L "$directory/$backup_artifact" ] || die "Backup artifact must not be a symlink: $backup_artifact"
  done
  manifest_names="$(awk 'NF == 2 {sub(/^\*/, "", $2); print $2}' "$directory/SHA256SUMS" | LC_ALL=C sort)"
  expected_names="$(printf '%s\n' markdown-storage.tar.gz metadata.env postgres.dump | LC_ALL=C sort)"
  [ "$manifest_names" = "$expected_names" ] || die "Backup checksum manifest must cover exactly the three backup artifacts"
  (
    cd "$directory"
    sha256sum --check --strict --status SHA256SUMS
  ) || die "Backup checksum verification failed"
  backup_format="$(grep -E '^format=' "$directory/metadata.env" | cut -d= -f2-)"
  case "$backup_format" in
    connectmd-backup-v2 | connectmd-backup-v3) ;;
    *) die "Backup format is unsupported" ;;
  esac
  grep -Eq '^created_at=[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$' "$directory/metadata.env" || die "Backup creation timestamp is invalid"
  grep -Eq '^postgres_database=[A-Za-z0-9_]+$' "$directory/metadata.env" || die "Backup database metadata is invalid"
  grep -Eq '^source_revision=[0-9a-f]{40}([0-9a-f]{24})?$' "$directory/metadata.env" || die "Backup source revision is invalid"
  grep -Eq '^api_image_id=sha256:[0-9a-f]{64}$' "$directory/metadata.env" || die "Backup API image identity is invalid"
  grep -Eq '^web_image_id=sha256:[0-9a-f]{64}$' "$directory/metadata.env" || die "Backup web image identity is invalid"
  grep -Eq '^nginx_image_id=sha256:[0-9a-f]{64}$' "$directory/metadata.env" || die "Backup Nginx image identity is invalid"
  grep -Eq '^release_receipt_digest=[0-9a-f]{64}$' "$directory/metadata.env" || die "Backup release receipt digest is invalid"
  if [ "$backup_format" = "connectmd-backup-v3" ]; then
    grep -Eq '^acceptance_receipt_digest=[0-9a-f]{64}$' "$directory/metadata.env" || die "Backup acceptance receipt digest is invalid"
  fi
}
