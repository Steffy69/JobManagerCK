"""Worker thread for copying NC files to USB and USB drive detection."""

import ctypes
import logging
import os
import shutil
from collections import Counter
from pathlib import Path

from PyQt5.QtCore import QThread, pyqtSignal

from transfer_common import describe_failure

logger = logging.getLogger(__name__)

# Win32 drive type constant for removable media.
_DRIVE_REMOVABLE = 2

# Safety buffer demanded on top of the summed NC file sizes.
_FREE_SPACE_BUFFER_BYTES = 1024 * 1024


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


def find_duplicate_basenames(paths: tuple[str, ...]) -> dict[str, list[str]]:
    """Map each colliding basename to the full paths that share it.

    The USB copy is FLAT — every file lands in the drive root — so two
    ``.nc`` files with the same name in different job subfolders would
    silently overwrite each other, and the CNC machine would mill from the
    wrong program with no error anywhere. Callers must treat any collision
    as a hard stop.
    """
    by_name: dict[str, list[str]] = {}
    counts = Counter(Path(p).name for p in paths)
    for path in paths:
        name = Path(path).name
        if counts[name] > 1:
            by_name.setdefault(name, []).append(path)
    return by_name


class USBTransferThread(QThread):
    """Copies .nc files to the root of a USB drive."""

    progress = pyqtSignal(str)
    finished = pyqtSignal(bool, str)

    def __init__(self, nc_files: tuple[str, ...], target_drive: str) -> None:
        super().__init__()
        self._nc_files = nc_files
        self._target = Path(target_drive + "\\")

    def _check_free_space(self) -> str | None:
        """Verify the stick can hold every NC file. Returns error or None.

        The per-file ``getsize`` calls hit the S: share, which is exactly why
        this runs on the worker thread rather than in the click handler.
        """
        required = 0
        for src in self._nc_files:
            try:
                required += os.path.getsize(src)
            except OSError:
                # Missing/unreadable source: the copy loop will report it
                # per-file with a friendlier message; don't block on it here.
                continue

        try:
            free = shutil.disk_usage(self._target).free
        except OSError as exc:
            return f"Could not read the USB drive: {describe_failure(exc)}"

        if free < required + _FREE_SPACE_BUFFER_BYTES:
            required_mb = required // (1024 * 1024) + 1
            free_mb = free // (1024 * 1024)
            return (
                f"Not enough space on {self._target} — need about "
                f"{required_mb} MB but only {free_mb} MB is free. "
                "Delete some files from the USB drive and try again."
            )
        return None

    def run(self) -> None:
        try:
            if not self._target.exists():
                self.finished.emit(False, f"Drive {self._target} not found")
                return

            if not self._nc_files:
                self.finished.emit(False, "No NC files to copy")
                return

            duplicates = find_duplicate_basenames(self._nc_files)
            if duplicates:
                lines = []
                for name, paths in sorted(duplicates.items()):
                    lines.append(f"{name}:")
                    lines.extend(f"    {p}" for p in paths)
                self.finished.emit(
                    False,
                    "Cannot copy — two or more NC files share the same "
                    "name and would overwrite each other on the USB "
                    "drive.\nRename these on the S: drive first:\n\n"
                    + "\n".join(lines),
                )
                return

            space_error = self._check_free_space()
            if space_error is not None:
                self.finished.emit(False, space_error)
                return

            total = len(self._nc_files)
            logger.info("Copying %d NC files to %s", total, self._target)

            copied = 0
            failures: list[str] = []
            for index, src in enumerate(self._nc_files, start=1):
                if self.isInterruptionRequested():
                    self.finished.emit(
                        False,
                        f"Cancelled — copied {copied} of {total} NC files "
                        f"to {self._target}.",
                    )
                    return
                name = Path(src).name
                self.progress.emit(f"Copying file {index} of {total}: {name}")
                try:
                    shutil.copy2(src, self._target / name)
                    copied += 1
                except Exception as exc:  # noqa: BLE001 - collected per file
                    logger.exception("Failed to copy %s", src)
                    failures.append(f"{name}: {describe_failure(exc)}")

            if failures:
                self.finished.emit(
                    False,
                    f"Copied {copied} of {total} NC files to {self._target}. "
                    f"{len(failures)} failed:\n" + "\n".join(failures),
                )
                return

            self.finished.emit(
                True, f"Copied {total} NC files to {self._target}"
            )

        except Exception as exc:  # noqa: BLE001 - worker must never die silently
            logger.exception("USB transfer failed")
            self.finished.emit(
                False, f"USB transfer failed: {describe_failure(exc)}"
            )
