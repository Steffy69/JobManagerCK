"""Worker thread for transferring CO label files to CADCode directories.

The transfer is DESTRUCTIVE by design — CADCode must only ever see the
current job's label data, so ``Label Data`` is emptied before the new
``.mdb`` files land. To make that safe the copy is two-phase:

1. **Stage**: every ``.mdb`` is copied from the S: drive into a staging
   subfolder inside ``Label Data``. A failure here (network blip, locked
   file) aborts the whole transfer with the OLD data completely untouched.
2. **Commit**: only after every file staged successfully, the old files are
   removed and the staged files moved into place — local same-volume moves,
   near-atomic and effectively instant.

``Pix`` is a merge: images are copied over, but files whose size and mtime
already match the source are skipped (re-transfers of the same job would
otherwise re-pull every image over the network for nothing).
"""

import logging
import os
import shutil
import tempfile
from pathlib import Path

from PyQt5.QtCore import QThread, pyqtSignal

from transfer_common import describe_failure, files_identical

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

    # ------------------------------------------------------------------
    # Steps (split out so each failure mode is separately reportable)
    # ------------------------------------------------------------------

    def _check_sources_reachable(self) -> str | None:
        """Fast-fail when the sources' network drive is gone.

        Returns an error message, or None when everything looks reachable.
        Runs on this worker thread, so a hung SMB probe stalls the transfer
        (which shows "busy" and stays cancellable) rather than the GUI.
        """
        roots = {
            Path(src).anchor
            for src in (*self._mdb_files, *self._wmf_files)
            if Path(src).anchor
        }
        for root in sorted(roots):
            if not os.path.isdir(root):
                return (
                    f"The drive {root} is not reachable. Check the network "
                    "connection, then try again. Nothing was changed."
                )
        return None

    def _stage_mdb_files(self, staging_dir: Path) -> list[str]:
        """Copy every .mdb into the staging dir. Returns failure lines."""
        failures: list[str] = []
        total = len(self._mdb_files)
        for index, src in enumerate(self._mdb_files, start=1):
            if self.isInterruptionRequested():
                failures.append("Cancelled by user.")
                return failures
            self.progress.emit(
                f"Copying label file {index} of {total}: {Path(src).name}"
            )
            try:
                shutil.copy2(src, staging_dir / Path(src).name)
            except Exception as exc:  # noqa: BLE001 - reported per file
                logger.exception("Failed to stage %s", src)
                failures.append(
                    f"{Path(src).name}: {describe_failure(exc)}"
                )
                # One failure aborts staging — the commit is all-or-nothing,
                # so there is no point pulling the remaining files.
                return failures
        return failures

    def _commit_label_data(self, label_dir: Path, staging_dir: Path) -> None:
        """Replace Label Data's contents with the staged files."""
        self.progress.emit("Installing new label data...")
        for item in os.scandir(label_dir):
            if item.is_file():
                os.unlink(item.path)
        for item in os.scandir(staging_dir):
            os.replace(item.path, label_dir / item.name)
        logger.info("Committed %d .mdb files to %s",
                    len(self._mdb_files), label_dir)

    def _copy_pix_files(self, pix_dir: Path) -> tuple[int, int, list[str]]:
        """Merge .wmf images into Pix. Returns (copied, skipped, failures)."""
        copied = 0
        skipped = 0
        failures: list[str] = []
        total = len(self._wmf_files)
        for index, src in enumerate(self._wmf_files, start=1):
            if self.isInterruptionRequested():
                failures.append("Cancelled by user.")
                break
            dest = pix_dir / Path(src).name
            if files_identical(src, str(dest)):
                skipped += 1
                continue
            self.progress.emit(
                f"Copying image {index} of {total}: {Path(src).name}"
            )
            try:
                shutil.copy2(src, dest)
                copied += 1
            except Exception as exc:  # noqa: BLE001 - collected per file
                logger.exception("Failed to copy %s", src)
                failures.append(f"{Path(src).name}: {describe_failure(exc)}")
        return copied, skipped, failures

    # ------------------------------------------------------------------
    # Thread entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        staging_dir: Path | None = None
        try:
            unreachable = self._check_sources_reachable()
            if unreachable is not None:
                self.finished.emit(False, unreachable)
                return

            label_dir = self._dest_base / "Label Data"
            pix_dir = self._dest_base / "Pix"
            label_dir.mkdir(parents=True, exist_ok=True)
            pix_dir.mkdir(parents=True, exist_ok=True)

            # Phase 1: stage. The old label data is untouched until every
            # new file has arrived safely.
            staging_dir = Path(
                tempfile.mkdtemp(prefix=".staging_", dir=label_dir)
            )
            stage_failures = self._stage_mdb_files(staging_dir)
            if stage_failures:
                self.finished.emit(
                    False,
                    "Label transfer stopped — the old label data is "
                    "untouched.\n\n" + "\n".join(stage_failures),
                )
                return

            # Phase 2: commit (local, near-instant).
            self._commit_label_data(label_dir, staging_dir)

            copied, skipped, pix_failures = self._copy_pix_files(pix_dir)

            mdb_total = len(self._mdb_files)
            if pix_failures:
                self.finished.emit(
                    False,
                    f"Label data updated ({mdb_total} files), but "
                    f"{len(pix_failures)} of {len(self._wmf_files)} images "
                    "failed:\n" + "\n".join(pix_failures),
                )
                return

            summary = f"Transferred {mdb_total} label files"
            if copied or skipped:
                summary += f" and {copied} images"
                if skipped:
                    summary += f" ({skipped} unchanged, skipped)"
            summary += " to CADCode"
            self.finished.emit(True, summary)

        except Exception as exc:  # noqa: BLE001 - worker must never die silently
            logger.exception("File transfer failed")
            self.finished.emit(
                False, f"Transfer failed: {describe_failure(exc)}"
            )
        finally:
            if staging_dir is not None:
                shutil.rmtree(staging_dir, ignore_errors=True)
