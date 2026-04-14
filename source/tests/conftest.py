"""Test configuration shared by all JobManagerCK unit tests.

Sets ``PYTEST_QT_API=pyqt5`` before any pytest-qt / PyQt import so the
pytest-qt plugin binds to PyQt5 rather than a concurrently installed
PyQt6. Individual tests can still import plain modules safely.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("PYTEST_QT_API", "pyqt5")

# Make the ``source/`` directory importable so tests can do
# ``import printer_status_widget`` without fiddling with sys.path in each
# test file.
_SOURCE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _SOURCE_DIR not in sys.path:
    sys.path.insert(0, _SOURCE_DIR)
