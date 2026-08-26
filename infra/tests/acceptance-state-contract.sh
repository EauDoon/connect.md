#!/usr/bin/env bash
# Hermetic record-contract coverage. This never invokes Docker, curl, DNS, or
# TLS: public-origin evidence belongs solely to release-accept.sh on a fresh
# dedicated connect.md host.
set -Eeuo pipefail

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
readonly LIBRARY="$REPO_ROOT/infra/scripts/lib.sh"

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
expect_rejected() {
  local description="$1"
  shift
  if ( "$@" ) >/dev/null 2>&1; then
    die "Counterexample unexpectedly passed: $description"
  fi
}

assert_release_accept_direct_trust_contract() {
  local source="$1" curl_calls
  grep -Fq 'assert_direct_system_trust_environment' "$source" || die "Release acceptance does not reject inherited trust overrides"
  grep -Fq 'run_with_direct_system_trust openssl s_client' "$source" || die "OpenSSL TLS probe is not sanitized"
  grep -Fq 'run_with_direct_system_trust openssl x509' "$source" || die "OpenSSL leaf conversion is not sanitized"
  grep -Fq 'DIRECT_SYSTEM_COMMAND_PATH' "$LIBRARY" || die "Sanitized release child does not reset PATH"
  if grep -Eq '^[[:space:]]*curl[[:space:]]' "$source"; then
    die "Release acceptance contains an unsanitized direct curl invocation"
  fi
  curl_calls="$(awk '
    /run_with_direct_system_trust[[:space:]]+curl[[:space:]]/ {
      count += 1
      for (field = 1; field <= NF; field += 1) {
        if ($field == "curl") {
          if ($(field + 1) != "-q" && $(field + 1) != "--disable") exit 1
        }
      }
    }
    END { if (count < 3) exit 1; print count }
  ' "$source")" || die "Every release curl must disable default config before other options"
  [ -n "$curl_calls" ] || die "Release acceptance curl coverage is missing"
}

