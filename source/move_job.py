"""Worker thread for moving a job folder (Move to Printed / Restore).

Same-volume moves on the S: share are a single rename and effectively
instant — but a DROPPED job can live anywhere (desktop, another share), and
``shutil.move`` silently degrades to copy-everything-then-delete across
volumes. That can take minutes over SMB, so the move always runs on a
worker thread with the usual busy UI.
"""

from __future__ import annotations

import logging
import os
import shutil

from PyQt5.QtCore import QThread, pyqtSignal

from transfer_common import describe_failure

logger = logging.getLogger(__name__)


class MoveJobThread(QThread):
    """Moves one folder, refusing to merge into an existing destination."""

    finished = pyqtSignal(bool, str)  # (success, message)

    def __init__(self, src: str, dest: str) -> None:
        super().__init__()
        self._src = src
        self._dest = dest

    def run(self) -> None:
        try:
            if os.path.exists(self._dest):
                self.finished.emit(
                    False,
                    f"A folder named '{os.path.basename(self._dest)}' "
                    f"already exists at the destination:\n{self._dest}\n\n"
                    "Rename or remove it first.",
                )
                return

            parent = os.path.dirname(self._dest)
            if parent:
                os.makedirs(parent, exist_ok=True)

            shutil.move(self._src, self._dest)
            logger.info("Moved %s -> %s", self._src, self._dest)
            self.finished.emit(True, self._dest)

        except Exception as exc:  # noqa: BLE001 - worker must never die silently
            logger.exception("Move failed: %s -> %s", self._src, self._dest)
            self.finished.emit(
                False,
                f"Could not move '{os.path.basename(self._src)}': "
                f"{describe_failure(exc)}",
            )
