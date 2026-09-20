#!/usr/bin/env bash
set -euo pipefail
flight_lab_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$flight_lab_dir"
node scripts/setup.mjs hardware-upstream.json
if ! command -v uv >/dev/null; then
  echo 'Install uv from https://docs.astral.sh/uv/ before setting up hardware dependencies.' >&2
  exit 1
fi
if [[ ! -d .venv ]]; then uv venv .venv --python 3.12; fi
uv pip install --python .venv/bin/python -r requirements-hardware.txt
PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 PYTHONPATH="$flight_lab_dir/.vendor/turbodrone/backend" .venv/bin/python - <<'PY'
import cv2, fastapi, uvicorn
import web_server
from models.s2x_rc import S2xDroneModel
from protocols.s2x_rc_protocol_adapter import S2xRCProtocolAdapter
from protocols.s2x_video_protocol import S2xVideoProtocolAdapter
print('TurboDrone core imports passed. No controller, socket, or flight service was started.')
PY
echo 'Hardware source and dependencies are ready. See docs/hardware-handoff.md before connecting a drone.'
