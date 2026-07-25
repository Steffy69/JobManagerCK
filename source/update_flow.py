"""Auto-update UI flow for the main window.

Owns the checker/downloader threads and the dialogs between "a release
exists" and "restart to apply". Kept out of job_manager.py so the window
stays a coordinator; kept out of updater.py so that module stays free of
widget code.
"""

from __future__ import annotations

import logging

from PyQt5.QtCore import QObject, Qt
from PyQt5.QtWidgets import QMessageBox, QProgressDialog, QWidget

from updater import (
    CURRENT_VERSION,
    DOWNLOAD_CANCELLED,
    UpdateChecker,
    UpdateDownloader,
    apply_update,
)

logger = logging.getLogger(__name__)


class UpdateFlow(QObject):
    """Drives check → prompt → download → apply against a parent window."""

    def __init__(self, window: QWidget, statusbar) -> None:
        super().__init__(window)
        self._window = window
        self._statusbar = statusbar
        self._checker: UpdateChecker | None = None
        self._downloader: UpdateDownloader | None = None

    # -- checking ------------------------------------------------------

    def check(self, force: bool = True) -> None:
        """Start an update check. ``force=False`` is the quiet startup path
        that respects the 4-hour check cache; the Help menu forces a real
        check and always answers."""
        # Rebinding the checker while a check is in flight would drop the
        # only reference to a running QThread and PyQt would destroy it
        # mid-run. The Help menu item and the start-up check share this
        # path.
        if self._checker is not None and self._checker.isRunning():
            return

        self._statusbar.showMessage("Checking for updates...")
        self._checker = UpdateChecker(force=force)
        self._checker.update_available.connect(self._on_update_available)
        self._checker.up_to_date.connect(
            lambda latest: self._statusbar.showMessage(
                f"Job Manager is up to date (v{CURRENT_VERSION})"
            ),
        )
        self._checker.error.connect(
            lambda msg: self._statusbar.showMessage(
                "Could not check for updates"
            ),
        )
        self._checker.start()

    # -- downloading ---------------------------------------------------

    def _on_update_available(self, info: dict) -> None:
        self._statusbar.showMessage("Update available!")
        notes = info.get("release_notes") or "No release notes available."
        reply = QMessageBox.question(
            self._window,
            "Update Available",
            f"Version {info['version']} is available!\n\n"
            f"Release notes:\n{notes}\n\n"
            "Would you like to download and install it?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self._download(info)

    def _download(self, info: dict) -> None:
        # Same guard as the checker: a second click during a running
        # download must not rebind the only reference to a live QThread.
        if self._downloader is not None and self._downloader.isRunning():
            return

        progress = QProgressDialog(
            "Downloading update...", "Cancel", 0, 100, self._window
        )
        progress.setWindowModality(Qt.WindowModal)
        progress.setAutoClose(False)

        self._downloader = UpdateDownloader(
            download_url=info.get("download_url", ""),
            expected_size=info.get("size", 0),
        )
        self._downloader.progress.connect(progress.setValue)
        # Cancel actually cancels: the worker checks the interruption flag
        # in its chunk loop and deletes the partial file.
        progress.canceled.connect(self._downloader.requestInterruption)
        self._downloader.finished.connect(
            lambda ok, result: self._on_download_finished(
                ok, result, progress
            ),
        )
        self._downloader.start()

    def _on_download_finished(
        self, success: bool, result: str, progress_dialog: QProgressDialog,
    ) -> None:
        progress_dialog.close()
        if success:
            reply = QMessageBox.question(
                self._window,
                "Update Downloaded",
                "Update downloaded successfully!\n\n"
                "The application will now restart to apply the update.",
                QMessageBox.Ok | QMessageBox.Cancel,
            )
            if reply == QMessageBox.Ok:
                try:
                    apply_update(result)
                except RuntimeError as exc:
                    # Refused outside the frozen app (would overwrite
                    # python.exe when run from source).
                    QMessageBox.critical(
                        self._window, "Update Failed", str(exc)
                    )
        elif result == DOWNLOAD_CANCELLED:
            self._statusbar.showMessage("Update cancelled")
        else:
            QMessageBox.critical(
                self._window,
                "Download Failed",
                f"Failed to download update:\n{result}",
            )

    # -- teardown ------------------------------------------------------

    def shutdown(self) -> None:
        """Stop any in-flight download before the window closes."""
        if self._downloader is not None and self._downloader.isRunning():
            self._downloader.requestInterruption()
            self._downloader.wait(5000)
