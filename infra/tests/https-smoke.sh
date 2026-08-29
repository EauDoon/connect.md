#!/usr/bin/env bash
# Exercise the public protocol surface through the production Compose proxy.
set -Eeuo pipefail

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
readonly PROJECT_NAME="connectmd-ci-tls-smoke"
readonly DOMAIN="connectmd.example.test"
readonly HTTP_PORT="18080"
readonly HTTPS_PORT="18443"
readonly PUBLIC_BASE="https://${DOMAIN}"
readonly EXPECTED_RELEASE_TAG="local"
readonly EXPECTED_CSP="default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'self'; form-action 'self'; script-src 'self' https://*.clerk.accounts.dev https://clerk.${DOMAIN} https://*.protect.clerk.com https://challenges.cloudflare.com; script-src-elem 'self' 'unsafe-inline' https://*.clerk.accounts.dev https://clerk.${DOMAIN} https://*.protect.clerk.com https://challenges.cloudflare.com; script-src-attr 'none'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https://img.clerk.com; font-src 'self' data:; connect-src 'self' https://*.clerk.accounts.dev https://clerk.${DOMAIN} https://*.protect.clerk.com:* https://img.clerk.com; worker-src 'self' blob:; frame-src 'self' blob: https://challenges.cloudflare.com https://*.protect.clerk.com; manifest-src 'self'; media-src 'none'"
readonly SCRATCH="$(mktemp -d)"
readonly MAX_PUBLIC_PROBE_BYTES=16777216
readonly PUBLIC_PROBE_HEADERS="$SCRATCH/public-resource.headers"
readonly PUBLIC_PROBE_BODY="$SCRATCH/public-resource.body"
readonly ENV_FILE="$SCRATCH/smoke.env"
readonly BACKUP_DIR="$SCRATCH/backups"
readonly WITNESS_DIR="$SCRATCH/deletion-head-witness"
readonly CERTIFICATE_DIR="$SCRATCH/certificates"
readonly CERTIFICATE="$CERTIFICATE_DIR/fullchain.pem"
readonly PRIVATE_KEY="$CERTIFICATE_DIR/privkey.pem"
readonly COMPOSE_BASE="$REPO_ROOT/compose.yaml"
readonly COMPOSE_PROD="$REPO_ROOT/compose.prod.yaml"
readonly CONTAINER_UID=10001
readonly CONTAINER_GID=10001
readonly HOST_UID="$(id -u)"
readonly HOST_GID="$(id -g)"
COMPOSE=(docker compose --project-name "$PROJECT_NAME" --env-file "$ENV_FILE" -f "$COMPOSE_BASE" -f "$COMPOSE_PROD")

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "Required command is unavailable: $1"
}

run_as_root() {
  if [ "$HOST_UID" = 0 ]; then
    "$@"
  else
    sudo --non-interactive "$@"
  fi
}

set_env_value() {
  local key="$1"
  local value="$2"
  sed -i -E "s|^${key}=.*$|${key}=${value}|" "$ENV_FILE"
}

env_value() {
  local key="$1"
  grep -E "^${key}=" "$ENV_FILE" | cut -d= -f2-
}

apply_database_role_contract() {
  local mutate="$1" reconcile="$2" verify="$3"
  {
    printf '%s\n' \
      "$(env_value CONNECTMD_MIGRATOR_DB_PASSWORD)" \
      "$(env_value CONNECTMD_API_DB_PASSWORD)" \
      "$(env_value CONNECTMD_SEARCH_PROJECTION_DB_PASSWORD)" \
      "$(env_value CONNECTMD_PROJECTION_ADMIN_DB_PASSWORD)" \
      "$(env_value CONNECTMD_ACCOUNT_ERASURE_DB_PASSWORD)" \
      "$(env_value CONNECTMD_BACKUP_DB_PASSWORD)"
    cat "$REPO_ROOT/infra/postgres/database-role-contract.sql"
  } | "${COMPOSE[@]}" exec -T postgres sh -eu -c '
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

wait_for_service() {
  local service="$1"
  local container status attempt
  container="$("${COMPOSE[@]}" ps -q "$service")"
  [ -n "$container" ] || die "No container found for $service"
  for attempt in $(seq 1 60); do
    status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container")"
    case "$status" in
      healthy | running) return 0 ;;
      unhealthy | exited | dead) die "$service entered state: $status" ;;
    esac
    sleep 2
  done
  die "Timed out waiting for $service"
}

