"""Starts Commands from this folder (used by shortcuts and the Windows Terminal profile)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from commands.__main__ import main  # noqa: E402

sys.exit(main())
