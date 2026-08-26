#!/usr/bin/env bash
# Hermetic hostname preflight coverage. No Docker or network access.
set -Eeuo pipefail

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
readonly LIBRARY="$REPO_ROOT/infra/scripts/lib.sh"
readonly NGINX_ENTRYPOINT="$REPO_ROOT/infra/nginx/docker-entrypoint.d/10-select-connectmd-config.sh"

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

temp_root="${TMPDIR:-/tmp}"
scratch="$(mktemp -d "$temp_root/connectmd-hostname-contract.XXXXXX")"
cleanup() {
  local status=$?
  trap - EXIT
  case "$scratch" in
    "$temp_root"/connectmd-hostname-contract.*) rm -rf -- "$scratch" ;;
    *) printf 'ERROR: Refusing unsafe test cleanup: %s\n' "$scratch" >&2; status=1 ;;
  esac
  exit "$status"
}
trap cleanup EXIT

fixture="$scratch/repository"
mkdir -p "$fixture/infra/scripts"
cp "$LIBRARY" "$fixture/infra/scripts/lib.sh"

write_env() {
  printf 'CONNECTMD_DOMAIN=%s\n' "$1" > "$fixture/.env"
}

expect_library_accepts() {
  local domain="$1" output
  write_env "$domain"
  output="$(bash -c 'source "$1"; require_hostname' bash "$fixture/infra/scripts/lib.sh")" \
    || die "Valid hostname was rejected: $domain"
  [ "$output" = "$domain" ] || die "Hostname validator changed the accepted value: $domain"
}

expect_rejected() {
  local domain="$1" output
  write_env "$domain"
  if output="$(bash -c 'source "$1"; require_hostname' bash "$fixture/infra/scripts/lib.sh" 2>&1)"; then
    die "Operational hostname validator accepted: $domain"
  fi
  printf '%s\n' "$output" | grep -Fq 'must be a valid lowercase DNS hostname' \
    || die "Operational hostname validator returned the wrong failure: $domain"

  if output="$(CONNECTMD_DOMAIN="$domain" CONNECTMD_RELEASE_TAG=local sh "$NGINX_ENTRYPOINT" 2>&1)"; then
    die "Nginx hostname validator accepted: $domain"
  fi
  printf '%s\n' "$output" | grep -Fq 'is not a valid lowercase DNS hostname' \
    || die "Nginx hostname validator returned the wrong failure: $domain"
}

label_63="$(printf 'a%.0s' {1..63})"
label_64="${label_63}a"
expect_library_accepts connectmd.example.test
expect_library_accepts "$label_63.example"

for invalid_domain in \
  -bad.example \
  bad-.example \
  Example.COM \
  bad_name.example \
  .bad.example \
  bad.example. \
  bad..example \
  "$label_64.example" \
  "$label_63.$label_63.$label_63.$label_63"
do
  expect_rejected "$invalid_domain"
done

printf 'HOSTNAME_CONTRACT=PASS\n'