assert_release_accept_csp_contract() {
  local source="$1" staged_binding_line csp_assignment_line csp_gate_line mutation_line
  grep -Fq 'template="$REPO_ROOT/infra/nginx/conf.d/connectmd.tls.conf"' "$source" || die "Release acceptance is detached from the staged frontend CSP template"
  grep -Fq "sed -n 's/^[[:space:]]*add_header Content-Security-Policy \"\\(.*\\)\" always;\$/\\1/p' \"\$template\"" "$source" || die "Release acceptance does not derive the CSP from the staged template"
  grep -Fq "*'__CONNECTMD_DOMAIN__'*)" "$source" || die "Release acceptance does not require a domain-bound staged CSP template"
  grep -Fq 'expected_frontend_csp="$(expected_frontend_csp "$domain")"' "$source" || die "Release acceptance does not bind the CSP to the validated domain"
  grep -Fq 'grep -Fxq "Content-Security-Policy: $expected_frontend_csp" "$workdir/root.headers.normalized" || die "Public HTTPS origin did not return the required Content-Security-Policy"' "$source" || die "Release acceptance does not require the exact public Content-Security-Policy"
  if grep -Fq "default-src" "$source"; then
    die "Release acceptance must not hard-code a CSP instead of using the staged template"
  fi
  staged_binding_line="$(grep -nF '[ "$(current_source_revision)" = "$STAGED_SOURCE_REVISION" ]' "$source" | cut -d: -f1)"
  csp_assignment_line="$(grep -nF 'expected_frontend_csp="$(expected_frontend_csp "$domain")"' "$source" | cut -d: -f1)"
  csp_gate_line="$(grep -nF 'grep -Fxq "Content-Security-Policy: $expected_frontend_csp"' "$source" | cut -d: -f1)"
  mutation_line="$(grep -nF 'acceptance_receipt="$(write_release_acceptance' "$source" | cut -d: -f1)"
  [ -n "$staged_binding_line" ] && [ -n "$csp_assignment_line" ] && [ -n "$csp_gate_line" ] && [ -n "$mutation_line" ] && [ "$(printf '%s\n' "$staged_binding_line" | wc -l | tr -d '[:space:]')" = 1 ] && [ "$(printf '%s\n' "$csp_assignment_line" | wc -l | tr -d '[:space:]')" = 1 ] && [ "$(printf '%s\n' "$csp_gate_line" | wc -l | tr -d '[:space:]')" = 1 ] && [ "$(printf '%s\n' "$mutation_line" | wc -l | tr -d '[:space:]')" = 1 ] || die "Release acceptance CSP gate anchors are ambiguous"
  [ "$staged_binding_line" -lt "$csp_assignment_line" ] || die "Release acceptance CSP template is not bound to the staged source"
  [ "$csp_gate_line" -lt "$mutation_line" ] || die "Release acceptance CSP gate occurs after an acceptance mutation"
}

grep -Fq 'readonly STAGED_RELEASE_FILE=' "$LIBRARY" || die "Staged release record is missing"
grep -Fq 'connectmd-staged-release-v1' "$LIBRARY" || die "Staged release format is missing"
grep -Fq 'connectmd-release-acceptance-v1' "$LIBRARY" || die "Acceptance receipt format is missing"
grep -Fq 'connectmd-release-acceptance-v2' "$LIBRARY" || die "Exact-search acceptance receipt format is missing"
grep -Fq 'connectmd-release-acceptance-evidence-v2' "$LIBRARY" || die "Exact-search acceptance evidence format is missing"
grep -Fq 'CONNECTMD_RELEASE_FORMAT=connectmd-release-v3' "$LIBRARY" || die "Accepted v3 marker is missing"
grep -Fq 'CONNECTMD_ACCEPTANCE_RECEIPT_SHA256=' "$LIBRARY" || die "Accepted marker does not bind acceptance evidence"
grep -Fq 'recruiting_enabled=' "$LIBRARY" || die "Release receipts do not bind recruiting state"
grep -Fq 'CONNECTMD_RECRUITING_ENABLED_PINNED=' "$LIBRARY" || die "Active marker does not bind recruiting state"
grep -Fq 'Backup release recruiting state does not match .env' "$REPO_ROOT/infra/scripts/restore.sh" || die "Restore preflight does not bind recruiting authority"
grep -Fq 'assert_no_pending_staged_release' "$REPO_ROOT/infra/scripts/update.sh" || die "Update does not reject staged authority"
grep -Fq 'assert_no_pending_staged_release' "$REPO_ROOT/infra/scripts/backup.sh" || die "Backup does not reject staged authority"
grep -Fq 'assert_no_pending_staged_release' "$REPO_ROOT/infra/scripts/reconfigure.sh" || die "Reconfigure does not reject staged authority"
grep -Fq 'Recruiting enablement requires a newly staged and accepted release' "$REPO_ROOT/infra/scripts/reconfigure.sh" || die "Reconfigure can enable recruiting without staged accepted authority"
grep -Fq 'Pending staged release can roll back only to its prior accepted target' "$REPO_ROOT/infra/scripts/rollback.sh" || die "Rollback target is not constrained while staged"
grep -Fq 'Rollback release recruiting state does not match .env' "$REPO_ROOT/infra/scripts/rollback.sh" || die "Rollback does not bind recruiting authority"
grep -Fq 'Rollback did not restore the exact prior accepted marker authority' "$LIBRARY" || die "Rollback does not recheck the exact prior marker authority"
grep -Fq -- '--yes-accept' "$REPO_ROOT/infra/scripts/release-accept.sh" || die "Explicit acceptance confirmation is missing"
grep -Fq 'Active marker acceptance authority does not match this staged release' "$REPO_ROOT/infra/scripts/release-accept.sh" || die "Acceptance retry does not bind the active marker to the staged receipt"
assert_release_accept_direct_trust_contract "$REPO_ROOT/infra/scripts/release-accept.sh"
assert_release_accept_csp_contract "$REPO_ROOT/infra/scripts/release-accept.sh"
if grep -Eq -- '--(resolve|insecure|cacert)' "$REPO_ROOT/infra/scripts/release-accept.sh"; then
  die "Live release acceptance contains a TLS or name-resolution bypass"
fi
grep -Fq '[ "$restore_backup_format" = "connectmd-backup-v3" ]' "$REPO_ROOT/infra/scripts/deploy.sh" || die "V2 restore can reuse accepted authority"
grep -Fq '[ "$RELEASE_ACCEPTANCE_DIGEST" = "$restore_backup_acceptance_digest" ]' "$REPO_ROOT/infra/scripts/deploy.sh" || die "V3 restore relaunch is not bound to its recorded acceptance authority"

scratch="$(mktemp -d)"
cleanup() { local status=$?; trap - EXIT; rm -rf -- "$scratch"; exit "$status"; }
trap cleanup EXIT

csp_removed="$scratch/release-accept.csp-removed.sh"
cp "$REPO_ROOT/infra/scripts/release-accept.sh" "$csp_removed"
sed -i '/Content-Security-Policy: \$expected_frontend_csp/d' "$csp_removed"
expect_rejected 'missing public CSP gate' assert_release_accept_csp_contract "$csp_removed"

csp_post_mutation="$scratch/release-accept.csp-post-mutation.sh"
awk '
  /Content-Security-Policy: \$expected_frontend_csp/ { next }
  { print }
  /^acceptance_receipt="\$\(write_release_acceptance/ {
    print "grep -Fxq \"Content-Security-Policy: $expected_frontend_csp\" \"$workdir/root.headers.normalized\" || die \"Public HTTPS origin did not return the required Content-Security-Policy\""
  }
