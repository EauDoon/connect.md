#!/bin/sh
set -eu

config_dir=/etc/nginx/connectmd-conf.d
target=/etc/nginx/conf.d/default.conf
domain=${CONNECTMD_DOMAIN:-}
release_tag=${CONNECTMD_RELEASE_TAG:-local}
tls_mode=${CONNECTMD_TLS_MODE:-auto}
http_binding=${CONNECTMD_HTTP_BINDING:-}
https_binding=${CONNECTMD_HTTPS_BINDING:-}

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

if [ -n "$domain" ] && ! is_lowercase_dns_hostname "$domain"; then
  echo "CONNECTMD_DOMAIN is not a valid lowercase DNS hostname" >&2
  exit 1
fi

case "$release_tag" in
  ""|*[!A-Za-z0-9_.-]*)
    echo "CONNECTMD_RELEASE_TAG is not a valid release tag" >&2
    exit 1
    ;;
esac

case "$tls_mode" in
  auto) ;;
  external)
    case "$http_binding" in
      127.0.0.1:*:80) ;;
      *)
        echo "CONNECTMD_TLS_MODE=external requires a loopback-only CONNECTMD_HTTP_BINDING" >&2
        exit 1
        ;;
    esac
    case "$https_binding" in
      127.0.0.1:*:443) ;;
      *)
        echo "CONNECTMD_TLS_MODE=external requires a loopback-only CONNECTMD_HTTPS_BINDING" >&2
        exit 1
        ;;
    esac
    ;;
  *)
    echo "CONNECTMD_TLS_MODE must be auto or external" >&2
    exit 1
    ;;
esac

certificate="/etc/letsencrypt/live/$domain/fullchain.pem"
private_key="/etc/letsencrypt/live/$domain/privkey.pem"

if [ "$tls_mode" = external ]; then
  [ -n "$domain" ] || {
    echo "CONNECTMD_TLS_MODE=external requires CONNECTMD_DOMAIN" >&2
    exit 1
  }
  sed \
    -e '/^# connectmd: local TLS redirect begin$/,/^# connectmd: local TLS redirect end$/d' \
    -e 's/listen 443 ssl http2 default_server;/listen 80 default_server;/' \
    -e 's/listen \[::\]:443 ssl http2 default_server;/listen [::]:80 default_server;/' \
    -e 's/^  # external-proxy: /  /' \
    -e '/^[[:space:]]*ssl_/d' \
    -e "s/__CONNECTMD_DOMAIN__/$domain/g" \
    -e "s/__CONNECTMD_RELEASE_TAG__/$release_tag/g" \
    "$config_dir/connectmd.tls.conf" > "$target"
  if grep -Eq 'listen .*443|ssl_certificate' "$target"; then
    echo "External TLS configuration retained a local TLS listener" >&2
    exit 1
  fi
  echo "connect.md Nginx: external TLS termination enabled for $domain"
elif [ -n "$domain" ] && [ -r "$certificate" ] && [ -r "$private_key" ]; then
  sed -e "s/__CONNECTMD_DOMAIN__/$domain/g" -e "s/__CONNECTMD_RELEASE_TAG__/$release_tag/g" "$config_dir/connectmd.tls.conf" > "$target"
  echo "connect.md Nginx: TLS configuration enabled for $domain"
else
  cp "$config_dir/connectmd.http.conf" "$target"
  echo "connect.md Nginx: HTTP-only bootstrap configuration enabled"
fi
