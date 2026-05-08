#!/bin/bash
# Build a self-contained Linux distribution bundle for Synapse Voice.
# Outputs:  dist/synapse-voice/  (run synapse-voice/synapse-voice)
#           dist/synapse-voice-linux-x86_64.tar.gz
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -d ".venv" ]]; then
    echo "ERROR: run scripts/setup-venv.sh first" >&2
    exit 1
fi

source .venv/bin/activate

pip install --quiet pyinstaller

rm -rf build dist
pyinstaller --clean --noconfirm dist-build/synapse-voice.spec

if [[ ! -f "dist/synapse-voice/synapse-voice" ]]; then
    echo "ERROR: PyInstaller did not produce the expected binary" >&2
    exit 1
fi

# Tarball for distribution
ARCHIVE="dist/synapse-voice-linux-x86_64.tar.gz"
tar -C dist -czf "$ARCHIVE" synapse-voice
size=$(du -h "$ARCHIVE" | cut -f1)

echo
echo "Built: dist/synapse-voice/synapse-voice"
echo "Bundle: $ARCHIVE ($size)"
echo
echo "Run:   ./dist/synapse-voice/synapse-voice"
echo "Ship:  scp $ARCHIVE user@host:/path/"