' "$REPO_ROOT/infra/scripts/release-accept.sh" > "$csp_post_mutation"
expect_rejected 'post-mutation public CSP gate' assert_release_accept_csp_contract "$csp_post_mutation"

csp_hard_coded="$scratch/release-accept.csp-hard-coded.sh"
awk '
  index($0, "expected_frontend_csp=\"$(expected_frontend_csp \"$domain\")\"") {
    print "expected_frontend_csp=\"default-src '\''self'\''\""
    next
  }
  { print }
' "$REPO_ROOT/infra/scripts/release-accept.sh" > "$csp_hard_coded"
expect_rejected 'hard-coded weakened CSP' assert_release_accept_csp_contract "$csp_hard_coded"

csp_detached_template="$scratch/release-accept.csp-detached-template.sh"
cp "$REPO_ROOT/infra/scripts/release-accept.sh" "$csp_detached_template"
sed -i 's|REPO_ROOT/infra/nginx/conf.d/connectmd.tls.conf|REPO_ROOT/infra/nginx/conf.d/detached.conf|' "$csp_detached_template"
expect_rejected 'detached staged CSP template' assert_release_accept_csp_contract "$csp_detached_template"

fixture="$scratch/repository"
mkdir -p "$fixture/infra/scripts" "$fixture/backups/.connectmd-lifecycle/releases" "$fixture/backups/.connectmd-lifecycle/release-acceptance"
cp "$LIBRARY" "$fixture/infra/scripts/lib.sh"
chmod 700 "$fixture/backups" "$fixture/backups/.connectmd-lifecycle" "$fixture/backups/.connectmd-lifecycle/releases" "$fixture/backups/.connectmd-lifecycle/release-acceptance"
printf 'CONNECTMD_BACKUP_DIR=%s\nCONNECTMD_DOMAIN=acceptance.example.test\nCONNECTMD_RECRUITING_ENABLED=false\n' "$fixture/backups" > "$fixture/.env"
chmod 600 "$fixture/.env"
if [ "$(stat -c '%a' "$fixture/.env")" != 600 ]; then
  printf 'ACCEPTANCE_STATE_CONTRACT=SKIPPED_NO_POSIX_MODE_SUPPORT\n'
  exit 0
fi

for trust_variable in CURL_CA_BUNDLE SSL_CERT_FILE SSL_CERT_DIR OPENSSL_CONF HTTPS_PROXY RES_OPTIONS; do
  expect_rejected "custom direct-trust environment: $trust_variable" env "$trust_variable=override" bash -c 'source "$1"; assert_direct_system_trust_environment' bash "$fixture/infra/scripts/lib.sh"
done
fake_bin="$scratch/direct-trust-fake-bin"
mkdir -p "$fake_bin"
for command_name in curl openssl; do
  printf '#!/usr/bin/env bash\nprintf "FAKE_%s_RAN\\n"\nexit 99\n' "$command_name" > "$fake_bin/$command_name"
  chmod 700 "$fake_bin/$command_name"
