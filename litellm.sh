#!/bin/bash
set -euo pipefail

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
VENV="$DIR/.venv"
CLI="$DIR/cli.py"
REQS="$DIR/requirements.txt"

# --- Pre-flight checks ---

if ! command -v python3 &>/dev/null; then
    echo "Error: python3 is not installed. Install Python 3 and try again."
    exit 1
fi

if [ ! -f "$CLI" ]; then
    echo "Error: cli.py not found at $CLI"
    echo "  The Python CLI is missing. Re-clone or restore the file."
    exit 1
fi

# --- Venv management ---

# Recreate venv if Python version changed or venv is broken
_venv_ok() {
    [ -d "$VENV" ] && [ -x "$VENV/bin/python" ] && "$VENV/bin/python" --version &>/dev/null
}

if ! _venv_ok; then
    echo "Setting up Python environment..."
    rm -rf "$VENV"
    python3 -m venv "$VENV" || { echo "Error: Failed to create Python virtual environment."; exit 1; }
    "$VENV/bin/pip" install -q -r "$REQS" || { echo "Error: Failed to install dependencies."; exit 1; }
fi

# Quick dep check: try importing all required packages
# If any import fails, reinstall. Fast path (~50ms) when deps are satisfied.
_deps_ok() {
    "$VENV/bin/python" -c "
import importlib, sys
for pkg in ['yaml', 'requests']:
    try:
        importlib.import_module(pkg)
    except ImportError:
        sys.exit(1)
" 2>/dev/null
}

if ! _deps_ok; then
    echo "Installing dependencies..."
    "$VENV/bin/pip" install -q -r "$REQS" || { echo "Error: Failed to install dependencies."; exit 1; }
fi

# --- Load .env into host environment so providers can read env vars ---

if [ -f "$DIR/.env" ]; then
    while IFS='=' read -r key value; do
        # Skip comments and blank lines
        [[ -z "$key" || "$key" =~ ^[[:space:]]*# ]] && continue
        # Strip surrounding quotes
        value="${value%\"}"
        value="${value#\"}"
        value="${value%\'}"
        value="${value#\'}"
        export "$key=$value"
    done < "$DIR/.env"
fi

# --- Forward to Python CLI ---

export LITELLM_CLI_NAME="./litellm.sh"
exec "$VENV/bin/python" "$CLI" "$@"
