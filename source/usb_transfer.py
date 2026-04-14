"""Worker thread for copying NC files to USB and USB drive detection."""

import ctypes
import logging
import shutil
from pathlib import Path

from PyQt5.QtCore import QThread, pyqtSignal

logger = logging.getLogger(__name__)

# Win32 drive type constant for removable media.
_DRIVE_REMOVABLE = 2


def detect_usb_drives() -> list[str]:
    """Return drive letters of removable USB drives (e.g. ['E:', 'F:']).

    Uses Win32 GetDriveTypeW to identify removable media.
    Skips A: and B: (legacy floppy drives).
    """
    drives: list[str] = []
    for code in range(ord("C"), ord("Z") + 1):
        letter = chr(code)
        root = f"{letter}:\\"
        if ctypes.windll.kernel32.GetDriveTypeW(root) == _DRIVE_REMOVABLE:
            drives.append(f"{letter}:")
    logger.debug("Detected USB drives: %s", drives)
    return drives


class USBTransferThread(QThread):
    """Copies .nc files to the root of a USB drive."""

    progress = pyqtSignal(str)
    finished = pyqtSignal(bool, str)

    def __init__(self, nc_files: tuple[str, ...], target_drive: str) -> None:
        super().__init__()
        self._nc_files = nc_files
        self._target = Path(target_drive + "\\")

    def run(self) -> None:
        try:
            if not self._target.exists():
                self.finished.emit(False, f"Drive {self._target} not found")
                return

            if not self._nc_files:
                self.finished.emit(False, "No NC files to copy")
                return

            total = len(self._nc_files)
            logger.info("Copying %d NC files to %s", total, self._target)

            for index, src in enumerate(self._nc_files, start=1):
                name = Path(src).name
                self.progress.emit(f"Copying file {index} of {total}: {name}")
                shutil.copy2(src, self._target / name)

            self.finished.emit(True, f"Copied {total} NC files to {self._target}")

        except Exception as exc:
            logger.exception("USB transfer failed")
            self.finished.emit(False, f"USB transfer failed: {exc}")