done
for command_name in curl openssl; do
  if ! fake_output="$(PATH="$fake_bin:$PATH" bash -c 'source "$1"; run_with_direct_system_trust "$2" --version' bash "$fixture/infra/scripts/lib.sh" "$command_name" 2>&1)"; then
    die "Sanitized direct-trust child could not execute system $command_name"
  fi
  case "$fake_output" in *"FAKE_${command_name}_RAN"*) die "Caller PATH substituted $command_name" ;; esac
done
missing_q="$scratch/release-accept.missing-q.sh"
nonfirst_q="$scratch/release-accept.nonfirst-q.sh"
cp "$REPO_ROOT/infra/scripts/release-accept.sh" "$missing_q"
sed -i '0,/curl -q/s//curl --fail/' "$missing_q"
expect_rejected 'curl missing first -q' assert_release_accept_direct_trust_contract "$missing_q"
cp "$REPO_ROOT/infra/scripts/release-accept.sh" "$nonfirst_q"
sed -i '0,/curl -q/s//curl --fail -q/' "$nonfirst_q"
expect_rejected 'curl non-first -q' assert_release_accept_direct_trust_contract "$nonfirst_q"

source_revision="0123456789abcdef0123456789abcdef01234567"
image_tag="fixture-tag"
api_id="sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
web_id="sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
nginx_id="sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
release="$fixture/backups/.connectmd-lifecycle/releases/release-$image_tag.env"
{
  printf 'format=connectmd-release-receipt-v1\nsource_revision=%s\nimage_tag=%s\napi_image_id=%s\nweb_image_id=%s\nnginx_image_id=%s\nrecruiting_enabled=false\nrecorded_at=2026-01-01T00:00:00Z\n' "$source_revision" "$image_tag" "$api_id" "$web_id" "$nginx_id"
} > "$release"
chmod 600 "$release"
release_digest="$(sha256sum "$release" | awk '{print $1}')"
stage="$fixture/.connectmd-staged-release.env"
{
  printf 'format=connectmd-staged-release-v1\nsource_revision=%s\nimage_tag=%s\napi_image_id=%s\nweb_image_id=%s\nnginx_image_id=%s\nrecruiting_enabled=false\nrelease_receipt_digest=%s\nprior_accepted_marker_digest=none\nstaged_at=2026-01-01T00:00:00Z\n' "$source_revision" "$image_tag" "$api_id" "$web_id" "$nginx_id" "$release_digest"
} > "$stage"
chmod 600 "$stage"
stage_digest="$(sha256sum "$stage" | awk '{print $1}')"

bash -c 'source "$1"; load_staged_release >/dev/null' bash "$fixture/infra/scripts/lib.sh"
cp "$stage" "$stage.legacy"; sed -i '/^recruiting_enabled=/d' "$stage.legacy"; chmod 600 "$stage.legacy"
expect_rejected 'legacy unbound staged release' bash -c 'source "$1"; validate_staged_release "$2" >/dev/null' bash "$fixture/infra/scripts/lib.sh" "$stage.legacy"
cp "$release" "$release.legacy"; sed -i '/^recruiting_enabled=/d' "$release.legacy"; chmod 600 "$release.legacy"
expect_rejected 'legacy unbound release receipt' bash -c 'source "$1"; validate_release_receipt "$2" "$3" "$4" "$5" "$6" "$7" >/dev/null' bash "$fixture/infra/scripts/lib.sh" "$release.legacy" "$source_revision" "$image_tag" "$api_id" "$web_id" "$nginx_id"

