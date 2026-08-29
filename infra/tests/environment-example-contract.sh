#!/usr/bin/env bash
# Hermetic production-placeholder and lifecycle-default coverage. No Docker or network access.
set -Eeuo pipefail

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
readonly LIBRARY="$REPO_ROOT/infra/scripts/lib.sh"

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

temp_root="${TMPDIR:-/tmp}"
scratch="$(mktemp -d "$temp_root/connectmd-environment-contract.XXXXXX")"
cleanup() {
  local status=$?
  trap - EXIT
  case "$scratch" in
    "$temp_root"/connectmd-environment-contract.*) rm -rf -- "$scratch" ;;
    *) printf 'ERROR: Refusing unsafe test cleanup: %s\n' "$scratch" >&2; status=1 ;;
  esac
  exit "$status"
}
trap cleanup EXIT

fixture="$scratch/repository"
mkdir -p "$fixture/infra/scripts"
cp "$LIBRARY" "$fixture/infra/scripts/lib.sh"
cp "$REPO_ROOT/.env.example" "$fixture/.env"

for placeholder_key in \
  POSTGRES_PASSWORD \
  CONNECTMD_MIGRATOR_DB_PASSWORD \
  CONNECTMD_API_DB_PASSWORD \
  CONNECTMD_PROJECTION_ADMIN_DB_PASSWORD \
  CONNECTMD_ACCOUNT_ERASURE_DB_PASSWORD \
  CONNECTMD_BACKUP_DB_PASSWORD \
  MEILI_MASTER_KEY \
  CONNECTMD_MEILISEARCH_SEARCH_KEY \
  CONNECTMD_SEARCH_PROJECTION_MEILI_KEY \
  CONNECTMD_SEARCH_PROJECTION_DB_PASSWORD \
  CONNECTMD_CLERK_JWKS_URL \
  CONNECTMD_CLERK_ISSUER \
  CONNECTMD_CLERK_AUTHORIZED_PARTIES \
  CONNECTMD_API_KEY_PEPPER \
  CONNECTMD_VERIFICATION_REVIEWER_ID \
  CONNECTMD_POST_MODERATOR_ID \
  CONNECTMD_APPEAL_REVIEWER_ID \
  NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY \
  CLERK_SECRET_KEY \
  CONNECTMD_DOMAIN \
  ACME_EMAIL \
  CONNECTMD_PUBLIC_BASE_URL \
  NEXT_PUBLIC_SITE_URL \
  CONNECTMD_LIFECYCLE_HMAC_KEY \
  CONNECTMD_LIFECYCLE_AEAD_KEY \
  CONNECTMD_DELETION_WITNESS_HMAC_KEY \
  CONNECTMD_CLERK_BACKEND_SECRET
do
  if output="$(bash -c 'source "$1"; require_secret_value "$2"' bash "$fixture/infra/scripts/lib.sh" "$placeholder_key" 2>&1)"; then
    die ".env.example placeholder unexpectedly passed production preflight: $placeholder_key=$output"
  fi
  printf '%s\n' "$output" | grep -Fq "$placeholder_key still has an example value" \
    || die ".env.example placeholder returned the wrong failure: $placeholder_key"
done

if ! bash -c 'source "$1"; validate_clerk_secret_key "$2"' bash "$fixture/infra/scripts/lib.sh" 'sk_test_1234567890123456'; then
  die "A well-formed Clerk secret key was rejected"
fi
for malformed_clerk_secret in \
  sk_test_short \
  sk_bad_1234567890123456 \
  sk_live_123456789012345!
do
  if output="$(bash -c 'source "$1"; validate_clerk_secret_key "$2"' bash "$fixture/infra/scripts/lib.sh" "$malformed_clerk_secret" 2>&1)"; then
    die "Malformed Clerk secret key unexpectedly passed production preflight"
  fi
  printf '%s\n' "$output" | grep -Fq 'CLERK_SECRET_KEY must be a well-formed Clerk secret key' \
    || die "Malformed Clerk secret key returned the wrong failure"
done

for valid_authorized_parties in \
  '["https://connect.example.test"]' \
  '["https://admin.example.test","https://connect.example.test"]'
