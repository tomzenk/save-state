#!/usr/bin/env bash
set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$(command -v python3 || command -v python || true)"
[ -n "$PYTHON" ] || exit 0
exec "$PYTHON" "$SCRIPT_DIR/../scripts/state.py" hook --root "${CLAUDE_PROJECT_DIR:-$PWD}"