cp "$fixture/.env" "$fixture/.env.saved"
sed -i 's/^CONNECTMD_RECRUITING_ENABLED=.*/CONNECTMD_RECRUITING_ENABLED=true/' "$fixture/.env"
expect_rejected 'recruiting toggle after staging' env -u CONNECTMD_RECRUITING_ENABLED bash -c 'source "$1"; validate_staged_release "$2" >/dev/null' bash "$fixture/infra/scripts/lib.sh" "$stage"
mv "$fixture/.env.saved" "$fixture/.env"
cp "$stage" "$stage.duplicate"; printf 'image_tag=%s\n' "$image_tag" >> "$stage.duplicate"; chmod 600 "$stage.duplicate"
expect_rejected 'duplicate stage key' bash -c 'source "$1"; validate_staged_release "$2" >/dev/null' bash "$fixture/infra/scripts/lib.sh" "$stage.duplicate"
cp "$stage" "$stage.unknown"; printf 'unexpected=value\n' >> "$stage.unknown"; chmod 600 "$stage.unknown"
expect_rejected 'unknown stage key' bash -c 'source "$1"; validate_staged_release "$2" >/dev/null' bash "$fixture/infra/scripts/lib.sh" "$stage.unknown"
ln -s "$stage" "$stage.symlink"
expect_rejected 'symlink stage record' bash -c 'source "$1"; validate_staged_release "$2" >/dev/null' bash "$fixture/infra/scripts/lib.sh" "$stage.symlink"
cp "$stage" "$stage.mode"; chmod 644 "$stage.mode"
expect_rejected 'broad-mode stage record' bash -c 'source "$1"; validate_staged_release "$2" >/dev/null' bash "$fixture/infra/scripts/lib.sh" "$stage.mode"
cp "$stage" "$stage.substituted"; sed -i 's/^release_receipt_digest=.*/release_receipt_digest=dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd/' "$stage.substituted"; chmod 600 "$stage.substituted"
expect_rejected 'substituted release digest' bash -c 'source "$1"; validate_staged_release "$2" >/dev/null' bash "$fixture/infra/scripts/lib.sh" "$stage.substituted"

selector_bin="$scratch/release-selector-bin"
mkdir -p "$selector_bin"
{
  printf '%s\n' '#!/usr/bin/env bash'
  printf 'if [ "$1" = "-C" ] && [ "$3" = "rev-parse" ] && [ "$4" = "--verify" ] && [ "$5" = "HEAD" ]; then\n'
  printf '  printf "%%s\\n" "%s"\n' "$source_revision"
  printf 'else\n  exit 97\nfi\n'
} > "$selector_bin/git"
{
  printf '%s\n' '#!/usr/bin/env bash'
  printf 'if [ "$1" != "image" ] || [ "$2" != "inspect" ]; then exit 97; fi\n'
  printf 'case "$*" in\n'
  printf '  *connectmd-api:%s*) printf "%%s\\n" "%s" ;;\n' "$image_tag" "$api_id"
  printf '  *connectmd-web:%s*) printf "%%s\\n" "%s" ;;\n' "$image_tag" "$web_id"
  printf '  *connectmd-nginx:%s*) printf "%%s\\n" "%s" ;;\n' "$image_tag" "$nginx_id"
  printf '  *sha256:*) exit 0 ;;\n'
  printf '  *) exit 97 ;;\n'
  printf 'esac\n'
} > "$selector_bin/docker"
chmod 700 "$selector_bin/git" "$selector_bin/docker"

staged_selection="$(env -u CONNECTMD_IMAGE_TAG PATH="$selector_bin:$PATH" bash -c 'source "$1"; select_release_image_tag staged-or-accepted; printf "SELECTED=%s\n" "$CONNECTMD_IMAGE_TAG"' bash "$fixture/infra/scripts/lib.sh")"
[ "$staged_selection" = "SELECTED=$image_tag" ] || die "Staged release selector did not select the staged tag"

mv "$stage" "$stage.saved"
expect_rejected 'neither accepted nor staged release identity' env -u CONNECTMD_IMAGE_TAG PATH="$selector_bin:$PATH" bash -c 'source "$1"; select_release_image_tag staged-or-accepted' bash "$fixture/infra/scripts/lib.sh"
mv "$stage.saved" "$stage"

expect_rejected 'inherited nonempty CONNECTMD_IMAGE_TAG' env PATH="$selector_bin:$PATH" CONNECTMD_IMAGE_TAG=attacker-tag bash -c 'source "$1"; select_release_image_tag staged-or-accepted' bash "$fixture/infra/scripts/lib.sh"
expect_rejected 'inherited empty CONNECTMD_IMAGE_TAG' env PATH="$selector_bin:$PATH" CONNECTMD_IMAGE_TAG= bash -c 'source "$1"; select_release_image_tag staged-or-accepted' bash "$fixture/infra/scripts/lib.sh"

