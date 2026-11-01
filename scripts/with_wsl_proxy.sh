#!/usr/bin/env bash
# Apply an explicitly configured proxy to one WSL command. No profile is edited.
set -euo pipefail

if [[ $# -eq 0 ]]; then
  printf 'Usage: bash scripts/with_wsl_proxy.sh COMMAND [ARG ...]\n' >&2
  exit 2
fi

if [[ -z "${VISTA_AUDIT_PROXY:-}" ]]; then
  exec "$@"
fi

export http_proxy="$VISTA_AUDIT_PROXY"
export https_proxy="$VISTA_AUDIT_PROXY"
export HTTP_PROXY="$VISTA_AUDIT_PROXY"
export HTTPS_PROXY="$VISTA_AUDIT_PROXY"
export no_proxy="${no_proxy:-localhost,127.0.0.1,::1}"
export NO_PROXY="${NO_PROXY:-$no_proxy}"
exec "$@"