do
  bash -c 'source "$1"; validate_clerk_authorized_site_origin "$2" "$3"' bash \
    "$fixture/infra/scripts/lib.sh" "$valid_authorized_parties" 'https://connect.example.test' \
    || die "Clerk authorized parties rejected the canonical site origin"
done
for invalid_authorized_parties in \
  '["https://other.example.test"]' \
  '["https://connect.example.test/"]' \
  '["https://connect.example.test",7]' \
  'not-json'
do
  if output="$(bash -c 'source "$1"; validate_clerk_authorized_site_origin "$2" "$3"' bash \
    "$fixture/infra/scripts/lib.sh" "$invalid_authorized_parties" 'https://connect.example.test' 2>&1)"; then
    die "Invalid Clerk authorized parties unexpectedly passed production preflight"
  fi
  printf '%s\n' "$output" | grep -Fq 'CONNECTMD_CLERK_AUTHORIZED_PARTIES must include the canonical NEXT_PUBLIC_SITE_URL origin' \
    || die "Invalid Clerk authorized parties returned the wrong failure"
  if printf '%s\n' "$output" | grep -Fq "$invalid_authorized_parties"; then
    die "Invalid Clerk authorized parties were echoed by production preflight"
  fi
done
if output="$(PYTHONOPTIMIZE=1 bash -c 'source "$1"; validate_clerk_authorized_site_origin "$2" "$3"' bash \
  "$fixture/infra/scripts/lib.sh" '["https://other.example.test"]' 'https://connect.example.test' 2>&1)"; then
  die "Optimized Python unexpectedly accepted Clerk authorized parties without the site origin"
fi
printf '%s\n' "$output" | grep -Fq 'CONNECTMD_CLERK_AUTHORIZED_PARTIES must include the canonical NEXT_PUBLIC_SITE_URL origin' \
  || die "Optimized Python returned the wrong Clerk authorized-party failure"

canonical_public_origin='https://connect.example.test'
for valid_site_origin in \
  'https://connect.example.test' \
  'https://connect-md.vercel.app'
do
  bash -c 'source "$1"; validate_canonical_https_origin "$2" NEXT_PUBLIC_SITE_URL' bash \
    "$fixture/infra/scripts/lib.sh" "$valid_site_origin" \
    || die "A canonical split frontend origin was rejected"
done
for invalid_site_origin in \
  'http://connect.example.test' \
  'https://Connect.example.test' \
  'https://connect.example.test/' \
  'https://connect.example.test:443' \
  'https://user@connect.example.test' \
  'https://connect.example.test/path'
do
  if output="$(bash -c 'source "$1"; validate_canonical_https_origin "$2" NEXT_PUBLIC_SITE_URL' bash \
    "$fixture/infra/scripts/lib.sh" "$invalid_site_origin" 2>&1)"; then
    die "A noncanonical frontend origin unexpectedly passed production preflight"
  fi
  printf '%s\n' "$output" | grep -Fq 'NEXT_PUBLIC_SITE_URL must be a canonical HTTPS origin' \
    || die "A noncanonical frontend origin returned the wrong failure"
done
for valid_api_base in '' "$canonical_public_origin"; do
  bash -c 'source "$1"; validate_public_api_base "$2" "$3"' bash \
    "$fixture/infra/scripts/lib.sh" "$valid_api_base" "$canonical_public_origin" \
    || die "A blank or canonical NEXT_PUBLIC_API_BASE_URL was rejected"
done
for invalid_api_base in \
  'http://connect.example.test' \
  'https://other.example.test' \
  'https://user:pass@connect.example.test' \
  'https://connect.example.test/' \
  'https://connect.example.test:443' \
  'https://connect.example.test:8443' \
  'https://connect.example.test//' \
  'https://connect.example.test/path' \
  'https://connect.example.test?query=1' \
  'https://connect.example.test#fragment' \
  'HTTPS://connect.example.test' \
  'https://CONNECT.example.test' \
  ' https://connect.example.test' \
  '/v1' \
  'javascript:alert(1)' \
  'not-a-url'
