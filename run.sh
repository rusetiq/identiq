#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
if ! .venv/bin/python -c 'import flask, cv2, numpy, PIL, flask_sock' >/dev/null 2>&1; then
  .venv/bin/python -m ensurepip --upgrade
  .venv/bin/python -m pip install -r requirements.txt
fi
.venv/bin/python scripts/download_models.py
echo "Identiq is available at http://localhost:${PORT:-8080}"
echo "Initial staff login: data/staff-login.json (keep this file private)"
exec .venv/bin/python app.py