https_get() {
  curl --fail --silent --show-error \
    --cacert "$CERTIFICATE" \
    --resolve "${DOMAIN}:${HTTPS_PORT}:127.0.0.1" \
    "https://${DOMAIN}:${HTTPS_PORT}$1"
}

https_headers() {
  curl --fail --silent --show-error \
    --cacert "$CERTIFICATE" \
    --resolve "${DOMAIN}:${HTTPS_PORT}:127.0.0.1" \
    --dump-header - \
    --output /dev/null \
    "https://${DOMAIN}:${HTTPS_PORT}$1"
}

https_assert_public_resource() {
  local path="$1"
  local expected_content_type="$2"
  local marker="$3"
  local status content_type body_bytes
  if ! status="$(curl --silent --show-error \
    --cacert "$CERTIFICATE" \
    --resolve "${DOMAIN}:${HTTPS_PORT}:127.0.0.1" \
    --max-time 30 \
    --max-filesize "$MAX_PUBLIC_PROBE_BYTES" \
    --dump-header "$PUBLIC_PROBE_HEADERS" \
    --output "$PUBLIC_PROBE_BODY" \
    --write-out '%{http_code}' \
    "https://${DOMAIN}:${HTTPS_PORT}${path}")"; then
    die "HTTPS public-resource probe failed: $path"
  fi
  [ "$status" = "200" ] || die "HTTPS public-resource probe returned a non-200 status: $path"
  body_bytes="$(wc -c < "$PUBLIC_PROBE_BODY" | tr -d '[:space:]')"
  [ "$body_bytes" -le "$MAX_PUBLIC_PROBE_BYTES" ] || die "HTTPS public-resource probe exceeded its response bound: $path"
  content_type="$(
    tr -d '\r' < "$PUBLIC_PROBE_HEADERS" |
      sed -n 's/^[Cc]ontent-[Tt]ype:[[:space:]]*//p' |
      tail -n 1 |
      cut -d';' -f1
  )"
  [ "$content_type" = "$expected_content_type" ] || die "HTTPS public-resource probe returned an unexpected content type: $path"
  grep -F -- "$marker" "$PUBLIC_PROBE_BODY" >/dev/null || die "HTTPS public-resource probe omitted its stable marker: $path"
}

https_json_post() {
  local path="$1"
  local body="$2"
  curl --fail --silent --show-error \
    --cacert "$CERTIFICATE" \
    --resolve "${DOMAIN}:${HTTPS_PORT}:127.0.0.1" \
    --header 'Content-Type: application/json' \
    --data "$body" \
    "https://${DOMAIN}:${HTTPS_PORT}${path}"
}

mcp_post() {
  curl --fail --silent --show-error \
    --cacert "$CERTIFICATE" \
    --resolve "${DOMAIN}:${HTTPS_PORT}:127.0.0.1" \
    --header 'Content-Type: application/json' \
    --header 'MCP-Protocol-Version: 2025-06-18' \
    --data "$1" \
    "https://${DOMAIN}:${HTTPS_PORT}/mcp"
}

a2a_post() {
  curl --fail --silent --show-error \
    --cacert "$CERTIFICATE" \
    --resolve "${DOMAIN}:${HTTPS_PORT}:127.0.0.1" \
    --header 'Content-Type: application/a2a+json' \
    --header 'A2A-Version: 1.0' \
    --data "$1" \
    "https://${DOMAIN}:${HTTPS_PORT}/a2a/message:send"
}

assert_https_text() {
  local body="$1"
  case "$body" in
    *"$PUBLIC_BASE"*) ;;
    *) die "Discovery response did not use the configured HTTPS public base URL" ;;
  esac
  case "$body" in
    *"http://${DOMAIN}"*) die "Discovery response leaked an HTTP public URL" ;;
  esac
}

assert_agent_readme() {
  local body="$1"
  case "$body" in
    *'# connect.md agent onboarding README'*) ;;
    *) die "Agent onboarding README was not served as canonical Markdown" ;;
  esac
  case "$body" in
    *'## Onboarding sequence'*'Idempotency-Key'*'If-Match'*) ;;
    *) die "Agent onboarding README omitted the safe write sequence" ;;
  esac
}