cp "$stage" "$stage.original"
sed -i 's/^api_image_id=.*/api_image_id=sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd/' "$stage"
expect_rejected 'repointed staged image identity' env -u CONNECTMD_IMAGE_TAG PATH="$selector_bin:$PATH" bash -c 'source "$1"; select_release_image_tag staged-or-accepted' bash "$fixture/infra/scripts/lib.sh"
mv "$stage.original" "$stage"

cp "$stage" "$stage.original"
sed -i 's/^source_revision=.*/source_revision=fedcba9876543210fedcba9876543210fedcba98/' "$stage"
expect_rejected 'staged source mismatch' env -u CONNECTMD_IMAGE_TAG PATH="$selector_bin:$PATH" bash -c 'source "$1"; select_release_image_tag staged-or-accepted' bash "$fixture/infra/scripts/lib.sh"
mv "$stage.original" "$stage"

evidence="$fixture/backups/.connectmd-lifecycle/release-acceptance/acceptance-$image_tag-$stage_digest.evidence"
for key in tls_leaf_sha256 http_redirect_sha256 hsts_sha256 openapi_sha256 llms_sha256 llms_full_sha256 agent_card_sha256 oauth_sha256 mcp_oauth_sha256 public_search_sha256 mcp_initialize_tools_search_sha256 a2a_search_sha256; do
  printf '%s=%064d\n' "$key" 0
done > "$evidence"
sed -i '1ihttps_origin=https://acceptance.example.test' "$evidence"
chmod 600 "$evidence"
evidence_digest="$(sha256sum "$evidence" | awk '{print $1}')"
acceptance="$fixture/backups/.connectmd-lifecycle/release-acceptance/acceptance-$image_tag-$stage_digest.env"
{
  printf 'format=connectmd-release-acceptance-v1\nsource_revision=%s\nimage_tag=%s\napi_image_id=%s\nweb_image_id=%s\nnginx_image_id=%s\nrecruiting_enabled=false\nrelease_receipt_digest=%s\nstage_digest=%s\nhttps_origin=https://acceptance.example.test\n' "$source_revision" "$image_tag" "$api_id" "$web_id" "$nginx_id" "$release_digest" "$stage_digest"
  for key in tls_leaf_sha256 http_redirect_sha256 hsts_sha256 openapi_sha256 llms_sha256 llms_full_sha256 agent_card_sha256 oauth_sha256 mcp_oauth_sha256 public_search_sha256 mcp_initialize_tools_search_sha256 a2a_search_sha256; do printf '%s=%064d\n' "$key" 0; done
  printf 'evidence_digest=%s\naccepted_at=2026-01-01T00:00:00Z\n' "$evidence_digest"
} > "$acceptance"
chmod 600 "$acceptance"
acceptance_digest="$(sha256sum "$acceptance" | awk '{print $1}')"
marker="$fixture/.connectmd-release.env"
{
  printf 'CONNECTMD_RELEASE_FORMAT=connectmd-release-v3\nCONNECTMD_SOURCE_REVISION=%s\nCONNECTMD_IMAGE_TAG=%s\nCONNECTMD_API_IMAGE_ID=%s\nCONNECTMD_WEB_IMAGE_ID=%s\nCONNECTMD_NGINX_IMAGE_ID=%s\nCONNECTMD_RECRUITING_ENABLED_PINNED=false\nCONNECTMD_RELEASE_RECEIPT_SHA256=%s\nCONNECTMD_ACCEPTANCE_RECEIPT_SHA256=%s\n' "$source_revision" "$image_tag" "$api_id" "$web_id" "$nginx_id" "$release_digest" "$acceptance_digest"
  for key in POSTGRES_PASSWORD CONNECTMD_MIGRATOR_DB_PASSWORD CONNECTMD_API_DB_PASSWORD CONNECTMD_SEARCH_PROJECTION_DB_PASSWORD CONNECTMD_PROJECTION_ADMIN_DB_PASSWORD CONNECTMD_ACCOUNT_ERASURE_DB_PASSWORD CONNECTMD_BACKUP_DB_PASSWORD MEILI_MASTER_KEY CONNECTMD_MEILISEARCH_SEARCH_KEY CONNECTMD_SEARCH_PROJECTION_MEILI_KEY CONNECTMD_API_KEY_PEPPER CONNECTMD_LIFECYCLE_HMAC_KEY CONNECTMD_LIFECYCLE_AEAD_KEY CONNECTMD_DELETION_WITNESS_HMAC_KEY CONNECTMD_DELETION_WITNESS_DIR; do printf '%s_SHA256=%064d\n' "$key" 0; done
  printf 'CONNECTMD_ACCOUNT_LIFECYCLE_PINNED=false\n'
} > "$marker"
chmod 600 "$marker"
bash -c 'source "$1"; load_active_release_identity >/dev/null' bash "$fixture/infra/scripts/lib.sh"

