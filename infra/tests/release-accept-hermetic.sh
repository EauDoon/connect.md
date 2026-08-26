#!/usr/bin/env bash
set -Eeuo pipefail

# Execute the production release-accept.sh bytes against deterministic helper
# and protocol fixtures.  The real lib.sh remains covered by
# acceptance-state-contract.sh; this harness supplies only the external
# command/authority seam that cannot be exercised without a live HTTPS origin.

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
readonly RELEASE_ACCEPT_SOURCE="$REPO_ROOT/infra/scripts/release-accept.sh"
readonly TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/connectmd-release-accept-hermetic.XXXXXX")"
trap 'rm -rf -- "$TEST_ROOT"' EXIT

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

assert_file() {
  [ -f "$1" ] || die "Expected file is missing: $1"
}

assert_not_file() {
  [ ! -e "$1" ] && [ ! -L "$1" ] || die "Unexpected file exists: $1"
}

write_fixtures() {
  local fixture_root="$1"
  mkdir -p "$fixture_root"
  printf 'Connect.md release acceptance root\n' > "$fixture_root/root.body"
  cat > "$fixture_root/agent-readme.md" <<'EOF'
# connect.md agent onboarding README
## Onboarding sequence
Idempotency-Key
If-Match
EOF
  cat > "$fixture_root/openapi.json" <<'EOF'
{"openapi":"3.1.0","paths":{"/v1/profiles":{"post":{}},"/v1/profiles/{handle}":{"put":{}},"/v1/resumes":{"post":{}},"/v1/resumes/{slug}":{"put":{}},"/v1/search":{"get":{}},"/v1/search/query":{"post":{}},"/v1/ingest":{"post":{}}},"components":{"schemas":{"SearchQueryRequest":{"properties":{"agent_capability":{"anyOf":[{"type":"string","const":"internal_contact_request"}]}}},"SearchResponse":{"properties":{"hits":{},"offset":{},"limit":{},"total":{},"indexing_available":{}}}}}}
EOF
  cat > "$fixture_root/llms.txt" <<'EOF'
# connect.md
[OpenAPI](https://connectmd.example.test/openapi.json)
[OAuth protected-resource metadata](https://connectmd.example.test/.well-known/oauth-protected-resource)
[A2A Agent Card](https://connectmd.example.test/.well-known/agent-card.json)
[A2A HTTP+JSON endpoint](https://connectmd.example.test/a2a)
[MCP endpoint](https://connectmd.example.test/mcp)
Content-Type: text/markdown
Idempotency-Key
POST /v1/search/query
EOF
  cat > "$fixture_root/llms-full.txt" <<'EOF'
# connect.md complete agent guide
Base URL: https://connectmd.example.test
OpenAPI: https://connectmd.example.test/openapi.json
POST /mcp
POST /a2a/message:send
GET /v1/search
Idempotency-Key
EOF
  cat > "$fixture_root/agent-card.json" <<'EOF'
{"name":"connect.md","supportedInterfaces":[{"url":"https://connectmd.example.test/a2a","protocolBinding":"HTTP+JSON","protocolVersion":"1.0"}],"documentationUrl":"https://connectmd.example.test/llms-full.txt","capabilities":{"streaming":false,"pushNotifications":false,"extendedAgentCard":false},"skills":[{"id":"search-public-documents"},{"id":"discover-public-taxonomies"},{"id":"discover-public-agents"},{"id":"list-profile-agents"},{"id":"request-mediated-contact"},{"id":"send-mandate-bound-agent-outreach"},{"id":"get-mandate-bound-agent-outreach-status"}]}
EOF
  cat > "$fixture_root/oauth.json" <<'EOF'
{"resource":"https://connectmd.example.test","resource_documentation":"https://connectmd.example.test/docs","bearer_methods_supported":["header"],"scopes_supported":["documents:read","documents:write","search:read","contacts:write"],"authorization_servers":["https://clerk.connectmd.example.test"]}
EOF
  cat > "$fixture_root/mcp-oauth.json" <<'EOF'
{"resource":"https://connectmd.example.test/mcp","authorization_servers":["https://clerk.connectmd.example.test"],"scopes_supported":["documents:read","documents:write","search:read","contacts:write"],"bearer_methods_supported":["header"],"resource_documentation":"https://connectmd.example.test/docs"}
EOF
  cat > "$fixture_root/public-search.json" <<'EOF'
{"hits":[],"offset":0,"limit":1,"total":0,"indexing_available":true,"facets":{},"taxonomy_facets":{},"warning":null}
EOF
  cat > "$fixture_root/exact-search.json" <<'EOF'
{"mode":"exact","hits":[],"offset":0,"limit":1,"total":0,"indexing_available":true,"complete":true,"search_revision":1,"next_cursor":null,"facets":{},"taxonomy_facets":{},"warning":null}
EOF
  cat > "$fixture_root/mcp-initialize.json" <<'EOF'
{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2025-06-18","serverInfo":{"name":"connect.md"},"capabilities":{"tools":{"listChanged":false}}}}
EOF
  cat > "$fixture_root/mcp-tools.json" <<'EOF'
{"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"list_taxonomies"},{"name":"list_taxonomy_terms"},{"name":"search_documents"},{"name":"get_agent_identity"},{"name":"list_profile_agents"},{"name":"list_agent_directory"},{"name":"read_document"},{"name":"list_my_documents"},{"name":"get_changes"},{"name":"update_document"},{"name":"create_document"},{"name":"propose_document_update"},{"name":"send_agent_outreach"},{"name":"get_agent_outreach_status"}]}}
EOF
  cat > "$fixture_root/mcp-search.json" <<'EOF'
{"jsonrpc":"2.0","id":3,"result":{"structuredContent":{"hits":[],"offset":0,"limit":1,"total":0,"indexing_available":true,"facets":{},"taxonomy_facets":{},"warning":null}}}
EOF
  cat > "$fixture_root/a2a-search.json" <<'EOF'
{"task":{"status":{"state":"TASK_STATE_COMPLETED"},"artifacts":[{"parts":[{"data":{"hits":[],"offset":0,"limit":1,"total":0,"indexing_available":true,"facets":{},"taxonomy_facets":{},"warning":null}}]}]}}
EOF
}

write_helper_shim() {
  local root="$1"
  mkdir -p "$root/infra/scripts" "$root/infra/nginx/conf.d" "$root/backups/.connectmd-lifecycle"
  cp "$RELEASE_ACCEPT_SOURCE" "$root/infra/scripts/release-accept.sh"
  cp "$REPO_ROOT/infra/nginx/conf.d/connectmd.tls.conf" "$root/infra/nginx/conf.d/connectmd.tls.conf"
  : > "$root/compose.yaml"
  : > "$root/compose.prod.yaml"
  cat > "$root/infra/scripts/lib.sh" <<'SHIM'
#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="${FAKE_ROOT:?}"
ENV_FILE="$REPO_ROOT/.env"
RELEASE_ENV_FILE="$REPO_ROOT/.connectmd-release.env"
STAGED_RELEASE_FILE="$REPO_ROOT/.connectmd-staged-release.env"
FAKE_FIXTURE_DIR="${FAKE_FIXTURE_DIR:?}"
FAKE_LOG="$REPO_ROOT/events.log"

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
log_event() { printf '%s\n' "$1" >> "$FAKE_LOG"; }
require_command() { :; }
ensure_repo() { :; }
ensure_clean_source() { :; }
acquire_operation_lock() { :; }
validate_production_env() { :; }
assert_direct_system_trust_environment() { :; }
require_hostname() { printf '%s' 'connectmd.example.test'; }
read_env_optional_value() {
  case "$1" in
    CONNECTMD_ACCOUNT_LIFECYCLE_ENABLED|NEXT_PUBLIC_ACCOUNT_LIFECYCLE_ENABLED) printf '%s' false ;;
    *) return 1 ;;
  esac
}
current_source_revision() { printf '%s' '0123456789abcdef0123456789abcdef01234567'; }
digest_of_file() { sha256sum -- "$1" | awk '{print $1}'; }

run_with_direct_system_trust() {
  local command_name="${1:-}"
  shift || true
  if [ "$command_name" = openssl ]; then
    case "${1:-}" in
      s_client) printf 'hermetic-tls-leaf\n' ;;
      x509) cat ;;
      *) return 1 ;;
    esac
    return 0
  fi
  [ "$command_name" = curl ] || return 1

  local output='' headers='' write_out='' data='' target='' path='' fixture='' argument
  while [ "$#" -gt 0 ]; do
    argument="$1"
    case "$argument" in
      --output|-o|--dump-header|--write-out|--data|--data-raw|--data-binary|--header|--proto)
        [ "$#" -ge 2 ] || return 2
        case "$argument" in
          --output|-o) output="$2" ;;
          --dump-header) headers="$2" ;;
          --write-out) write_out="$2" ;;
          --data|--data-raw|--data-binary) data="$2" ;;
        esac
        shift 2
        ;;
      --*) shift ;;
      *) target="$argument"; shift ;;
    esac
  done
  if [[ "$target" == http://* ]]; then
    [ -z "$headers" ] || printf 'HTTP/1.1 301 Moved Permanently\r\nLocation: https://connectmd.example.test/\r\n\r\n' > "$headers"
    [ -n "$write_out" ] && printf '301'
    return 0
  fi
  [ "$target" = https://connectmd.example.test ] && path=/ || path="${target#https://connectmd.example.test}"
  [ -n "$path" ] || path=/
  if [ "${FAKE_MODE:-success}" = transport-failure ] && [ "$path" = /v1/search/query ]; then
    return 22
  fi
  if [ "${FAKE_MODE:-success}" = missing-probe ] && [ "$path" = /llms-full.txt ]; then
    [ -z "$headers" ] || printf 'HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\n' > "$headers"
    [ -n "$write_out" ] && printf '200'
    return 0
  fi
  if [ "${FAKE_MODE:-success}" = malformed-protocol ] && [ "$path" = /mcp ] && [[ "$data" == *'"tools/call"'* ]]; then
    printf '{"jsonrpc":"2.0","id":3,"result":{}}' > "$output"
    printf 'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nMCP-Protocol-Version: 2025-06-18\r\n\r\n' > "$headers"
    return 0
  fi
  case "$path" in
    /) fixture=root.body ;;
    /agent-readme.md) fixture=agent-readme.md ;;
    /openapi.json) fixture=openapi.json ;;
    /llms.txt) fixture=llms.txt ;;
    /llms-full.txt) fixture=llms-full.txt ;;
    /.well-known/agent-card.json) fixture=agent-card.json ;;
    /.well-known/oauth-protected-resource) fixture=oauth.json ;;
    /.well-known/oauth-protected-resource/mcp) fixture=mcp-oauth.json ;;
    /v1/search/query)
      if [[ "$data" == *'"mode":"exact"'* ]]; then fixture=exact-search.json; else fixture=public-search.json; fi
      ;;
    /mcp)
      case "$data" in
        *'"method":"initialize"'*) fixture=mcp-initialize.json ;;
        *'"method":"tools/list"'*) fixture=mcp-tools.json ;;
        *) fixture=mcp-search.json ;;
      esac
      ;;
    /a2a/message:send) fixture=a2a-search.json ;;
    *) return 22 ;;
  esac
  [ -n "$output" ] && [ "$output" != /dev/null ] && cp "$FAKE_FIXTURE_DIR/$fixture" "$output"
  if [ -n "$headers" ]; then
    if [ "$path" = / ]; then
      csp="$(sed -n 's/^[[:space:]]*add_header Content-Security-Policy "\(.*\)" always;$/\1/p' "$REPO_ROOT/infra/nginx/conf.d/connectmd.tls.conf")"
      csp="${csp//__CONNECTMD_DOMAIN__/connectmd.example.test}"
      printf 'HTTP/1.1 200 OK\r\nStrict-Transport-Security: max-age=31536000; includeSubDomains\r\nContent-Security-Policy: %s\r\nX-Connectmd-Release-Tag: ci-hermetic\r\n\r\n' "$csp" > "$headers"
    elif [ "$path" = /agent-readme.md ]; then
      printf 'HTTP/1.1 200 OK\r\nContent-Type: text/markdown\r\n\r\n' > "$headers"
    else
      printf 'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nMCP-Protocol-Version: 2025-06-18\r\n\r\n' > "$headers"
    fi
  fi
  [ -n "$write_out" ] && printf '200'
  return 0
}