do
  if output="$(bash -c 'source "$1"; validate_public_api_base "$2" "$3"' bash \
    "$fixture/infra/scripts/lib.sh" "$invalid_api_base" "$canonical_public_origin" 2>&1)"; then
    die "Invalid NEXT_PUBLIC_API_BASE_URL unexpectedly passed production preflight: $invalid_api_base"
  fi
  printf '%s\n' "$output" | grep -Fq 'NEXT_PUBLIC_API_BASE_URL must be empty or exactly the canonical CONNECTMD_PUBLIC_BASE_URL HTTPS origin' \
    || die "Invalid NEXT_PUBLIC_API_BASE_URL returned the wrong failure: $invalid_api_base"
  if printf '%s\n' "$output" | grep -Fq "$invalid_api_base"; then
    die "Invalid NEXT_PUBLIC_API_BASE_URL was echoed by production preflight: $invalid_api_base"
  fi
done

if output="$(bash -c 'unset NEXT_PUBLIC_API_BASE_URL; source "$1"; validate_public_api_base_environment_override "$2"' bash \
  "$fixture/infra/scripts/lib.sh" "$canonical_public_origin" 2>&1)"; then
  :
else
  die "An absent NEXT_PUBLIC_API_BASE_URL environment override was rejected: $output"
fi
if output="$(NEXT_PUBLIC_API_BASE_URL="$canonical_public_origin" bash -c 'source "$1"; validate_public_api_base_environment_override "$2"' bash \
  "$fixture/infra/scripts/lib.sh" "$canonical_public_origin" 2>&1)"; then
  :
else
  die "A matching NEXT_PUBLIC_API_BASE_URL environment override was rejected: $output"
fi
for invalid_api_override in \
  'http://connect.example.test' \
  'https://other.example.test' \
  'https://user:pass@connect.example.test' \
  'https://connect.example.test/' \
  'https://connect.example.test:8443' \
  'https://connect.example.test//' \
  'https://connect.example.test/path' \
  'https://connect.example.test?query=1' \
  'https://connect.example.test#fragment' \
  'HTTPS://connect.example.test' \
  'https://CONNECT.example.test' \
  ' https://connect.example.test' \
  '/v1' \
  'not-a-url'
do
  if output="$(NEXT_PUBLIC_API_BASE_URL="$invalid_api_override" bash -c 'source "$1"; validate_public_api_base_environment_override "$2"' bash \
    "$fixture/infra/scripts/lib.sh" "$canonical_public_origin" 2>&1)"; then
    die "An inherited NEXT_PUBLIC_API_BASE_URL override unexpectedly passed: $invalid_api_override"
  fi
  printf '%s\n' "$output" | grep -Fq 'NEXT_PUBLIC_API_BASE_URL environment override must match .env' \
    || die "An invalid NEXT_PUBLIC_API_BASE_URL override returned the wrong failure: $invalid_api_override"
  if printf '%s\n' "$output" | grep -Fq "$invalid_api_override"; then
    die "An invalid NEXT_PUBLIC_API_BASE_URL override was echoed: $invalid_api_override"
  fi
done

readonly PARITY_FIXTURE="$scratch/process-environment"
mkdir -p "$PARITY_FIXTURE/infra/scripts"
cp "$LIBRARY" "$PARITY_FIXTURE/infra/scripts/lib.sh"
readonly PARITY_LIBRARY="$PARITY_FIXTURE/infra/scripts/lib.sh"

write_parity_env() {
  printf '%s\n' "$@" > "$PARITY_FIXTURE/.env"
  chmod 600 "$PARITY_FIXTURE/.env"
}

run_parity_guard() {
  env -i "$@" "PATH=$PATH" bash -c 'source "$1"; assert_env_file_matches_process_environment' bash "$PARITY_LIBRARY"
}