mv "$stage" "$stage.pending"
accepted_selection="$(env -u CONNECTMD_IMAGE_TAG PATH="$selector_bin:$PATH" bash -c 'source "$1"; select_release_image_tag accepted-only; printf "SELECTED=%s\n" "$CONNECTMD_IMAGE_TAG"' bash "$fixture/infra/scripts/lib.sh")"
[ "$accepted_selection" = "SELECTED=$image_tag" ] || die "Accepted release selector did not select the recorded tag"
mv "$stage.pending" "$stage"

# Historical v1 evidence remains valid, while new v2 receipts must bind the
# additional exact-search public probe without weakening the strict key set.
v2_tag="fixture-tag-v2"
v2_release="$fixture/backups/.connectmd-lifecycle/releases/release-$v2_tag.env"
{
  printf 'format=connectmd-release-receipt-v1\nsource_revision=%s\nimage_tag=%s\napi_image_id=%s\nweb_image_id=%s\nnginx_image_id=%s\nrecruiting_enabled=false\nrecorded_at=2026-01-01T00:00:02Z\n' "$source_revision" "$v2_tag" "$api_id" "$web_id" "$nginx_id"
} > "$v2_release"
chmod 600 "$v2_release"
v2_release_digest="$(sha256sum "$v2_release" | awk '{print $1}')"
v2_stage_digest="$(printf 'v2-stage' | sha256sum | awk '{print $1}')"
v2_evidence="$fixture/backups/.connectmd-lifecycle/release-acceptance/acceptance-$v2_tag-$v2_stage_digest.evidence"
{
  printf 'format=connectmd-release-acceptance-evidence-v2\nhttps_origin=https://acceptance.example.test\n'
  for key in tls_leaf_sha256 http_redirect_sha256 hsts_sha256 openapi_sha256 llms_sha256 llms_full_sha256 agent_card_sha256 oauth_sha256 mcp_oauth_sha256 public_search_sha256 exact_search_sha256 mcp_initialize_tools_search_sha256 a2a_search_sha256; do printf '%s=%064d\n' "$key" 0; done
} > "$v2_evidence"
chmod 600 "$v2_evidence"
v2_evidence_digest="$(sha256sum "$v2_evidence" | awk '{print $1}')"
v2_acceptance="$fixture/backups/.connectmd-lifecycle/release-acceptance/acceptance-$v2_tag-$v2_stage_digest.env"
{
  printf 'format=connectmd-release-acceptance-v2\nsource_revision=%s\nimage_tag=%s\napi_image_id=%s\nweb_image_id=%s\nnginx_image_id=%s\nrecruiting_enabled=false\nrelease_receipt_digest=%s\nstage_digest=%s\nhttps_origin=https://acceptance.example.test\n' "$source_revision" "$v2_tag" "$api_id" "$web_id" "$nginx_id" "$v2_release_digest" "$v2_stage_digest"
  for key in tls_leaf_sha256 http_redirect_sha256 hsts_sha256 openapi_sha256 llms_sha256 llms_full_sha256 agent_card_sha256 oauth_sha256 mcp_oauth_sha256 public_search_sha256 exact_search_sha256 mcp_initialize_tools_search_sha256 a2a_search_sha256; do printf '%s=%064d\n' "$key" 0; done
  printf 'evidence_digest=%s\naccepted_at=2026-01-01T00:00:02Z\n' "$v2_evidence_digest"
} > "$v2_acceptance"
chmod 600 "$v2_acceptance"
bash -c 'source "$1"; validate_acceptance_receipt "$2" "$3" "$4" "$5" "$6" "$7"' bash "$fixture/infra/scripts/lib.sh" "$v2_acceptance" "$source_revision" "$v2_tag" "$api_id" "$web_id" "$nginx_id"
cp "$v2_evidence" "$v2_evidence.missing-exact"
sed -i '/^exact_search_sha256=/d' "$v2_evidence.missing-exact"
chmod 600 "$v2_evidence.missing-exact"
expect_rejected 'v2 evidence missing exact-search digest' bash -c 'source "$1"; validate_acceptance_evidence "$2"' bash "$fixture/infra/scripts/lib.sh" "$v2_evidence.missing-exact"
expect_rejected 'rollback prior marker digest mismatch' bash -c 'source "$1"; discard_staged_release_after_rollback "$2" none "$3" "$4" "$5" "$6" "$7" "$8"' bash "$fixture/infra/scripts/lib.sh" "$stage_digest" "$source_revision" "$image_tag" "$api_id" "$web_id" "$nginx_id"
cp "$marker" "$marker.substituted"; sed -i 's/^CONNECTMD_ACCEPTANCE_RECEIPT_SHA256=.*/CONNECTMD_ACCEPTANCE_RECEIPT_SHA256=eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee/' "$marker.substituted"; chmod 600 "$marker.substituted"
bad_fixture="$scratch/bad-repository"
cp -a "$fixture" "$bad_fixture"
cp "$marker.substituted" "$bad_fixture/.connectmd-release.env"
chmod 600 "$bad_fixture/.connectmd-release.env"
expect_rejected 'v3 acceptance digest substitution' bash -c 'source "$1"; load_active_release_identity >/dev/null' bash "$bad_fixture/infra/scripts/lib.sh"
cp "$marker" "$marker.legacy"; sed -i '/^CONNECTMD_RECRUITING_ENABLED_PINNED=/d' "$marker.legacy"; chmod 600 "$marker.legacy"
legacy_marker_fixture="$scratch/legacy-marker-repository"
cp -a "$fixture" "$legacy_marker_fixture"
cp "$marker.legacy" "$legacy_marker_fixture/.connectmd-release.env"
chmod 600 "$legacy_marker_fixture/.connectmd-release.env"
expect_rejected 'legacy unbound active marker' bash -c 'source "$1"; load_active_release_identity >/dev/null' bash "$legacy_marker_fixture/infra/scripts/lib.sh"

