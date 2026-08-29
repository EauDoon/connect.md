#!/usr/bin/env bash
set -Eeuo pipefail

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

run_external() {
  docker run --rm \
    --add-host api:127.0.0.1 \
    --add-host frontend:127.0.0.1 \
    -e CONNECTMD_DOMAIN=connectmd.example.test \
    -e CONNECTMD_RELEASE_TAG=external-contract \
    -e CONNECTMD_TLS_MODE=external \
    -e CONNECTMD_HTTP_BINDING="$1" \
    -e CONNECTMD_HTTPS_BINDING="$2" \
    connectmd-nginx:local nginx "$3"
}

output="$(run_external 127.0.0.1:18080:80 127.0.0.1::443 -T 2>&1)" \
  || die "External TLS Nginx configuration failed validation"
for marker in \
  'listen 80 default_server;' \
  'server_name connectmd.example.test;' \
  'set_real_ip_from 172.31.254.1;' \
  'proxy_set_header X-Forwarded-Proto https;' \
  'proxy_set_header X-Forwarded-For $remote_addr;' \
  'location ^~ /v1/' \
  'X-Connectmd-Release-Tag "external-contract"'
do
  printf '%s\n' "$output" | grep -Fq "$marker" \
    || die "External TLS Nginx configuration omitted: $marker"
done
if printf '%s\n' "$output" | grep -Eq 'listen .*443|ssl_certificate'; then
  die "External TLS Nginx configuration retained a local TLS listener"
fi
if run_external 80:80 127.0.0.1::443 -t >/dev/null 2>&1; then
  die "External TLS Nginx accepted a public HTTP binding"
fi
if run_external 127.0.0.1:18080:80 443:443 -t >/dev/null 2>&1; then
  die "External TLS Nginx accepted a public HTTPS binding"
fi

printf 'NGINX_EXTERNAL_TLS=PASS\n'