service_is_running() { return 0; }
assert_service_image_identity() { :; }
wait_for_service() { :; }
wait_for_profiled_service() { :; }
assert_release_images_match() { :; }
active_marker_digest_or_none() { printf '%s' none; }
load_active_release_identity() { :; }
load_release_acceptance() { :; }

load_staged_release() {
  STAGED_SOURCE_REVISION="$(current_source_revision)"
  STAGED_IMAGE_TAG=ci-hermetic
  STAGED_API_IMAGE_ID=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
  STAGED_WEB_IMAGE_ID=sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
  STAGED_NGINX_IMAGE_ID=sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc
  STAGED_RELEASE_RECEIPT_DIGEST=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
  STAGED_PRIOR_ACCEPTED_MARKER_DIGEST=none
  STAGED_RELEASE_DIGEST=dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd
}

write_release_acceptance() {
  local source="$1" image_tag="$2" api_id="$3" web_id="$4" nginx_id="$5" release_digest="$6" evidence_input="$7"
  local root="$REPO_ROOT/backups/.connectmd-lifecycle/release-acceptance"
  local evidence="$root/acceptance-$image_tag-$STAGED_RELEASE_DIGEST.evidence"
  local receipt="$root/acceptance-$image_tag-$STAGED_RELEASE_DIGEST.env"
  mkdir -p "$root"
  cp "$evidence_input" "$evidence"
  chmod 600 "$evidence"
  printf 'format=connectmd-release-acceptance-evidence-v2\nsource_revision=%s\nimage_tag=%s\napi_image_id=%s\nweb_image_id=%s\nnginx_image_id=%s\nrelease_receipt_digest=%s\nstage_digest=%s\nevidence_digest=%s\nhttps_origin=https://connectmd.example.test\naccepted_at=2026-01-01T00:00:00Z\n' \
    "$source" "$image_tag" "$api_id" "$web_id" "$nginx_id" "$release_digest" "$STAGED_RELEASE_DIGEST" "$(digest_of_file "$evidence_input")" > "$receipt"
  chmod 600 "$receipt"
  log_event write_release_acceptance
  printf '%s' "$receipt"
}

