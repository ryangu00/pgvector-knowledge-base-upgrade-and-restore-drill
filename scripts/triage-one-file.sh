#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$SCRIPT_DIR/dual_tests.py" "${INTEGRATION_TREE:?}" "${PRISTINE_TREE:?}" --file "${1:?test file required}"
