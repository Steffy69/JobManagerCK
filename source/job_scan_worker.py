"""Background job scanning for JobManagerCK.

``scan_jobs`` / ``scan_printed_jobs`` walk every job folder on the ``S:``
share. That is a network traversal whose latency is bounded only by the SMB
timeout, so running it on the Qt GUI thread stalls painting and input for as
long as it takes — and the auto-refresh timer repeats it every few seconds.

This module runs that scan on a worker thread instead, following the same
``QThread``-per-operation pattern the app already uses for file transfer and
label printing. The GUI thread only ever touches the *result*.
"""

import logging

from PyQt5.QtCore import QThread, pyqtSignal

from job_scanner import scan_jobs, scan_printed_jobs

logger = logging.getLogger(__name__)


class JobScanThread(QThread):
    """Scans the active and printed job folders off the GUI thread.

    Emits exactly one of:

    ``scanned(active_jobs, printed_jobs)``
        Both scans completed. Either list may be empty.
    ``failed(message)``
        The active scan raised. The tree is left as-is by the caller so a
        transient S: drive blip never blanks a populated list.

    A failure of the *printed* scan alone is not fatal — it is logged and
    reported as an empty printed list, matching the previous inline
    behaviour where each scan had its own try/except.
    """

    scanned = pyqtSignal(list, list)
    failed = pyqtSignal(str)

    def __init__(self, scan_active=None, scan_printed=None, parent=None) -> None:
        """Create a scan thread.

        The two scan callables are injectable so the caller controls which
        implementation runs — the main window passes its own module-level
        ``scan_jobs`` / ``scan_printed_jobs`` references, which keeps them
        patchable by tests. Defaults are the real ``job_scanner`` functions.
        """
        super().__init__(parent)
        self._scan_active = scan_active or scan_jobs
        self._scan_printed = scan_printed or scan_printed_jobs

    def run(self) -> None:  # noqa: D102 - QThread override
        try:
            active = self._scan_active()
        except Exception as exc:  # noqa: BLE001 - must not kill the thread
            logger.exception("Failed to scan job folders")
            self.failed.emit(str(exc))
            return

        try:
            printed = self._scan_printed()
        except Exception:  # noqa: BLE001 - printed folder is non-critical
            logger.exception("Failed to scan printed job folder")
            printed = []

        self.scanned.emit(list(active), list(printed))