persist_image_tag() {
  log_event persist_image_tag
  printf 'accepted\n' > "$REPO_ROOT/active-marker"
}

clear_staged_release_after_acceptance() {
  log_event clear_staged_release_after_acceptance
  rm -f -- "$STAGED_RELEASE_FILE"
}

clear_matching_completed_restore_state() { :; }
SHIM
  chmod +x "$root/infra/scripts/release-accept.sh" "$root/infra/scripts/lib.sh"
}

prepare_case() {
  local root="$1"
  write_helper_shim "$root"
  write_fixtures "$root/fixtures"
  if ! command -v python3 >/dev/null 2>&1 && [ -x "$REPO_ROOT/apps/api/.venv/Scripts/python.exe" ]; then
    mkdir -p "$root/bin"
    cat > "$root/bin/python3" <<EOF
#!/usr/bin/env bash
exec "$REPO_ROOT/apps/api/.venv/Scripts/python.exe" "\$@"
EOF
    chmod +x "$root/bin/python3"
  fi
  printf 'fixture\n' > "$root/.env"
  printf 'fixture-stage\n' > "$root/.connectmd-staged-release.env"
  chmod 600 "$root/.env" "$root/.connectmd-staged-release.env"
  cmp -s "$RELEASE_ACCEPT_SOURCE" "$root/infra/scripts/release-accept.sh" \
    || die 'Hermetic harness did not execute byte-identical production release-accept.sh'
}

