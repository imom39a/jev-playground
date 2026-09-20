#!/usr/bin/env bash
set -euo pipefail
flight_lab_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$flight_lab_dir/.."
if ! source scripts/load-jev-env.sh; then
  echo "Starting Flight Lab in offline mode; the simulator needs no API key." >&2
fi
cd "$flight_lab_dir"
if [[ -f .env ]]; then
  exec node --env-file=.env server/index.mjs "$@"
fi
exec node server/index.mjs "$@"