# A repeated staging of the same immutable image tag receives a different stage
# digest, so both immutable acceptance receipts can coexist without collision.
stage_two="$fixture/.connectmd-staged-release-second.env"
cp "$stage" "$stage_two"
sed -i 's/^staged_at=.*/staged_at=2026-01-01T00:00:01Z/' "$stage_two"
chmod 600 "$stage_two"
stage_two_digest="$(sha256sum "$stage_two" | awk '{print $1}')"
evidence_two="$fixture/backups/.connectmd-lifecycle/release-acceptance/acceptance-$image_tag-$stage_two_digest.evidence"
acceptance_two="$fixture/backups/.connectmd-lifecycle/release-acceptance/acceptance-$image_tag-$stage_two_digest.env"
cp "$evidence" "$evidence_two"
chmod 600 "$evidence_two"
cp "$acceptance" "$acceptance_two"
sed -i "s/^stage_digest=.*/stage_digest=$stage_two_digest/; s/^accepted_at=.*/accepted_at=2026-01-01T00:00:01Z/" "$acceptance_two"
chmod 600 "$acceptance_two"
bash -c 'source "$1"; load_release_acceptance "$2" "" "$3" >/dev/null' bash "$fixture/infra/scripts/lib.sh" "$image_tag" "$stage_two_digest"
[ "$(bash -c 'source "$1"; load_release_acceptance "$2"' bash "$fixture/infra/scripts/lib.sh" "$image_tag")" = "$acceptance_two" ] || die "Stage-scoped acceptance history did not select the newest same-tag receipt"

printf 'ACCEPTANCE_STATE_CONTRACT=PASS\n'
