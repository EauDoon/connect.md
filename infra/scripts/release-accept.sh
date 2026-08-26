#!/usr/bin/env bash
# Explicitly promote a locally healthy staged candidate only after a direct,
# publicly trusted HTTPS protocol check.  This script intentionally has no
# host override, custom CA, or insecure-TLS escape hatch.
set -Eeuo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/lib.sh"

[ "$#" -eq 1 ] && [ "$1" = "--yes-accept" ] || die "Usage: ${0##*/} --yes-accept"
ensure_repo
acquire_operation_lock
ensure_clean_source
validate_production_env
assert_direct_system_trust_environment
require_command curl
require_command openssl
require_command python3
require_command sha256sum

if [ ! -e "$STAGED_RELEASE_FILE" ] && [ ! -L "$STAGED_RELEASE_FILE" ]; then
  # The only post-promotion durable action is clearing a matching completed
  # restore state. Treat that final window as retryable rather than forcing an
  # operator to recreate a stage or rewrite immutable evidence.
  load_active_release_identity
  load_release_acceptance "$RELEASE_IMAGE_TAG" "$RELEASE_ACCEPTANCE_DIGEST" >/dev/null
  clear_matching_completed_restore_state "$RELEASE_SOURCE_REVISION" "$RELEASE_IMAGE_TAG" "$RELEASE_API_IMAGE_ID" "$RELEASE_WEB_IMAGE_ID" "$RELEASE_NGINX_IMAGE_ID" "$RELEASE_RECEIPT_DIGEST"
  printf 'ACCEPTED_IMAGE_TAG=%s\n' "$RELEASE_IMAGE_TAG"
  printf 'ACCEPTANCE_RECEIPT=%s\n' "$ACCEPTANCE_RECEIPT_PATH"
  exit 0
fi

load_staged_release >/dev/null
[ "$(current_source_revision)" = "$STAGED_SOURCE_REVISION" ] || die "Checked-out source revision does not match the staged release"
current_recruiting_enabled="$(normalize_recruiting_enabled)"
[ "$STAGED_RECRUITING_ENABLED" = "$current_recruiting_enabled" ] || die "Staged release recruiting state does not match .env"
assert_release_images_match "$STAGED_IMAGE_TAG" "$STAGED_API_IMAGE_ID" "$STAGED_WEB_IMAGE_ID" "$STAGED_NGINX_IMAGE_ID"
for service_and_identity in "api:$STAGED_API_IMAGE_ID" "frontend:$STAGED_WEB_IMAGE_ID" "nginx:$STAGED_NGINX_IMAGE_ID"; do
  service="${service_and_identity%%:*}"
  identity="${service_and_identity#*:}"
  service_is_running "$service" || die "Staged service is not running: $service"
  assert_service_image_identity "$service" "$identity"
done

# Promotion is the release-authority mutation boundary. Revalidate every
# required runtime through its Docker health contract immediately before the
# public protocol probes and before any acceptance receipt or active marker can
# be written. Do not call health.sh here: this process already owns the global
# operation lock, and the helpers below keep this gate bounded and in-process.
readonly acceptance_service_health_attempts=30
readonly acceptance_lifecycle_health_attempts=30
for service in postgres meilisearch converter search-projection-worker api frontend nginx; do
  wait_for_service "$service" "$acceptance_service_health_attempts"
done
lifecycle_enabled="$(read_env_optional_value CONNECTMD_ACCOUNT_LIFECYCLE_ENABLED)" \
  || die "CONNECTMD_ACCOUNT_LIFECYCLE_ENABLED must appear at most once in .env"
if [ "${lifecycle_enabled:-false}" = "true" ]; then
  wait_for_profiled_service account-lifecycle account-erasure-worker "$acceptance_lifecycle_health_attempts"
fi

