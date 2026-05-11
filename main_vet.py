"""PyInstaller entry point for the Vet-mode build.

Inserts ``--vet`` into ``sys.argv`` before delegating to ``main.main`` so
the Vet-mode kiosk EXE behaves identically to ``python main.py --vet``.
"""

import sys

if "--vet" not in sys.argv and "--mode" not in sys.argv:
    sys.argv.insert(1, "--vet")

from main import main

if __name__ == "__main__":
    main()
