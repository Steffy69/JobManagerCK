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


class DropZone(QFrame):
    """A drop target that accepts any directory and emits its path."""

    fileDropped = pyqtSignal(str)

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
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setStyleSheet(STYLE_DRAG_OVER)

    def dragLeaveEvent(self, event) -> None:
        self.setStyleSheet(STYLE_NORMAL)

    def dropEvent(self, event) -> None:
        files: list[str] = [u.toLocalFile() for u in event.mimeData().urls()]
        if files and os.path.isdir(files[0]):
            logger.info("Folder dropped: %s", files[0])
            self.fileDropped.emit(files[0])
        self.dragLeaveEvent(event)