# A retry after the durable receipt or atomic active-marker promotion may find
# the prior digest changed.  It is acceptable only if the existing accepted
# marker is exactly the staged identity and is bound to the same acceptance.
current_marker_digest="$(active_marker_digest_or_none)"
stage_already_promoted=false
if [ "$current_marker_digest" != "$STAGED_PRIOR_ACCEPTED_MARKER_DIGEST" ]; then
  load_active_release_identity
  [ "$RELEASE_SOURCE_REVISION" = "$STAGED_SOURCE_REVISION" ] && [ "$RELEASE_IMAGE_TAG" = "$STAGED_IMAGE_TAG" ] && [ "$RELEASE_API_IMAGE_ID" = "$STAGED_API_IMAGE_ID" ] && [ "$RELEASE_WEB_IMAGE_ID" = "$STAGED_WEB_IMAGE_ID" ] && [ "$RELEASE_NGINX_IMAGE_ID" = "$STAGED_NGINX_IMAGE_ID" ] && [ "$RELEASE_RECRUITING_ENABLED" = "$STAGED_RECRUITING_ENABLED" ] || die "Active marker changed after staging"
  staged_acceptance_receipt="$(load_release_acceptance "$STAGED_IMAGE_TAG" "" "$STAGED_RELEASE_DIGEST")"
  [ "$RELEASE_ACCEPTANCE_DIGEST" = "$(digest_of_file "$staged_acceptance_receipt")" ] || die "Active marker acceptance authority does not match this staged release"
  stage_already_promoted=true
fi

domain="$(require_hostname)"
origin="https://$domain"
workdir="$(mktemp -d "$REPO_ROOT/.connectmd-release-acceptance.XXXXXX")"
chmod 700 "$workdir"
cleanup() {
  local status=$?
  trap - EXIT
  rm -rf -- "$workdir"
  exit "$status"
}
trap cleanup EXIT

public_get() {
  local path="$1" output="$2" headers="$3"
  run_with_direct_system_trust curl -q --fail --silent --show-error --noproxy '*' --proto '=https' --tlsv1.2 --connect-timeout 15 --max-time 45 \
    --dump-header "$headers" --output "$output" "$origin$path"
}

readonly agent_readme_max_bytes=16777216

public_get_agent_readme() {
  local output="$1" headers="$2" status
  status="$(run_with_direct_system_trust curl -q --fail --silent --show-error --noproxy '*' --proto '=https' --tlsv1.2 --connect-timeout 15 --max-time 45 --max-filesize "$agent_readme_max_bytes" \
    --dump-header "$headers" --output "$output" --write-out '%{http_code}' "$origin/agent-readme.md")"
  [ "$status" = "200" ] || die "Public agent README probe did not return HTTP 200"
}

assert_agent_readme() {
  local body="$1" headers="$2" content_type body_bytes marker
  content_type="$(tr -d '\r' < "$headers" | sed -n 's/^[Cc]ontent-[Tt]ype:[[:space:]]*//p' | tail -n 1 | cut -d';' -f1)"
  [ "$content_type" = "text/markdown" ] || die "Public agent README probe returned an unexpected media type"
  body_bytes="$(wc -c < "$body" | tr -d '[:space:]')"
  case "$body_bytes" in
    '' | *[!0-9]*) die "Public agent README probe returned an invalid body size" ;;
  esac
  [ "$body_bytes" -le "$agent_readme_max_bytes" ] || die "Public agent README probe exceeded its response bound"
  for marker in '# connect.md agent onboarding README' '## Onboarding sequence' 'Idempotency-Key' 'If-Match'; do
    grep -F -- "$marker" "$body" >/dev/null || die "Public agent README omitted a required onboarding marker"
  done
}

