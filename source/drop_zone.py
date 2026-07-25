"""Drag-and-drop zone widget for accepting job folder drops."""

import os
import logging

from PyQt5.QtCore import pyqtSignal, Qt
from PyQt5.QtWidgets import QFrame, QVBoxLayout, QLabel

logger = logging.getLogger(__name__)

STYLE_NORMAL = """
    QFrame {
        border: 2px dashed #aaa;
        border-radius: 5px;
        background-color: #f0f0f0;
        min-height: 80px;
    }
    QFrame:hover {
        border-color: #777;
        background-color: #e8e8e8;
    }
"""

STYLE_DRAG_OVER = """
    QFrame {
        border: 2px solid #4CAF50;
        border-radius: 5px;
        background-color: #e8f5e9;
        min-height: 80px;
    }
"""


def _local_directories(mime_data) -> list[str]:
    """Extract the local DIRECTORY paths from a drag's mime data.

    Files, Outlook virtual items and other non-filesystem drops resolve to
    an empty ``toLocalFile()`` or a non-directory and are excluded.
    """
    dirs: list[str] = []
    for url in mime_data.urls():
        path = url.toLocalFile()
        if path and os.path.isdir(path):
            dirs.append(path)
    return dirs


class DropZone(QFrame):
    """A drop target that accepts job FOLDER drops and emits their paths.

    The old behaviour lit up green for any URL drag and then silently
    ignored files and second-and-later folders — the operator saw the app
    "accept" a drop and do nothing. Now the drag is only accepted when it
    contains at least one folder (the OS shows the no-drop cursor
    otherwise), every dropped folder is emitted, and a rejected drop says
    why via ``dropRejected``.
    """

    fileDropped = pyqtSignal(str)
    dropRejected = pyqtSignal(str)  # human-readable reason

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setFrameStyle(QFrame.StyledPanel)
        self.setStyleSheet(STYLE_NORMAL)

        layout = QVBoxLayout()
        self.label = QLabel("Drag and drop a job folder here")
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setStyleSheet("color: #666;")
        layout.addWidget(self.label)
        self.setLayout(layout)

    def dragEnterEvent(self, event) -> None:
        if _local_directories(event.mimeData()):
            event.acceptProposedAction()
            self.setStyleSheet(STYLE_DRAG_OVER)

    def dragLeaveEvent(self, event) -> None:
        self.setStyleSheet(STYLE_NORMAL)

    def dropEvent(self, event) -> None:
        dirs = _local_directories(event.mimeData())
        if dirs:
            for path in dirs:
                logger.info("Folder dropped: %s", path)
                self.fileDropped.emit(path)
        else:
            self.dropRejected.emit(
                "That was a file — drop the job's folder instead"
            )
        self.dragLeaveEvent(event)
