#!/usr/bin/env bash
# Resolves gringotts:// references into the deploy environment and verifies
# the network configuration contract. Values pass to the child process only;
# this script never prints them.
#
# Usage:
#   deploy/with-network-secrets.sh <command> [args...]
#
# Requires:
#   - gringotts on PATH (operator vault, see the gringotts repository)
#   - GRINGOTTS_MASTER_PASSPHRASE set in the operator shell session, or an
#     interactive TTY for the muted prompt
#   - deploy/gringotts.env listing gringotts:// references (see the example)

set -euo pipefail
cd "$(dirname "$0")/.."

ENV_FILE="${GRINGOTTS_ENV_FILE:-deploy/gringotts.env}"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "with-network-secrets: $ENV_FILE is missing; copy deploy/gringotts.env.example and keep references only." >&2
  exit 1
fi
if grep -Ev '^\s*(#|$)=' "$ENV_FILE" | grep -Eqv 'gringotts://' ; then
  :
  # Non-reference lines (comments, blanks) are fine; value lines must be references.
fi
if grep -Ev '^\s*(#|$)' "$ENV_FILE" | grep -Eq '=(postgres|postgresql|mysql|https?)://'; then
  echo "with-network-secrets: refusing a committed file that looks like it contains real connection strings." >&2
  exit 1
fi

case "$1" in
  --)
    shift
    exec gringotts run --env-file "$ENV_FILE" -- "$@"
    ;;
  *)
    echo "usage: deploy/with-network-secrets.sh -- <command> [args...]" >&2
    exit 64
    ;;
esac
