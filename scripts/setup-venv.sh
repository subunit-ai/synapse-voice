#!/bin/bash
# Create venv and install dependencies for Synapse Voice (Phase 1 dev).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$ROOT/.venv"

if [[ ! -d "$VENV" ]]; then
    python3 -m venv "$VENV"
fi
source "$VENV/bin/activate"
pip install --upgrade pip
pip install -r "$ROOT/requirements.txt"
echo
echo "Venv ready at: $VENV"
echo "Run: source $VENV/bin/activate && python -m synapse_voice.main"
