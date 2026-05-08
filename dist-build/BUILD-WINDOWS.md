# Windows build

The PyInstaller spec is cross-platform. To produce `synapse-voice.exe` you need a Windows machine (or VM / wine — not officially supported) and run:

## Prerequisites on Windows

- Python 3.12 (64-bit) from python.org
- Microsoft C++ Build Tools (for `evdev` is not needed — pynput on Windows uses `pywin32`)

## Build steps

```powershell
# 1. clone / copy the synapse-voice/ tree to the Windows machine

# 2. create venv + deps
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install pyinstaller pywin32

# 3. build
pyinstaller --clean --noconfirm dist-build\synapse-voice.spec

# Output: dist\synapse-voice\synapse-voice.exe
```

Run by double-clicking the .exe in `dist\synapse-voice\`. The console window stays hidden (`console=False` in the spec).

## Sign + installer (optional, later)

For a polished customer-distributable .exe:

- **Code signing**: Use SignTool with your Authenticode cert
- **Installer**: Wrap with [NSIS](https://nsis.sourceforge.io/) or [Inno Setup](https://jrsoftware.org/isinfo.php)

Both are out of scope for the v0.1 build — direct .exe + folder is fine for early users.

## Cross-build attempt (Linux → Windows via wine)

Untested. PyInstaller does *not* officially cross-compile. The supported workflow is to build on the target OS. If you want a CI pipeline, GitHub Actions with a `windows-latest` runner is the cleanest path.
