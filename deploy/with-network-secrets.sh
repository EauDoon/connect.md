#!/usr/bin/env bash
# Resolves gringotts:// references into the deploy environment and verifies
# the network configuration contract. Values pass to the child process only;
# this script never prints them.
#
# Usage:
#   deploy/with-network-secrets.sh -- <command> [args...]
#
# Requires:
#   - gringotts on PATH (operator vault, see the gringotts repository)
#   - GRINGOTTS_MASTER_PASSPHRASE set in the operator shell session, or an
#     interactive TTY for the muted prompt
#   - deploy/gringotts.env listing gringotts:// references (see the example)
#
# The env file may hold only comments, blank lines, and NAME=gringotts://path
# references. Any other line, including a plaintext value, stops the deploy
# before gringotts runs; the error names the line number, never its value.

set -euo pipefail

usage() {
  echo "usage: deploy/with-network-secrets.sh -- <command> [args...]" >&2
  exit 64
}

if [[ "${1:-}" != "--" || $# -lt 2 ]]; then
  usage
fi
shift

cd "$(dirname "$0")/.."

ENV_FILE="${GRINGOTTS_ENV_FILE:-deploy/gringotts.env}"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "with-network-secrets: $ENV_FILE is missing; copy deploy/gringotts.env.example and keep references only." >&2
  exit 1
fi
if grep -Ev '^\s*(#|$)' "$ENV_FILE" | grep -Eq '=(postgres|postgresql|mysql|https?)://'; then
  echo "with-network-secrets: refusing a committed file that looks like it contains real connection strings." >&2
  exit 1
fi

reference='^[A-Z_][A-Z0-9_]*=gringotts://[A-Za-z0-9._/-]+$'
line_number=0
while IFS= read -r line || [[ -n "$line" ]]; do
  line_number=$((line_number + 1))
  line="${line%$'\r'}"
  if [[ "$line" =~ ^[[:space:]]*(#|$) ]]; then
    continue
  fi
  if [[ ! "$line" =~ $reference ]]; then
    echo "with-network-secrets: line $line_number is not a gringotts:// reference" >&2
    exit 1
  fi
done < "$ENV_FILE"

exec gringotts run --env-file "$ENV_FILE" -- "$@"
