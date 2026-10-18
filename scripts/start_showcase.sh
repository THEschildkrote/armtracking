#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
PYTHON_BIN=${PYTHON_BIN:-python3}
exec "$PYTHON_BIN" -m sponsors.showcase.server --config sponsors/config.local.json "$@"