assert_sitemap_same_origin() {
  local body_file="$1"
  python3 - "$body_file" "$PUBLIC_BASE" <<'PY'
import sys
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from urllib.parse import urlsplit

body_path, expected_base = sys.argv[1:]
try:
    root = ElementTree.parse(Path(body_path)).getroot()
except (OSError, ElementTree.ParseError) as exc:
    raise SystemExit("sitemap XML could not be parsed") from exc

local_name = lambda tag: tag.rsplit("}", 1)[-1]
if local_name(root.tag) != "urlset":
    raise SystemExit("sitemap root was not urlset")

expected = urlsplit(expected_base)
if expected.scheme != "https" or not expected.netloc:
    raise SystemExit("configured public origin was not HTTPS")

locations = [
    element.text or ""
    for element in root.iter()
    if local_name(element.tag) == "loc"
]
if len(locations) > 50_000:
    raise SystemExit("sitemap exceeded its URL bound")
for location in locations:
    parsed = urlsplit(location)
    if parsed.scheme != expected.scheme or parsed.netloc != expected.netloc or not parsed.path.startswith("/"):
        raise SystemExit("sitemap contained a non-same-origin URL")
PY
}

assert_monaco_loader() {
  local body="$1"
  case "$body" in
    *'_amdLoaderGlobal'*'define.amd'*) ;;
    *) die "Self-hosted Monaco loader was not served from the frontend" ;;
  esac
}

assert_csp_header() {
  local headers="$1"
  local actual
  actual="$(printf '%s' "$headers" | tr -d '\r' | sed -n 's/^Content-Security-Policy: //p')"
  [ "$actual" = "$EXPECTED_CSP" ] || die "HTTPS response did not contain the exact production Content-Security-Policy"
}

assert_release_tag_header() {
  local headers="$1" actual
  actual="$(printf '%s' "$headers" | tr -d '\r' | sed -n 's/^X-Connectmd-Release-Tag: //p')"
  [ "$actual" = "$EXPECTED_RELEASE_TAG" ] || die "HTTPS response did not contain the exact release tag"
}

assert_resource_metadata() {
  local body="$1"
  local expected="$2"
  printf '%s' "$body" | python3 -c '
import json
import sys

payload = json.load(sys.stdin)
expected = sys.argv[1]
assert payload["resource"] == expected, payload
' "$expected"
}

assert_agent_card() {
  local body="$1"
  printf '%s' "$body" | python3 -c '
import json
import sys

payload = json.load(sys.stdin)
base = sys.argv[1]
interface = payload["supportedInterfaces"][0]
assert interface == {
    "url": f"{base}/a2a",
    "protocolBinding": "HTTP+JSON",
    "protocolVersion": "1.0",
}, payload
assert {skill["id"] for skill in payload["skills"]} == {
    "search-public-documents",
    "discover-public-taxonomies",
    "discover-public-agents",
    "list-profile-agents",
    "request-mediated-contact",
    "send-mandate-bound-agent-outreach",
    "get-mandate-bound-agent-outreach-status",
}, payload
' "$PUBLIC_BASE"
}

assert_taxonomy_catalog() {
  local body="$1"
  printf '%s' "$body" | python3 -c '
import json
import sys

payload = json.load(sys.stdin)
expected = {
    "occupation", "industry", "location", "skill", "language",
    "seniority", "open_to", "organization", "representative", "work_mode",
}
assert isinstance(payload, list), payload
assert {entry["taxonomy"] for entry in payload} == expected, payload
for entry in payload:
    assert isinstance(entry["current_revision"], int), entry
    assert "owner_id" not in entry and "document_id" not in entry and "count" not in entry, entry
'
}

assert_taxonomy_terms() {
  local body="$1"
  printf '%s' "$body" | python3 -c '
import json
import sys

payload = json.load(sys.stdin)
assert payload["terms"] == [] and payload["next_cursor"] is None, payload
assert isinstance(payload["revision"], int) and payload["revision"] >= 0, payload
'
}

assert_structured_search() {
  local body="$1"
  printf '%s' "$body" | python3 -c '
import json
import sys

payload = json.load(sys.stdin)
assert payload["hits"] == [], payload
assert payload["offset"] == 0 and payload["limit"] == 5 and payload["total"] == 0, payload
assert payload["indexing_available"] is True, payload
assert payload["facets"] == {} and payload["taxonomy_facets"] == {}, payload
assert payload["warning"] is None, payload
'
}