expect_parity_rejected() {
  local description="$1" expected="$2" forbidden_one="$3" forbidden_two="$4" output
  shift 4
  if output="$(run_parity_guard "$@" 2>&1)"; then
    die "Counterexample unexpectedly passed: $description"
  fi
  printf '%s\n' "$output" | grep -Fq "$expected" \
    || die "Counterexample returned the wrong failure: $description"
  for forbidden in "$forbidden_one" "$forbidden_two"; do
    if [ -n "$forbidden" ] && printf '%s\n' "$output" | grep -Fq -- "$forbidden"; then
      die "Counterexample echoed a value: $description"
    fi
  done
}

write_parity_env \
  'CONNECTMD_ACCOUNT_LIFECYCLE_ENABLED=false' \
  'NEXT_PUBLIC_ACCOUNT_LIFECYCLE_ENABLED=false' \
  'NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test_contract_value' \
  'CLERK_SECRET_KEY=sk_test_contract_value' \
  'NEXT_PUBLIC_SITE_URL=https://connect.example.test'
if ! output="$(run_parity_guard \
  -u CONNECTMD_ACCOUNT_LIFECYCLE_ENABLED \
  -u NEXT_PUBLIC_ACCOUNT_LIFECYCLE_ENABLED \
  -u NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY \
  -u CLERK_SECRET_KEY \
  -u NEXT_PUBLIC_SITE_URL 2>&1)"; then
  die "Absent process-environment values were rejected: $output"
fi
if ! output="$(run_parity_guard \
  CONNECTMD_ACCOUNT_LIFECYCLE_ENABLED=false \
  NEXT_PUBLIC_ACCOUNT_LIFECYCLE_ENABLED=false \
  NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test_contract_value \
  CLERK_SECRET_KEY=sk_test_contract_value \
  NEXT_PUBLIC_SITE_URL=https://connect.example.test 2>&1)"; then
  die "Matching process-environment values were rejected: $output"
fi
if ! output="$(run_parity_guard \
  CONNECTMD_IMAGE_TAG=accepted-release \
  CONNECTMD_COMPOSE_PROJECT_NAME=connectmd 2>&1)"; then
  die "Operational process-environment values absent from .env were rejected: $output"
fi
expect_parity_rejected \
  'API lifecycle override' \
  'CONNECTMD_ACCOUNT_LIFECYCLE_ENABLED environment override must match .env' \
  true false \
  CONNECTMD_ACCOUNT_LIFECYCLE_ENABLED=true
expect_parity_rejected \
  'frontend lifecycle override' \
  'NEXT_PUBLIC_ACCOUNT_LIFECYCLE_ENABLED environment override must match .env' \
  true false \
  NEXT_PUBLIC_ACCOUNT_LIFECYCLE_ENABLED=true
expect_parity_rejected \
  'lifecycle API/frontend pair' \
  'NEXT_PUBLIC_ACCOUNT_LIFECYCLE_ENABLED environment override must match .env' \
  true false \
  CONNECTMD_ACCOUNT_LIFECYCLE_ENABLED=false \
  NEXT_PUBLIC_ACCOUNT_LIFECYCLE_ENABLED=true
expect_parity_rejected \
  'secret override' \
  'CLERK_SECRET_KEY environment override must match .env' \
  process-secret file-secret \
  CLERK_SECRET_KEY=process-secret
expect_parity_rejected \
  'public build override' \
  'NEXT_PUBLIC_ACCOUNT_LIFECYCLE_ENABLED environment override must match .env' \
  true false \
  NEXT_PUBLIC_ACCOUNT_LIFECYCLE_ENABLED=true

write_parity_env \
  'CONNECTMD_SAFE_VALUE=stable' \
  'MALFORMED-KEY=malformed-value'
expect_parity_rejected \
  'malformed .env key' \
  '.env contains an invalid variable name' \
  malformed-value MALFORMED-KEY

write_parity_env \
  'CONNECTMD_DUPLICATE=first-value' \
  'CONNECTMD_DUPLICATE=second-value'
expect_parity_rejected \
  'duplicate .env key' \
  '.env contains a duplicate variable name' \
  first-value second-value

