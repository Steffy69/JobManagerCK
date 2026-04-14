"""Worker thread for transferring CO label files to CADCode directories."""

import logging
import shutil
from pathlib import Path

from PyQt5.QtCore import QThread, pyqtSignal

logger = logging.getLogger(__name__)


class FileTransferThread(QThread):
    """Copies .mdb label data and .wmf image files to CADCode directories."""

    progress = pyqtSignal(str)
    finished = pyqtSignal(bool, str)

    def __init__(
        self,
        mdb_files: tuple[str, ...],
        wmf_files: tuple[str, ...],
        dest_base: str = r"C:\CADCode",
    ) -> None:
        super().__init__()
        self._mdb_files = mdb_files
        self._wmf_files = wmf_files
        self._dest_base = Path(dest_base)

    def run(self) -> None:
        try:
            label_dir = self._dest_base / "Label Data"
            pix_dir = self._dest_base / "Pix"

            # Ensure destination directories exist
            label_dir.mkdir(parents=True, exist_ok=True)
            pix_dir.mkdir(parents=True, exist_ok=True)

            # Clear Label Data directory
            self.progress.emit("Clearing Label Data...")
            for item in label_dir.iterdir():
                if item.is_file():
                    item.unlink()
            logger.info("Cleared Label Data directory: %s", label_dir)

            # Copy .mdb files into Label Data
            self.progress.emit(f"Copying {len(self._mdb_files)} label files...")
            for src in self._mdb_files:
                shutil.copy2(src, label_dir / Path(src).name)
            logger.info("Copied %d .mdb files to %s", len(self._mdb_files), label_dir)

            # Copy .wmf files into Pix (merge with overwrite)
            self.progress.emit(f"Copying {len(self._wmf_files)} image files...")
            for src in self._wmf_files:
                shutil.copy2(src, pix_dir / Path(src).name)
            logger.info("Copied %d .wmf files to %s", len(self._wmf_files), pix_dir)

            total = len(self._mdb_files) + len(self._wmf_files)
            self.finished.emit(True, f"Transferred {total} files to CADCode")

        except Exception as exc:
            logger.exception("File transfer failed")
            self.finished.emit(False, f"Transfer failed: {exc}")