assert_mcp_public_search() {
  local body="$1"
  printf '%s' "$body" | python3 -c '
import json
import sys

payload = json.load(sys.stdin)
result = payload["result"]
assert result.get("isError") is not True, payload
search = result["structuredContent"]
assert search["hits"] == [] and search["total"] == 0 and search["limit"] == 5, payload
assert search["indexing_available"] is True, payload
'
}

assert_a2a_search() {
  local body="$1"
  printf '%s' "$body" | python3 -c '
import json
import sys

payload = json.load(sys.stdin)
task = payload["task"]
assert task["status"]["state"] == "TASK_STATE_COMPLETED", payload
search = task["artifacts"][0]["parts"][0]["data"]
assert search["hits"] == [], payload
assert search["offset"] == 0 and search["limit"] == 5 and search["total"] == 0, payload
assert search["indexing_available"] is True, payload
assert search["facets"] == {} and search["taxonomy_facets"] == {}, payload
assert search["warning"] is None, payload
'
}

assert_mcp_initialize() {
  local body="$1"
  printf '%s' "$body" | python3 -c '
import json
import sys

payload = json.load(sys.stdin)
assert payload["id"] == 1, payload
assert payload["result"]["protocolVersion"] == "2025-06-18", payload
' 
}

assert_mcp_tools() {
  local body="$1"
  printf '%s' "$body" | python3 -c '
import json
import sys

payload = json.load(sys.stdin)
assert payload["id"] == 2, payload
names = {tool["name"] for tool in payload["result"]["tools"]}
assert {
    "search_documents",
    "list_taxonomies",
    "list_taxonomy_terms",
    "read_document",
}.issubset(names), names
'
}

cleanup() {
  local status=$?
  set +e
  "${COMPOSE[@]}" down --volumes --remove-orphans >/dev/null 2>&1
  run_as_root chown -R "$HOST_UID:$HOST_GID" "$BACKUP_DIR" "$WITNESS_DIR" >/dev/null 2>&1
  rm -rf "$SCRATCH"
  exit "$status"
}
trap cleanup EXIT

require_command docker
require_command curl
require_command openssl
require_command python3
if [ "$HOST_UID" != 0 ]; then
  require_command sudo
  sudo --non-interactive true || die "Passwordless sudo is required for the HTTPS smoke ownership boundary"
fi

cp "$REPO_ROOT/.env.example" "$ENV_FILE"
set_env_value POSTGRES_PASSWORD 1111111111111111111111111111111111111111111111111111111111111111
set_env_value CONNECTMD_MIGRATOR_DB_PASSWORD 2222222222222222222222222222222222222222222222222222222222222222
set_env_value CONNECTMD_API_DB_PASSWORD 3333333333333333333333333333333333333333333333333333333333333333
set_env_value CONNECTMD_PROJECTION_ADMIN_DB_PASSWORD 4444444444444444444444444444444444444444444444444444444444444444
set_env_value CONNECTMD_ACCOUNT_ERASURE_DB_PASSWORD 5555555555555555555555555555555555555555555555555555555555555555
set_env_value CONNECTMD_BACKUP_DB_PASSWORD 6666666666666666666666666666666666666666666666666666666666666666
set_env_value CONNECTMD_SEARCH_PROJECTION_DB_PASSWORD 7777777777777777777777777777777777777777777777777777777777777777
set_env_value MEILI_MASTER_KEY ci-meilisearch-master-key-for-tls-smoke
set_env_value CONNECTMD_CLERK_JWKS_URL https://clerk.example.test/.well-known/jwks.json
set_env_value CONNECTMD_CLERK_ISSUER https://clerk.example.test
set_env_value CONNECTMD_CLERK_AUTHORIZED_PARTIES "[\"${PUBLIC_BASE}\"]"
set_env_value CONNECTMD_API_KEY_PEPPER ci-api-key-pepper-for-tls-smoke-0123456789
set_env_value CONNECTMD_EXACT_SEARCH_CURSOR_KEYRING '[{"kid":"ci-v1","secret":"Y2ktZXhhY3Qtc2VhcmNoLWN1cnNvci1zZWNyZXQtMDAw"}]'
set_env_value CONNECTMD_EXACT_SEARCH_CURSOR_TTL_SECONDS 900
set_env_value CONNECTMD_BACKUP_DIR "$BACKUP_DIR"
set_env_value CONNECTMD_DELETION_WITNESS_DIR "$WITNESS_DIR"
set_env_value CONNECTMD_LIFECYCLE_HMAC_KEY ci-lifecycle-hmac-key-for-tls-smoke-0123456789
set_env_value CONNECTMD_LIFECYCLE_AEAD_KEY ci-lifecycle-aead-key-for-tls-smoke-0123456789
set_env_value CONNECTMD_DELETION_WITNESS_HMAC_KEY ci-deletion-witness-key-for-tls-smoke-0123456789
set_env_value CONNECTMD_DOMAIN "$DOMAIN"
set_env_value CONNECTMD_PUBLIC_BASE_URL "$PUBLIC_BASE"
set_env_value NEXT_PUBLIC_SITE_URL "$PUBLIC_BASE"
set_env_value NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY pk_test_Zm9vLWJhci0xLmNsZXJrLmFjY291bnRzLmRldiQ=
set_env_value CONNECTMD_HTTP_PORT "$HTTP_PORT"
set_env_value CONNECTMD_HTTPS_PORT "$HTTPS_PORT"
set_env_value ACME_EMAIL ci-smoke@example.test

