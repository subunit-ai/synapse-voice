"""PyInstaller entry point.

Importing the package uses absolute paths so PyInstaller's frozen-import
machinery resolves them correctly. main.py uses relative imports that
break when invoked as a top-level script.
"""
import sys


# PyInstaller --windowed (console=False in our spec) makes sys.stdout and
# sys.stderr literally `None` instead of file-like objects.  Libraries
# that don't expect this — tqdm, huggingface_hub progress bars, onnx-asr's
# load logging — call sys.stdout.write() and crash with
# `AttributeError: 'NoneType' object has no attribute 'write'`.  TJ saw
# this in v0.5.3 on Win-ARM trying to load the ONNX Whisper model.
#
# Stub out None streams with a no-op writer so any library can write
# without crashing.  We use a real io.StringIO rather than a custom class
# so attribute introspection (encoding, isatty, etc.) returns sensible
# values for code that probes the stream before writing.
import io

class _NullStream(io.StringIO):
    """StringIO that pretends to be a real terminal for compat checks."""

    def isatty(self) -> bool:  # type: ignore[override]
        return False

    def fileno(self) -> int:  # type: ignore[override]
        # Some progress bars probe fileno() before falling back to write().
        # Raising the conventional OSError is the contract for "no fd".
        raise OSError("frozen --windowed: no real stdio fileno")

if sys.stdout is None:
    sys.stdout = _NullStream()
if sys.stderr is None:
    sys.stderr = _NullStream()

from synapse_voice.main import main

if __name__ == "__main__":
    sys.exit(main())
