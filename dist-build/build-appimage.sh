#!/bin/bash
# Wrap the PyInstaller bundle into an AppImage for Linux.
#
# Prereqs:
#   - bash dist-build/build-linux.sh has been run (creates dist/synapse-voice/)
#   - linuxdeploy and appimagetool downloaded to ~/.local/bin (auto-fetched if missing)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -d "dist/synapse-voice" ]]; then
    echo "→ PyInstaller bundle missing, building first..."
    bash dist-build/build-linux.sh
fi

LOCAL_BIN="$HOME/.local/bin"
mkdir -p "$LOCAL_BIN"

APPIMAGETOOL="$LOCAL_BIN/appimagetool"
if [[ ! -x "$APPIMAGETOOL" ]]; then
    echo "→ Downloading appimagetool..."
    curl -fsSL -o "$APPIMAGETOOL" \
        "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage"
    chmod +x "$APPIMAGETOOL"
fi

APPDIR="dist/SynapseVoice.AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/share/applications" "$APPDIR/usr/share/icons/hicolor/256x256/apps"

# Copy PyInstaller bundle
cp -r dist/synapse-voice/* "$APPDIR/usr/bin/"

# AppRun launcher
cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/bash
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/synapse-voice" "$@"
EOF
chmod +x "$APPDIR/AppRun"

# .desktop file
cat > "$APPDIR/synapse-voice.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Synapse Voice
Comment=Hotkey-driven speech-to-text for the subunit ecosystem
Exec=synapse-voice
Icon=synapse-voice
Categories=Utility;AudioVideo;
Terminal=false
StartupNotify=false
EOF
cp "$APPDIR/synapse-voice.desktop" "$APPDIR/usr/share/applications/"

# Icon — generate a simple cyan circle PNG via Python.
# Prefer .venv if it exists (local dev), otherwise fall back to system python (CI).
if [[ -x "$ROOT/.venv/bin/python" ]]; then
    PYTHON_BIN="$ROOT/.venv/bin/python"
else
    PYTHON_BIN="$(command -v python3 || command -v python)"
fi
"$PYTHON_BIN" - <<'PY'
from pathlib import Path
try:
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QColor, QPainter, QPixmap
    from PyQt6.QtWidgets import QApplication
    import os, sys
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication(sys.argv)
    pix = QPixmap(256, 256)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(2, 8, 23))
    p.drawEllipse(8, 8, 240, 240)
    p.setBrush(QColor(64, 214, 255))
    p.drawEllipse(72, 72, 112, 112)
    p.end()
    out = Path("dist/SynapseVoice.AppDir/synapse-voice.png")
    pix.save(str(out))
    pix.save("dist/SynapseVoice.AppDir/usr/share/icons/hicolor/256x256/apps/synapse-voice.png")
    print(f"icon: {out}")
except Exception as e:
    print(f"icon-gen skipped: {e}")
PY

cd dist
# --appimage-extract-and-run: avoid FUSE dependency (works in containers / GH Actions)
ARCH=x86_64 "$APPIMAGETOOL" --appimage-extract-and-run SynapseVoice.AppDir SynapseVoice-x86_64.AppImage
chmod +x SynapseVoice-x86_64.AppImage
size=$(du -h SynapseVoice-x86_64.AppImage | cut -f1)
cd "$ROOT"

echo
echo "✅ AppImage: dist/SynapseVoice-x86_64.AppImage ($size)"
echo "Run:   ./dist/SynapseVoice-x86_64.AppImage"
