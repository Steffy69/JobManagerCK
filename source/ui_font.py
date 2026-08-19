"""Application-wide font scaling (accessibility).

One knob: the app font's point size. Applied before the main window is
built (so every widget lays out at the right size from the start) and
re-applied live when the user changes it in Print Settings — Qt propagates
an application-font change to existing widgets that haven't set an
explicit font of their own.
"""

from __future__ import annotations

import logging
from typing import Optional

from PyQt5.QtWidgets import QApplication

logger = logging.getLogger(__name__)

#: The platform's original font size, captured the first time we change
#: anything so "system default" (setting value 0) can always be restored.
_system_default_size: Optional[int] = None


def apply_ui_font_size(size: int) -> None:
    """Set the app-wide font point size. ``0`` restores the system default.

    Safe no-op when no QApplication exists (e.g. under import in tests).
    """
    global _system_default_size

    app = QApplication.instance()
    if app is None:
        return

    font = app.font()
    if _system_default_size is None:
        _system_default_size = font.pointSize()

    target = size if size > 0 else _system_default_size
    if target is None or target <= 0 or font.pointSize() == target:
        return

    font.setPointSize(target)
    app.setFont(font)
    logger.info("Applied UI font size: %dpt", target)