mkdir -p "$BACKUP_DIR/.connectmd-lifecycle/deletion-journal" "$WITNESS_DIR"
chmod 700 "$BACKUP_DIR/.connectmd-lifecycle" "$BACKUP_DIR/.connectmd-lifecycle/deletion-journal" "$WITNESS_DIR"
run_as_root chown -R "$CONTAINER_UID:$CONTAINER_GID" "$BACKUP_DIR/.connectmd-lifecycle/deletion-journal" "$WITNESS_DIR"
[ "$(stat -c '%u:%g' "$BACKUP_DIR/.connectmd-lifecycle/deletion-journal")" = "$CONTAINER_UID:$CONTAINER_GID" ] || die "Scratch deletion journal ownership does not match the API image"
[ "$(stat -c '%u:%g' "$WITNESS_DIR")" = "$CONTAINER_UID:$CONTAINER_GID" ] || die "Scratch deletion witness ownership does not match the API image"
mkdir -p "$CERTIFICATE_DIR"
openssl req -x509 -newkey rsa:2048 -sha256 -nodes -days 1 \
  -keyout "$PRIVATE_KEY" \
  -out "$CERTIFICATE" \
  -subj "/CN=${DOMAIN}" \
  -addext "subjectAltName=DNS:${DOMAIN}" \
  >/dev/null 2>&1

# Seed only this disposable Compose project's certificate volume. Certbot is not
# invoked, so this test makes no ACME, DNS, Clerk, or other external request.
"${COMPOSE[@]}" --profile tls run --rm --no-deps --entrypoint sh \
  --volume "$CERTIFICATE_DIR:/seed:ro" \
  certbot -ceu "
    mkdir -p /etc/letsencrypt/live/${DOMAIN}
    cp /seed/fullchain.pem /etc/letsencrypt/live/${DOMAIN}/fullchain.pem
    cp /seed/privkey.pem /etc/letsencrypt/live/${DOMAIN}/privkey.pem
    chmod 600 /etc/letsencrypt/live/${DOMAIN}/privkey.pem
  " >/dev/null

"${COMPOSE[@]}" up -d --no-build postgres meilisearch
wait_for_service postgres
wait_for_service meilisearch
search_key_line="$("${COMPOSE[@]}" --profile search-bootstrap run --rm --no-deps -T search-key-bootstrap python -m app.search_key_bootstrap search)"
[ "$(printf '%s\n' "$search_key_line" | wc -l | tr -d ' ')" = "1" ] || die "Search-key bootstrap output was not one line"
case "$search_key_line" in
  CONNECTMD_MEILISEARCH_SEARCH_KEY=*) ;;
  *) die "Search-key bootstrap output was invalid" ;;
