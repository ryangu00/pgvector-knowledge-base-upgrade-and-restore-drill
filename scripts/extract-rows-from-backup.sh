#!/usr/bin/env bash
# See docs/offline-reproduction.md for required environment and mock fixtures.
set -euo pipefail
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$SCRIPT_DIR/pg_lab.py" extract "$@"
