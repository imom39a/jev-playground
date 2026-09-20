#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "${project_dir}/scripts/load-jev-env.sh"

curl --silent --show-error --fail-with-body \
  --request POST "https://api.typesafe.ai/v1/systemone" \
  --header "Authorization: Bearer ${TYPESAFE_API_KEY}" \
  --header "Content-Type: application/json" \
  --data-binary @- <<'JSON' | python3 -m json.tool
{
  "state": {
    "message": "Checkout has failed for three hours and customers cannot pay."
  },
  "model": "jev-latest",
  "questions": {
    "is_urgent": {
      "type": "noul",
      "instructions": "Does `message` describe an urgent problem?"
    },
    "owner": {
      "type": "choice",
      "instructions": "Which team should own the problem described in `message`?",
      "criteria": {
        "billing": "Account charges, invoices, or refunds",
        "engineering": "A product or integration failure",
        "other": "Neither billing nor engineering"
      }
    }
  }
}
JSON