public_post() {
  local path="$1" media_type="$2" body="$3" output="$4"
  local protocol_headers=()
  case "$path" in
    /mcp) protocol_headers=(--header 'MCP-Protocol-Version: 2025-06-18') ;;
    /a2a/*) protocol_headers=(--header 'A2A-Version: 1.0') ;;
  esac
  run_with_direct_system_trust curl -q --fail --silent --show-error --noproxy '*' --proto '=https' --tlsv1.2 --connect-timeout 15 --max-time 45 \
    --header "Content-Type: $media_type" "${protocol_headers[@]}" --data "$body" --dump-header "$output.headers" --output "$output" "$origin$path"
}

expected_frontend_csp() {
  local domain="$1" template csp
  template="$REPO_ROOT/infra/nginx/conf.d/connectmd.tls.conf"
  [ -f "$template" ] && [ ! -L "$template" ] || die "Staged frontend CSP template is missing or unsafe"
  csp="$(sed -n 's/^[[:space:]]*add_header Content-Security-Policy "\(.*\)" always;$/\1/p' "$template")"
  [ "$(printf '%s\n' "$csp" | sed '/^$/d' | wc -l | tr -d '[:space:]')" = 1 ] || die "Staged frontend CSP template is ambiguous"
  case "$csp" in
    *'__CONNECTMD_DOMAIN__'*) ;;
    *) die "Staged frontend CSP template is not domain-bound" ;;
  esac
  printf '%s\n' "${csp//__CONNECTMD_DOMAIN__/$domain}"
}

assert_json() {
  local file="$1" kind="$2"
  python3 - "$file" "$kind" "$origin" <<'PY'
import json
import sys

kind, origin = sys.argv[2], sys.argv[3]
if kind in {"llms", "llms-full"}:
    payload = None
else:
    payload = json.load(open(sys.argv[1], encoding="utf-8"))
if kind == "openapi":
    assert isinstance(payload.get("openapi"), str) and payload["openapi"].startswith("3.")
    paths = payload["paths"]
    for path, method in {
        "/v1/profiles": "post",
        "/v1/profiles/{handle}": "put",
        "/v1/resumes": "post",
        "/v1/resumes/{slug}": "put",
        "/v1/search": "get",
        "/v1/search/query": "post",
        "/v1/ingest": "post",
    }.items():
        assert method in paths[path], (path, method)
    schemas = payload["components"]["schemas"]
    assert {"SearchQueryRequest", "SearchResponse"}.issubset(schemas)
    agent_capability = schemas["SearchQueryRequest"]["properties"]["agent_capability"]
    assert {"type": "string", "const": "internal_contact_request"} in agent_capability["anyOf"]
    assert {"hits", "offset", "limit", "total", "indexing_available"}.issubset(schemas["SearchResponse"]["properties"])
elif kind == "llms":
    text = open(sys.argv[1], encoding="utf-8").read()
    required = {
        "# connect.md",
        f"[OpenAPI]({origin}/openapi.json)",
        f"[OAuth protected-resource metadata]({origin}/.well-known/oauth-protected-resource)",
        f"[A2A Agent Card]({origin}/.well-known/agent-card.json)",
        f"[A2A HTTP+JSON endpoint]({origin}/a2a)",
        f"[MCP endpoint]({origin}/mcp)",
        "Content-Type: text/markdown",
        "Idempotency-Key",
        "POST /v1/search/query",
    }
    assert all(value in text for value in required), required - set(text.splitlines())
    assert f"http://{origin.removeprefix('https://')}" not in text
elif kind == "llms-full":
    text = open(sys.argv[1], encoding="utf-8").read()
    required = {
        "# connect.md complete agent guide",
        f"Base URL: {origin}",
        f"OpenAPI: {origin}/openapi.json",
        "POST /mcp",
        "POST /a2a/message:send",
        "GET /v1/search",
        "Idempotency-Key",
    }
    assert all(value in text for value in required), required - set(text.splitlines())
    assert f"http://{origin.removeprefix('https://')}" not in text
elif kind == "agent-card":
    assert payload["name"] == "connect.md"
    assert payload["supportedInterfaces"] == [{
        "url": f"{origin}/a2a", "protocolBinding": "HTTP+JSON", "protocolVersion": "1.0"
    }]
    assert payload["documentationUrl"] == f"{origin}/llms-full.txt"
    assert payload["capabilities"] == {"streaming": False, "pushNotifications": False, "extendedAgentCard": False}
    assert {skill["id"] for skill in payload["skills"]} == {
        "search-public-documents", "discover-public-taxonomies", "discover-public-agents",
        "list-profile-agents", "request-mediated-contact", "send-mandate-bound-agent-outreach",
        "get-mandate-bound-agent-outreach-status",
    }
elif kind == "oauth":
    assert payload["resource"] == origin
    assert payload["resource_documentation"] == f"{origin}/docs"
    assert payload["bearer_methods_supported"] == ["header"]
    assert {"documents:read", "documents:write", "search:read", "contacts:write"}.issubset(payload["scopes_supported"])
    assert isinstance(payload.get("authorization_servers"), list) and len(payload["authorization_servers"]) == 1
    assert payload["authorization_servers"][0].startswith("https://")
elif kind == "mcp-oauth":
    assert payload["resource"] == f"{origin}/mcp"
elif kind in {"search", "exact-search"}:
    assert isinstance(payload["hits"], list) and len(payload["hits"]) <= 1
    assert payload["offset"] == 0 and payload["limit"] == 1
    assert isinstance(payload["total"], int) and payload["total"] >= len(payload["hits"])
    assert isinstance(payload["indexing_available"], bool) and isinstance(payload["facets"], dict) and isinstance(payload["taxonomy_facets"], dict)
    assert payload["warning"] is None or isinstance(payload["warning"], str)
    assert all(not ({"owner_id", "grant_id", "mandate_id", "external_endpoint"} & set(hit)) for hit in payload["hits"])
    if kind == "exact-search":
        assert payload["mode"] == "exact" and payload["offset"] == 0
        assert payload["indexing_available"] is True and payload["complete"] is True
        assert isinstance(payload["search_revision"], int) and payload["search_revision"] >= 0
        assert payload["next_cursor"] is None or isinstance(payload["next_cursor"], str)
elif kind == "mcp":
    assert payload["jsonrpc"] == "2.0" and payload["id"] == 1
    assert payload["result"]["protocolVersion"] == "2025-06-18"
    assert payload["result"]["serverInfo"]["name"] == "connect.md"
    assert payload["result"]["capabilities"] == {"tools": {"listChanged": False}}
elif kind == "mcp-tools":
    assert payload["jsonrpc"] == "2.0" and payload["id"] == 2
    names = {tool["name"] for tool in payload["result"]["tools"]}
    assert {
        "list_taxonomies", "list_taxonomy_terms", "search_documents", "get_agent_identity",
        "list_profile_agents", "list_agent_directory", "read_document", "list_my_documents",
        "get_changes", "update_document", "create_document", "propose_document_update",
        "send_agent_outreach", "get_agent_outreach_status",
    }.issubset(names)
elif kind == "mcp-search":
    assert payload["jsonrpc"] == "2.0" and payload["id"] == 3
    assert payload["result"].get("isError") is not True
    search = payload["result"]["structuredContent"]
    assert isinstance(search["hits"], list) and len(search["hits"]) <= 1
    assert search["offset"] == 0 and search["limit"] == 1 and isinstance(search["total"], int)
    assert isinstance(search["indexing_available"], bool)
elif kind == "a2a":
    task = payload["task"]
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
    assert isinstance(task["artifacts"], list) and task["artifacts"]
    search = task["artifacts"][0]["parts"][0]["data"]
    assert isinstance(search["hits"], list) and len(search["hits"]) <= 1
    assert search["offset"] == 0 and search["limit"] == 1 and isinstance(search["total"], int)
    assert isinstance(search["indexing_available"], bool)
else:
    raise AssertionError(kind)
PY
}

assert_oauth_consistency() {
  python3 - "$1" "$2" "$origin" <<'PY'
import json
import sys

http = json.load(open(sys.argv[1], encoding="utf-8"))
mcp = json.load(open(sys.argv[2], encoding="utf-8"))
origin = sys.argv[3]
assert http["resource"] == origin
assert mcp["resource"] == f"{origin}/mcp"
assert http["authorization_servers"] == mcp["authorization_servers"]
assert http["scopes_supported"] == mcp["scopes_supported"]
assert http["bearer_methods_supported"] == mcp["bearer_methods_supported"] == ["header"]
assert http["resource_documentation"] == mcp["resource_documentation"] == f"{origin}/docs"
PY
}

expected_frontend_csp="$(expected_frontend_csp "$domain")"
public_get / "$workdir/root.body" "$workdir/root.headers"
header_file="$workdir/root.headers"
tr -d '\r' < "$header_file" > "$workdir/root.headers.normalized"
grep -Fxq "Strict-Transport-Security: max-age=31536000; includeSubDomains" "$workdir/root.headers.normalized" || die "Public HTTPS origin did not return the required HSTS header"
grep -Fxq "Content-Security-Policy: $expected_frontend_csp" "$workdir/root.headers.normalized" || die "Public HTTPS origin did not return the required Content-Security-Policy"
release_header="$(sed -n 's/^X-Connectmd-Release-Tag: //p' "$workdir/root.headers.normalized")"
[ "$release_header" = "$STAGED_IMAGE_TAG" ] || die "Public HTTPS origin did not return the staged X-Connectmd-Release-Tag"

# This uses normal DNS and the public CA trust store.  The leaf fingerprint is
# captured only after OpenSSL verifies both trust and the hostname.
run_with_direct_system_trust openssl s_client -connect "$domain:443" -servername "$domain" -verify_return_error -verify_hostname "$domain" </dev/null 2>"$workdir/tls.stderr" \
  | run_with_direct_system_trust openssl x509 -outform DER > "$workdir/tls-leaf.der"
[ -s "$workdir/tls-leaf.der" ] || die "Public TLS leaf could not be verified"

run_with_direct_system_trust curl -q --silent --show-error --noproxy '*' --proto '=http' --connect-timeout 15 --max-time 45 --max-redirs 0 \
  --dump-header "$workdir/http.headers" --output /dev/null "http://$domain/"
tr -d '\r' < "$workdir/http.headers" > "$workdir/http.headers.normalized"
grep -Eq '^HTTP/[0-9.]+ 301 ' "$workdir/http.headers.normalized" || die "Public HTTP origin did not return the required redirect"
grep -Fxq "Location: $origin/" "$workdir/http.headers.normalized" || die "Public HTTP redirect did not target the canonical HTTPS origin"

public_get_agent_readme "$workdir/agent-readme.md" "$workdir/agent-readme.headers"
assert_agent_readme "$workdir/agent-readme.md" "$workdir/agent-readme.headers"
public_get /openapi.json "$workdir/openapi.json" "$workdir/openapi.headers"
public_get /llms.txt "$workdir/llms.txt" "$workdir/llms.headers"
public_get /llms-full.txt "$workdir/llms-full.txt" "$workdir/llms-full.headers"
public_get /.well-known/agent-card.json "$workdir/agent-card.json" "$workdir/agent-card.headers"
public_get /.well-known/oauth-protected-resource "$workdir/oauth.json" "$workdir/oauth.headers"
public_get /.well-known/oauth-protected-resource/mcp "$workdir/mcp-oauth.json" "$workdir/mcp-oauth.headers"
public_post /v1/search/query application/json '{"q":"connectmd","limit":1}' "$workdir/public-search.json"
public_post /v1/search/query application/json '{"mode":"exact","q":"connectmd-release-acceptance-probe","limit":1}' "$workdir/exact-search.json"
public_post /mcp application/json '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"connectmd-release-accept","version":"1"}}}' "$workdir/mcp-initialize.json"
public_post /mcp application/json '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' "$workdir/mcp-tools.json"
public_post /mcp application/json '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"search_documents","arguments":{"q":"connectmd","limit":1}}}' "$workdir/mcp-search.json"
public_post /a2a/message:send application/a2a+json '{"message":{"messageId":"release-accept-search","role":"ROLE_USER","parts":[{"data":{"action":"search","query":"connectmd","limit":1},"mediaType":"application/json"}]}}' "$workdir/a2a-search.json"

assert_json "$workdir/openapi.json" openapi
assert_json "$workdir/llms.txt" llms
assert_json "$workdir/llms-full.txt" llms-full
assert_json "$workdir/agent-card.json" agent-card
assert_json "$workdir/oauth.json" oauth
assert_json "$workdir/mcp-oauth.json" mcp-oauth
assert_oauth_consistency "$workdir/oauth.json" "$workdir/mcp-oauth.json"
assert_json "$workdir/public-search.json" search
assert_json "$workdir/exact-search.json" exact-search
assert_json "$workdir/mcp-initialize.json" mcp
assert_json "$workdir/mcp-tools.json" mcp-tools
assert_json "$workdir/mcp-search.json" mcp-search
assert_json "$workdir/a2a-search.json" a2a
for mcp_headers in "$workdir/mcp-initialize.json.headers" "$workdir/mcp-tools.json.headers" "$workdir/mcp-search.json.headers"; do
  tr -d '\r' < "$mcp_headers" | grep -Fxq 'MCP-Protocol-Version: 2025-06-18' || die "MCP response did not return the negotiated protocol version"
done

cat "$workdir/mcp-initialize.json" "$workdir/mcp-tools.json" "$workdir/mcp-search.json" > "$workdir/mcp-initialize-tools-search.json"
{
  printf 'format=connectmd-release-acceptance-evidence-v2\n'
  printf 'https_origin=%s\n' "$origin"
  printf 'tls_leaf_sha256=%s\n' "$(digest_of_file "$workdir/tls-leaf.der")"
  printf 'http_redirect_sha256=%s\n' "$(digest_of_file "$workdir/http.headers.normalized")"
  printf 'hsts_sha256=%s\n' "$(digest_of_file "$workdir/root.headers.normalized")"
  printf 'openapi_sha256=%s\n' "$(digest_of_file "$workdir/openapi.json")"
  printf 'llms_sha256=%s\n' "$(digest_of_file "$workdir/llms.txt")"
  printf 'llms_full_sha256=%s\n' "$(digest_of_file "$workdir/llms-full.txt")"
  printf 'agent_card_sha256=%s\n' "$(digest_of_file "$workdir/agent-card.json")"
  printf 'oauth_sha256=%s\n' "$(digest_of_file "$workdir/oauth.json")"
  printf 'mcp_oauth_sha256=%s\n' "$(digest_of_file "$workdir/mcp-oauth.json")"
  printf 'public_search_sha256=%s\n' "$(digest_of_file "$workdir/public-search.json")"
  printf 'exact_search_sha256=%s\n' "$(digest_of_file "$workdir/exact-search.json")"
  printf 'mcp_initialize_tools_search_sha256=%s\n' "$(digest_of_file "$workdir/mcp-initialize-tools-search.json")"
  printf 'a2a_search_sha256=%s\n' "$(digest_of_file "$workdir/a2a-search.json")"
} > "$workdir/evidence.env"
chmod 600 "$workdir/evidence.env"

acceptance_receipt="$(write_release_acceptance "$STAGED_SOURCE_REVISION" "$STAGED_IMAGE_TAG" "$STAGED_API_IMAGE_ID" "$STAGED_WEB_IMAGE_ID" "$STAGED_NGINX_IMAGE_ID" "$STAGED_RELEASE_DIGEST" "$workdir/evidence.env")"
if [ "$stage_already_promoted" = false ]; then
  persist_image_tag "$STAGED_IMAGE_TAG" "$STAGED_SOURCE_REVISION" "$STAGED_API_IMAGE_ID" "$STAGED_WEB_IMAGE_ID" "$STAGED_NGINX_IMAGE_ID" "$STAGED_RELEASE_DIGEST"
else
  [ "$RELEASE_ACCEPTANCE_DIGEST" = "$(digest_of_file "$acceptance_receipt")" ] || die "Active marker acceptance authority does not match this staged release"
fi
clear_staged_release_after_acceptance "$STAGED_RELEASE_DIGEST"
clear_matching_completed_restore_state "$STAGED_SOURCE_REVISION" "$STAGED_IMAGE_TAG" "$STAGED_API_IMAGE_ID" "$STAGED_WEB_IMAGE_ID" "$STAGED_NGINX_IMAGE_ID" "$STAGED_RELEASE_RECEIPT_DIGEST"
printf 'ACCEPTED_IMAGE_TAG=%s\n' "$STAGED_IMAGE_TAG"
printf 'ACCEPTANCE_RECEIPT=%s\n' "$acceptance_receipt"
