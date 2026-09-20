#!/usr/bin/env bash

# Source this file from the repository root before using a TypeSafe SDK:
#   source scripts/load-jev-env.sh

_jev_project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -f "${_jev_project_dir}/.env" ]]; then
  # Parse dotenv syntax (including spaces around '=') without executing it as
  # shell code. NUL-delimited entries preserve spaces and quotes in credentials.
  while IFS= read -r -d '' _jev_env_entry; do
    export "$_jev_env_entry"
  done < <(node --input-type=module - "${_jev_project_dir}/.env" <<'NODE'
import { readFileSync } from 'node:fs';
import { parseEnv } from 'node:util';
const values = parseEnv(readFileSync(process.argv[2], 'utf8'));
for (const [key, value] of Object.entries(values)) {
  if (/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) process.stdout.write(`${key}=${value}\0`);
}
NODE
  )
  unset _jev_env_entry
fi

if [[ -z "${TYPESAFE_API_KEY:-}" && -n "${JEV_API_KEY:-}" ]]; then
  export TYPESAFE_API_KEY="${JEV_API_KEY}"
fi

if [[ -z "${TYPESAFE_API_KEY:-}" ]]; then
  echo "Missing TYPESAFE_API_KEY (or JEV_API_KEY) in ${_jev_project_dir}/.env" >&2
  return 1 2>/dev/null || exit 1
fi

unset _jev_project_dir
