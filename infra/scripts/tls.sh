#!/usr/bin/env bash
set -Eeuo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/lib.sh"

usage() {
  printf 'Usage: %s {issue|renew}\n' "${0##*/}" >&2
  exit 64
}

[ "$#" -eq 1 ] || usage
ensure_repo
acquire_operation_lock
validate_production_env
select_release_image_tag staged-or-accepted
domain="$(require_hostname)"
email="$(require_secret_value ACME_EMAIL)"
case "$email" in *@*.*) ;; *) die "ACME_EMAIL must be a valid contact address" ;; esac

case "$1" in
  issue)
    compose up -d --no-build nginx
    wait_for_service nginx
    compose --profile tls run --rm --no-deps certbot certonly --webroot --webroot-path /var/www/certbot --email "$email" --agree-tos --no-eff-email --non-interactive --keep-until-expiring -d "$domain"
    # The entrypoint selects the TLS server block only when the container starts.
    compose up -d --no-build --force-recreate nginx
    wait_for_service nginx
    compose exec -T nginx nginx -t
    ;;
  renew)
    compose --profile tls run --rm --no-deps certbot renew --webroot --webroot-path /var/www/certbot --non-interactive
    compose exec -T nginx nginx -t
    compose exec -T nginx nginx -s reload
    wait_for_service nginx
    ;;
  *)
    usage
    ;;
esac
printf 'TLS_%s=PASS\n' "$(printf '%s' "$1" | tr '[:lower:]' '[:upper:]')"
