"""PyInstaller entry point.

Importing the package uses absolute paths so PyInstaller's frozen-import
machinery resolves them correctly. main.py uses relative imports that
break when invoked as a top-level script.
"""
import sys

from synapse_voice.main import main

if __name__ == "__main__":
    sys.exit(main())
