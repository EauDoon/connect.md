#!/usr/bin/env bash
# Hermetic exact-search secret preflight coverage. No Docker or network access.
set -Eeuo pipefail

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
readonly LIBRARY="$REPO_ROOT/infra/scripts/lib.sh"

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
expect_rejected() {
  local description="$1" value="$2" ttl="${3:-900}"
  if bash -c 'source "$1"; validate_exact_search_cursor_authority' bash "$fixture/infra/scripts/lib.sh"; then
    die "Counterexample unexpectedly passed: $description"
  fi
}

scratch="$(mktemp -d)"
cleanup() { local status=$?; trap - EXIT; rm -rf -- "$scratch"; exit "$status"; }
trap cleanup EXIT
fixture="$scratch/repository"
mkdir -p "$fixture/infra/scripts"
cp "$LIBRARY" "$fixture/infra/scripts/lib.sh"

write_env() {
  printf 'CONNECTMD_EXACT_SEARCH_CURSOR_KEYRING=%s\nCONNECTMD_EXACT_SEARCH_CURSOR_TTL_SECONDS=%s\n' "$1" "$2" > "$fixture/.env"
  chmod 600 "$fixture/.env"
}

valid_secret=Y2ktZXhhY3Qtc2VhcmNoLWN1cnNvci1zZWNyZXQtMDAw
second_secret=Y2ktZXhhY3Qtc2VhcmNoLWN1cnNvci1zZWNyZXQtMDAx
write_env "[{\"kid\":\"v1\",\"secret\":\"$valid_secret\"}]" 900
bash -c 'source "$1"; validate_exact_search_cursor_authority' bash "$fixture/infra/scripts/lib.sh"

for counterexample in \
  '[]' \
  '[{"kid":"v1","secret":"c2hvcnQ"}]' \
  '[{"kid":"v1","secret":"Y2ktZXhhY3Qtc2VhcmNoLWN1cnNvci1zZWNyZXQtMDAw","extra":true}]' \
  '[{"kid":"v1","secret":"Y2ktZXhhY3Qtc2VhcmNoLWN1cnNvci1zZWNyZXQtMDAw"},{"kid":"v1","secret":"Y2ktZXhhY3Qtc2VhcmNoLWN1cnNvci1zZWNyZXQtMDAx"}]' \
  '[{"kid":"v1","secret":"Y2ktZXhhY3Qtc2VhcmNoLWN1cnNvci1zZWNyZXQtMDAw"},{"kid":"v2","secret":"Y2ktZXhhY3Qtc2VhcmNoLWN1cnNvci1zZWNyZXQtMDAx"},{"kid":"v3","secret":"Y2ktZXhhY3Qtc2VhcmNoLWN1cnNvci1zZWNyZXQtMDAw"},{"kid":"v4","secret":"Y2ktZXhhY3Qtc2VhcmNoLWN1cnNvci1zZWNyZXQtMDAx"}]'
do
  write_env "$counterexample" 900
  expect_rejected "invalid keyring" "$counterexample"
done

write_env '[{"kid":"v1","secret":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA+A"}]' 900
expect_rejected "standard Base64 plus alphabet" ignored
write_env '[{"kid":"v1","secret":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA/A"}]' 900
expect_rejected "standard Base64 slash alphabet" ignored

write_env "[{\"kid\":\"v1\",\"secret\":\"$valid_secret\"},{\"kid\":\"v2\",\"secret\":\"$second_secret\"}]" 59
expect_rejected "TTL below lower bound" ignored 59
write_env "[{\"kid\":\"v1\",\"secret\":\"$valid_secret\"}]" 3601
expect_rejected "TTL above upper bound" ignored 3601

printf 'EXACT_SEARCH_ENV_CONTRACT=PASS\n'