esac
search_key="${search_key_line#*=}"
[ -n "$search_key" ] || die "Search-key bootstrap returned an empty key"
set_env_value CONNECTMD_MEILISEARCH_SEARCH_KEY "$search_key"
apply_database_role_contract true false false
"${COMPOSE[@]}" --profile database-operations run --rm --no-deps -T db-migrate alembic upgrade head
apply_database_role_contract true true true
"${COMPOSE[@]}" --profile account-lifecycle run --rm --no-deps -T api python -m app.cli deletion-journal init
"${COMPOSE[@]}" --profile account-lifecycle run --rm --no-deps -T api python -m app.cli deletion-journal checkpoint
"${COMPOSE[@]}" --profile taxonomy-operations run --rm --no-deps -T taxonomy-admin python -m app.cli taxonomy backfill --if-required
"${COMPOSE[@]}" --profile taxonomy-operations run --rm --no-deps -T taxonomy-admin python -m app.cli taxonomy verify
"${COMPOSE[@]}" --profile exact-search-operations run --rm --no-deps -T exact-search-admin python -m app.cli exact-search backfill --if-required
"${COMPOSE[@]}" --profile exact-search-operations run --rm --no-deps -T exact-search-admin python -m app.cli exact-search verify
"${COMPOSE[@]}" --profile search-operations run --rm --no-deps -T search-admin python -m app.cli rebuild-search
"${COMPOSE[@]}" up -d --no-build converter api frontend nginx
for service in converter api frontend nginx; do
  wait_for_service "$service"
done

llms="$(https_get /llms.txt)"
agent_readme="$(https_get /agent-readme.md)"
monaco_loader="$(https_get /monaco/vs/loader.js)"
security_headers="$(https_headers /)"
llms_full="$(https_get /llms-full.txt)"
taxonomy_catalog="$(https_get /v1/taxonomies)"
taxonomy_terms="$(https_get '/v1/taxonomies/skill?limit=1')"
structured_search="$(https_json_post /v1/search/query '{"q":"platform","limit":5}')"
exact_search="$(https_json_post /v1/search/query '{"mode":"exact","q":"connectmd-https-smoke-probe","limit":5}')"
oauth_metadata="$(https_get /.well-known/oauth-protected-resource)"
mcp_oauth_metadata="$(https_get /.well-known/oauth-protected-resource/mcp)"
agent_card="$(https_get /.well-known/agent-card.json)"
mcp_initialize="$(mcp_post '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"ci-smoke","version":"1"}}}')"
mcp_tools="$(mcp_post '{"jsonrpc":"2.0","id":2,"method":"tools/list"}')"
mcp_search="$(mcp_post '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"search_documents","arguments":{"q":"platform","limit":5}}}')"
a2a_search="$(a2a_post '{"message":{"messageId":"ci-a2a-search","role":"ROLE_USER","parts":[{"data":{"action":"search","query":"platform","limit":5},"mediaType":"application/json"}]}}')"

assert_https_text "$llms"
assert_https_text "$agent_readme"
assert_agent_readme "$agent_readme"
assert_monaco_loader "$monaco_loader"
assert_csp_header "$security_headers"
assert_release_tag_header "$security_headers"
assert_https_text "$llms_full"
public_sitemap_paths=(
  /sitemap/0.xml
  /sitemap/1.xml
  /sitemap/2.xml
  /sitemap/3.xml
)
https_assert_public_resource /robots.txt text/plain 'User-Agent: *'
for sitemap_path in "${public_sitemap_paths[@]}"; do
  grep -F -- "Sitemap: ${PUBLIC_BASE}${sitemap_path}" "$PUBLIC_PROBE_BODY" >/dev/null || die "robots.txt omitted a current sitemap URL: $sitemap_path"
done
for sitemap_path in "${public_sitemap_paths[@]}"; do
  https_assert_public_resource "$sitemap_path" application/xml '<urlset'
  assert_sitemap_same_origin "$PUBLIC_PROBE_BODY"
done
https_assert_public_resource /schemas/profile.v2.write.schema.json application/schema+json '"schema_version"'
assert_taxonomy_catalog "$taxonomy_catalog"
assert_taxonomy_terms "$taxonomy_terms"
assert_structured_search "$structured_search"
printf '%s' "$exact_search" | python3 -c '
import json
import sys

payload = json.load(sys.stdin)
assert payload["mode"] == "exact" and payload["hits"] == [] and payload["total"] == 0, payload
assert payload["offset"] == 0 and payload["limit"] == 5, payload
assert payload["indexing_available"] is True and payload["complete"] is True, payload
assert isinstance(payload["search_revision"], int) and payload["search_revision"] >= 0, payload
'
assert_resource_metadata "$oauth_metadata" "$PUBLIC_BASE"
assert_resource_metadata "$mcp_oauth_metadata" "$PUBLIC_BASE/mcp"
assert_agent_card "$agent_card"
assert_mcp_initialize "$mcp_initialize"
assert_mcp_tools "$mcp_tools"
assert_mcp_public_search "$mcp_search"
assert_a2a_search "$a2a_search"

printf 'HTTPS_SMOKE=PASS\n'
