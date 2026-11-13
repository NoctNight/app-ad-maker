#!/usr/bin/env bash
# Run admaker from this skill folder, creating a private virtualenv on first use.
# Usage: scripts/admaker.sh <make|providers|doctor> [args...]
set -euo pipefail
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$SKILL_DIR/.venv"
PY="${PYTHON:-python3}"

if [ ! -x "$VENV/bin/python" ]; then
  "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
    || { echo "admaker needs Python 3.10+ (found: $("$PY" --version 2>&1))" >&2; exit 1; }
  echo "First run: installing admaker into $VENV ..." >&2
  "$PY" -m venv "$VENV"
  "$VENV/bin/python" -m pip install --quiet --upgrade pip
  "$VENV/bin/python" -m pip install --quiet -e "$SKILL_DIR"
fi

# Keys come from the caller's environment or a .env in the current directory.
exec "$VENV/bin/python" -m admaker "$@"
