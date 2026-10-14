#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "Run ./run.sh once to install the app dependencies."
  exit 1
fi
.venv/bin/python scripts/download_models.py
exec .venv/bin/python scripts/watch_network.py "$@"