run_case() {
  local name="$1" mode="$2" expected_status="$3" root="$TEST_ROOT/$1" output status
  mkdir -p "$root"
  prepare_case "$root"
  output="$root/run.output"
  set +e
  PATH="$root/bin:$PATH" FAKE_ROOT="$root" FAKE_FIXTURE_DIR="$root/fixtures" FAKE_MODE="$mode" \
    bash "$root/infra/scripts/release-accept.sh" --yes-accept > "$output" 2>&1
  status=$?
  set -e
  if [ "$status" -ne "$expected_status" ]; then
    cat "$output" >&2
    die "$name returned status $status; expected $expected_status"
  fi
  if [ "$expected_status" -eq 0 ]; then
    grep -Fxq 'ACCEPTED_IMAGE_TAG=ci-hermetic' "$output" \
      || die "$name did not report the accepted image tag"
    assert_file "$root/active-marker"
    assert_file "$root/backups/.connectmd-lifecycle/release-acceptance/acceptance-ci-hermetic-dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd.env"
    assert_not_file "$root/.connectmd-staged-release.env"
    expected_events=$'write_release_acceptance\npersist_image_tag\nclear_staged_release_after_acceptance'
    [ "$(cat "$root/events.log")" = "$expected_events" ] \
      || die "$name acceptance mutation ordering changed"
  else
    assert_not_file "$root/active-marker"
    assert_not_file "$root/backups/.connectmd-lifecycle/release-acceptance"
    assert_file "$root/.connectmd-staged-release.env"
  fi
}

run_case success success 0
run_case missing-probe missing-probe 1
run_case malformed-protocol malformed-protocol 1
run_case transport-failure transport-failure 22
printf 'release-accept hermetic: PASS (success, missing probe, malformed protocol, pre-mutation transport failure)\n'