valid_clerk_publishable_test='pk_test_Zm9vLWJhci0xLmNsZXJrLmFjY291bnRzLmRldiQ='
valid_clerk_publishable_live='pk_live_Y2xlcmsuZXhhbXBsZS5jb20k'
for valid_clerk_publishable in "$valid_clerk_publishable_test" "$valid_clerk_publishable_live"; do
  bash -c 'source "$1"; validate_clerk_publishable_key "$2"' bash "$fixture/infra/scripts/lib.sh" "$valid_clerk_publishable" \
    || die "A well-formed Clerk publishable key was rejected"
done
for malformed_clerk_publishable in \
  pk_test_short \
  pk_stage_Zm9vLmJhciQ= \
  'pk_test_@@@@' \
  pk_test_bm90LWhvc3Qk \
  pk_test_Zm9vLi5iYXI=
do
  if output="$(bash -c 'source "$1"; validate_clerk_publishable_key "$2"' bash "$fixture/infra/scripts/lib.sh" "$malformed_clerk_publishable" 2>&1)"; then
    die "Malformed Clerk publishable key unexpectedly passed production preflight"
  fi
  printf '%s\n' "$output" | grep -Fq 'NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY must be a well-formed Clerk publishable key' \
    || die "Malformed Clerk publishable key returned the wrong failure"
  if printf '%s\n' "$output" | grep -Fq "$malformed_clerk_publishable"; then
    die "Malformed Clerk publishable key was echoed by production preflight"
  fi
done
if output="$(PYTHONOPTIMIZE=1 bash -c 'source "$1"; validate_clerk_publishable_key "$2"' bash "$fixture/infra/scripts/lib.sh" pk_test_Zm9vLi5iYXI= 2>&1)"; then
  die "Optimized Python unexpectedly accepted a malformed Clerk publishable key"
fi
printf '%s\n' "$output" | grep -Fq 'NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY must be a well-formed Clerk publishable key' \
  || die "Optimized Python returned the wrong publishable-key failure"
if printf '%s\n' "$output" | grep -Fq 'pk_test_Zm9vLi5iYXI='; then
  die "Optimized Python publishable-key validation echoed the malformed value"
fi

grep -Fxq 'POSTGRES_USER=postgres' "$REPO_ROOT/.env.example" \
  || die "The cluster bootstrap identity must remain operator-only postgres"

grep -Fxq 'CONNECTMD_ACCOUNT_LIFECYCLE_ENABLED=false' "$REPO_ROOT/.env.example" \
  || die "API account lifecycle must remain disabled in .env.example"
grep -Fxq 'NEXT_PUBLIC_ACCOUNT_LIFECYCLE_ENABLED=false' "$REPO_ROOT/.env.example" \
  || die "Frontend account lifecycle must remain disabled in .env.example"
grep -Fxq 'CONNECTMD_ACCOUNT_ERASURE_MEILI_KEY=' "$REPO_ROOT/.env.example" \
  || die "Disabled lifecycle must not receive an erasure Meilisearch key"

printf 'CONNECTMD_CLERK_JWKS_URL=https://tenant.clerk.accounts.dev/.well-known/jwks.json\n' > "$fixture/.env"
valid_clerk_url="$(bash -c 'source "$1"; require_secret_value CONNECTMD_CLERK_JWKS_URL' bash "$fixture/infra/scripts/lib.sh")" \
  || die "A non-example Clerk JWKS URL was rejected"
[ "$valid_clerk_url" = 'https://tenant.clerk.accounts.dev/.well-known/jwks.json' ] \
  || die "A valid Clerk JWKS URL was changed"

printf 'CONNECTMD_CLERK_JWKS_URL=https://myexample.clerk.accounts.dev/.well-known/jwks.json\n' > "$fixture/.env"
similar_clerk_url="$(bash -c 'source "$1"; require_secret_value CONNECTMD_CLERK_JWKS_URL' bash "$fixture/infra/scripts/lib.sh")" \
  || die "A Clerk hostname containing a non-placeholder tenant was rejected"
[ "$similar_clerk_url" = 'https://myexample.clerk.accounts.dev/.well-known/jwks.json' ] \
  || die "A non-placeholder Clerk JWKS URL was changed"

printf 'ENVIRONMENT_EXAMPLE_CONTRACT=PASS\n'
