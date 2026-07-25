"""Main window for JobManagerCK v2.1.

Manages job files from S drive for Continental Kitchens workshop.
Supports Cabinetry Online and Custom Design job workflows.
"""

import logging
import os
import shutil
import sys
from typing import Optional

try:
    import winsound  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - non-Windows hosts (dev boxes, CI)
    # Matches the guarded-import convention in printer_service: the module
    # must stay importable off Windows so the test suite can construct the
    # window. Sound is cosmetic, so its absence is a silent no-op.
    winsound = None  # type: ignore[assignment]

from PyQt5 import uic
from PyQt5.QtCore import QEvent, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QIcon
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QInputDialog,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QTreeWidgetItem,
)

import preflight
import print_sequencer
import printer_service
from drop_zone import DropZone
from file_transfer import FileTransferThread
from job_scan_worker import JobScanThread
from job_scanner import PRINTED_DIR, Job, scan_jobs, scan_printed_jobs
from job_types import JobFiles, JobType, build_display_name, detect_job_type, scan_folder_files
from label_printer import LabelPrinterThread
from preflight import (
    check_cadcode_free_space,
    check_printer_available,
    check_s_drive_reachable,
    check_usb_free_space,
    estimate_nc_files_size,
)
from print_order_dialog import PrintOrderDialog
from printer_status_widget import PrinterStatusWidget
from settings import AppSettings, load_settings, save_settings, update_settings
from settings_dialog import SettingsDialog
from status_service import NullJobStatusService
from transfer_history import TransferHistory
from updater import (
    CURRENT_VERSION,
    UpdateChecker,
    UpdateDownloader,
    apply_update,
)
from usb_transfer import USBTransferThread, detect_usb_drives

logger = logging.getLogger(__name__)

# Colour constants for job status
COLOR_READY = QColor(0, 128, 0)              # green — no actions taken
COLOR_IN_PROGRESS = QColor(0, 0, 200)        # blue — at least one action taken
COLOR_PRINTED_FINAL = QColor(120, 120, 120)  # grey — moved to Printed folder
COLOR_DEFAULT = QColor(0, 0, 0)              # black — fallback

SOURCE_FOLDERS = [
    r"S:\Jobs\Cabinetry Online",
    r"S:\Jobs\Custom Design",
]
DEST_PATH = r"C:\CADCode"
ARCHIVE_PATH_LEGACY = r"S:\Jobs\Archive"
PRINTED_PATH = PRINTED_DIR  # re-export for any external callers that import PRINTED_PATH
AUTO_REFRESH_MS = 5000  # Poll S drive every 5 seconds

# NestLabel integration (placeholder — uncomment and set path when confirmed)
# NESTLABEL_EXE = r"C:\Program Files\NestLabel\NestLabel.exe"


def _resource_path(filename: str) -> str:
    """Resolve a resource file path for both bundled and script modes."""
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, filename)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)


def _migrate_archive_to_printed() -> Optional[str]:
    """Auto-migrate legacy ``S:\\Jobs\\Archive`` to ``S:\\Jobs\\Printed``.

    Returns a user-facing warning string if manual intervention is required
    (both folders exist), or ``None`` on success / no-op. Never raises — any
    unexpected error is logged and returned as a warning so the app can
    continue to launch.
    """
    try:
        archive_exists = os.path.isdir(ARCHIVE_PATH_LEGACY)
        printed_exists = os.path.isdir(PRINTED_PATH)

        if archive_exists and not printed_exists:
            os.rename(ARCHIVE_PATH_LEGACY, PRINTED_PATH)
            logger.info(
                "Migrated %s -> %s", ARCHIVE_PATH_LEGACY, PRINTED_PATH
            )
            return None

        if archive_exists and printed_exists:
            warning = (
                f"Both {ARCHIVE_PATH_LEGACY} and {PRINTED_PATH} exist.\n"
                "Automatic migration was skipped to avoid overwriting files.\n"
                "Please review and merge them manually."
            )
            logger.warning(warning)
            return warning

        if not printed_exists:
            os.makedirs(PRINTED_PATH, exist_ok=True)
            logger.info("Created %s", PRINTED_PATH)
        return None
    except OSError as exc:
        logger.exception("Archive->Printed migration failed: %s", exc)
        return f"Could not migrate Archive to Printed: {exc}"


def _beep(success: bool) -> None:
    """Play the OK / error system sound, if the platform provides one."""
    if winsound is None:
        return
    winsound.MessageBeep(
        winsound.MB_OK if success else winsound.MB_ICONHAND
    )


def _build_tooltip(files: JobFiles) -> str:
    """Build a tooltip string showing file counts for a job."""
    parts: list[str] = []
    if files.nc_files:
        parts.append(f"{len(files.nc_files)} NC files")
    if files.mdb_files:
        parts.append(f"{len(files.mdb_files)} MDB files")
    if files.wmf_files:
        parts.append(f"{len(files.wmf_files)} WMF files")
    if files.ljd_files:
        parts.append(f"{len(files.ljd_files)} LJD files")
    return ", ".join(parts) if parts else "No recognised files"


class JobManager(QMainWindow):
    """Main application window for JobManagerCK v2.1."""

    #: Emitted after a background scan has been applied to the tree. Used by
    #: tests to await the asynchronous refresh.
    jobsRefreshed = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()

        uic.loadUi(_resource_path("job_manager.ui"), self)

        icon_path = _resource_path("icon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        self.setWindowTitle(f"Job Manager CK v{CURRENT_VERSION}")

        # Persistent user settings (print delay, material priority, etc.).
        # Loaded early so downstream helpers (preflight, label printer) can
        # read them. A bad settings file never crashes startup — load_settings
        # logs the error and returns defaults.
        self._settings: AppSettings = load_settings()

        # Data stores
        self._history = TransferHistory()
        self._status_service = NullJobStatusService()
        self._active_jobs: list[Job] = []
        self._printed_jobs: list[Job] = []
        self._dropped_jobs: dict[str, Job] = {}

        # Tree roots — populated in _populate_tree; kept on self so
        # _refresh_preserving_selection can walk children without rebuilding.
        self._active_root: Optional[QTreeWidgetItem] = None
        self._printed_root: Optional[QTreeWidgetItem] = None

        # Identifies what the tree currently displays. A refresh whose result
        # matches this skips the rebuild entirely, which is the common case —
        # job folders change rarely, but the refresh timer fires constantly.
        self._last_tree_signature: Optional[tuple] = None

        # In-flight background scan, if any.
        self._scan_thread: Optional[JobScanThread] = None

        # Printer status tracked via PrinterStatusWidget; assume offline
        # until the first poll reports otherwise. This is consulted by
        # _on_selection_changed when deciding whether to enable the
        # Print Labels button.
        self._zebra_online: bool = False
        self._printer_status: Optional[PrinterStatusWidget] = None

        # Paths
        self._source_folders = SOURCE_FOLDERS
        self._dest_path = DEST_PATH

        self._migration_warning: Optional[str] = None

        # Drop zone
        self._drop_zone = DropZone()
        self._drop_zone.fileDropped.connect(self._handle_dropped_folder)
        self.centralwidget.layout().insertWidget(2, self._drop_zone)

        self._setup_ui()

        # Starts a worker thread and returns immediately, so the window is
        # on screen and interactive while the S: drive is being walked.
        self.refresh_jobs()

        # The Archive -> Printed migration probes two paths on the S: share.
        # Deferred so those round-trips happen after the window is visible
        # rather than in front of it.
        QTimer.singleShot(0, self._run_migration)

        # Auto-refresh timer
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._auto_refresh)
        self._refresh_timer.start(AUTO_REFRESH_MS)

        QTimer.singleShot(2000, self._check_for_updates)

    def _run_migration(self) -> None:
        """Run the one-shot Archive -> Printed migration and report problems."""
        self._migration_warning = _migrate_archive_to_printed()
        if self._migration_warning:
            QMessageBox.warning(
                self, "Folder migration", self._migration_warning
            )

    def _setup_ui(self) -> None:
        """Connect buttons, menus, and selection signals."""
        # Swap the .ui placeholder QLabel for a live PrinterStatusWidget.
        # The placeholder is kept in the .ui file so the form compiles on
        # dev machines that haven't pulled this module yet — we replace it
        # programmatically here.
        self._install_printer_status_widget()

        self.refreshButton.clicked.connect(self.refresh_jobs)
        self.transferButton.clicked.connect(self._transfer_files)
        self.printButton.clicked.connect(self._print_labels)
        self.copyNCButton.clicked.connect(self._copy_nc_to_usb)
        self.completeButton.clicked.connect(self._move_to_printed)
        self.completeButton.setText("Move to Printed")
        self.restoreButton.clicked.connect(self._restore_to_active)
        self.restoreButton.setVisible(False)

        self.jobTreeWidget.itemSelectionChanged.connect(self._on_selection_changed)
        self.jobTreeWidget.itemDoubleClicked.connect(self._open_job_folder)

        # Disable action buttons until a job is selected
        self._set_action_buttons_enabled(False)

        # Settings menu — Print Settings dialog for material priority,
        # print behaviour toggles, and troubleshooting actions.
        settings_menu = self.menuBar().addMenu("&Settings")
        settings_action = settings_menu.addAction("Print Settings...")
        settings_action.triggered.connect(self._on_settings_triggered)

        # Help menu
        help_menu = self.menuBar().addMenu("Help")
        help_menu.addAction("Check for Updates", self._check_for_updates)
        help_menu.addAction("About", self._show_about)

        self.statusbar.showMessage("Ready")

    # -- Printer status widget -------------------------------------------

    def _install_printer_status_widget(self) -> None:
        """Replace the ``printerStatusLabel`` placeholder with a live widget.

        The widget polls printer availability on the interval specified by
        :class:`AppSettings` and emits ``statusChanged`` on transitions so
        :meth:`_on_printer_status_changed` can toggle the Print Labels
        button's enabled state.
        """
        placeholder = getattr(self, "printerStatusLabel", None)
        parent_layout = None
        insert_index: Optional[int] = None
        if placeholder is not None:
            parent_widget = placeholder.parentWidget()
            parent_layout = parent_widget.layout() if parent_widget else None
            if parent_layout is not None:
                insert_index = parent_layout.indexOf(placeholder)
                parent_layout.removeWidget(placeholder)
            placeholder.hide()
            placeholder.deleteLater()

        widget = PrinterStatusWidget(
            poll_interval_ms=self._settings.status_poll_interval_ms,
            printer_name=self._settings.zebra_printer_name,
            parent=self,
        )
        widget.statusChanged.connect(self._on_printer_status_changed)

        if parent_layout is not None and insert_index is not None and insert_index >= 0:
            parent_layout.insertWidget(insert_index, widget)
        else:
            # Fall back to appending to the central widget layout so the
            # widget is always visible even if the .ui file changes.
            central_layout = self.centralwidget.layout()
            if central_layout is not None:
                central_layout.insertWidget(0, widget)

        self._printer_status = widget
        widget.start()

    def _on_printer_status_changed(self, available: bool) -> None:
        """React to a Zebra connect/disconnect transition.

        Stores the new state and re-evaluates the Print Labels button so
        the user can see an offline printer come back online mid-session
        without having to re-select the job.
        """
        self._zebra_online = available
        if available:
            self.statusbar.showMessage("Zebra printer connected")
        else:
            self.statusbar.showMessage(
                "Zebra printer disconnected — Print Labels disabled"
            )
        self._on_selection_changed()

    def _set_action_buttons_enabled(self, enabled: bool) -> None:
        for btn in (self.transferButton, self.printButton, self.copyNCButton, self.completeButton):
            btn.setEnabled(enabled)

    def _set_ui_busy(self, busy: bool) -> None:
        """Disable or re-enable the full UI during long operations.

        Also parks the background polls. While a transfer or print is
        running they compete with the worker for the same S: share and print
        spooler, and their results cannot be acted on anyway because the
        controls they feed are disabled.
        """
        enabled = not busy
        self.refreshButton.setEnabled(enabled)
        self.jobTreeWidget.setEnabled(enabled)
        self._set_action_buttons_enabled(enabled)
        self.restoreButton.setEnabled(enabled)
        self._set_polling_active(enabled)

    def _set_polling_active(self, active: bool) -> None:
        """Start or stop the job-refresh and printer-status polls."""
        timer = getattr(self, "_refresh_timer", None)
        if timer is not None:
            if active and not timer.isActive():
                timer.start(AUTO_REFRESH_MS)
            elif not active and timer.isActive():
                timer.stop()

        # getattr: Qt can deliver events during uic.loadUi, before __init__
        # has finished assigning our own attributes.
        status_widget = getattr(self, "_printer_status", None)
        if status_widget is not None:
            try:
                if active:
                    status_widget.start()
                else:
                    status_widget.stop()
            except Exception:  # noqa: BLE001 - polling is never load-bearing
                logger.exception("Failed to toggle printer status polling")

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Pause polling while the window is minimised.

        Minimised means nothing the polls produce can be seen, but the scans
        would still hammer the S: share and the print spooler all shift.
        """
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange:
            self._set_polling_active(not self.isMinimized())

    # -- Auto-refresh --

    def _auto_refresh(self) -> None:
        """Silent refresh that preserves the current selection."""
        self._refresh_preserving_selection()

    def _refresh_preserving_selection(self) -> None:
        """Kick off a refresh; the tree keeps its selection across the rebuild.

        Selection, expansion state and scroll position are preserved by
        :meth:`_populate_tree` itself, so this is now simply a refresh. The
        method is kept as the intention-revealing name used by call sites
        that specifically care about not losing the user's place.
        """
        self.refresh_jobs()

    # -- Job list --

    def refresh_jobs(self) -> None:
        """Start a background scan of the source folders.

        The scan walks every job folder on the ``S:`` share, so it runs on a
        worker thread — the GUI thread stays responsive and the auto-refresh
        timer can never stall the window. Results are applied in
        :meth:`_on_scan_finished`.

        If a scan is already in flight this is a no-op: the running scan will
        deliver fresh results momentarily, and queueing another would only
        add load to the share.
        """
        if self._scan_thread is not None and self._scan_thread.isRunning():
            return

        # Only announce scanning when there is nothing on screen yet. On the
        # periodic refresh the status bar may be showing transfer or print
        # progress, and overwriting that every few seconds is noise.
        if not self._active_jobs and not self._printed_jobs:
            self.statusbar.showMessage("Scanning jobs...")

        thread = JobScanThread(
            scan_active=scan_jobs, scan_printed=scan_printed_jobs, parent=self
        )
        thread.scanned.connect(self._on_scan_finished)
        thread.failed.connect(self._on_scan_failed)
        # Retire the thread once it ends. It is parented to the window, so
        # without this a new QThread child would accumulate on every tick of
        # the refresh timer for the lifetime of the process.
        thread.finished.connect(lambda t=thread: self._retire_scan_thread(t))
        self._scan_thread = thread
        thread.start()

    def _retire_scan_thread(self, thread: JobScanThread) -> None:
        """Drop and delete a finished scan thread."""
        if self._scan_thread is thread:
            self._scan_thread = None
        thread.deleteLater()

    def _on_scan_failed(self, _message: str) -> None:
        """Report a failed scan without discarding the jobs already listed.

        A transient S: drive blip should not blank a populated tree — the
        next refresh a few seconds later will recover.
        """
        self.statusbar.showMessage("Error: could not read S drive")
        self.jobsRefreshed.emit()

    def _on_scan_finished(self, active: list, printed: list) -> None:
        """Apply scan results from the worker thread to the tree."""
        self._active_jobs = list(active)
        # Re-add dropped jobs (treat as active).
        self._active_jobs.extend(self._dropped_jobs.values())
        self._printed_jobs = list(printed)

        changed = self._populate_tree()

        # Only refresh the counts when something actually changed, so a
        # running transfer or print keeps its progress message visible.
        if changed:
            co_count = sum(
                1 for j in self._active_jobs
                if j.job_type == JobType.CABINETRY_ONLINE
            )
            cd_count = len(self._active_jobs) - co_count
            self.statusbar.showMessage(
                f"Found {len(self._active_jobs)} active "
                f"({co_count} CO, {cd_count} CD), "
                f"{len(self._printed_jobs)} printed"
            )

        self.jobsRefreshed.emit()

    def _tree_signature(self, statuses: dict[str, str]) -> tuple:
        """Return a value identifying exactly what the tree should display.

        ``Job`` and ``JobFiles`` are frozen dataclasses, so comparing them
        compares every rendered field. Statuses are folded in because they
        drive the row colour. If this is unchanged since the last rebuild,
        the tree is already correct and rebuilding it would only cost the
        user their scroll position and expansion state.
        """
        return (
            tuple(self._active_jobs),
            tuple(self._printed_jobs),
            tuple(statuses.get(j.name, "Ready") for j in self._active_jobs),
        )

    def _populate_tree(self) -> bool:
        """Fill the QTreeWidget with two roots: Active Jobs + Printed Jobs.

        Returns True if the tree was rebuilt, False if it was already showing
        exactly this content and the rebuild was skipped.
        """
        # One read of the history file for the whole tree, rather than one
        # read per job.
        statuses = self._history.get_all_statuses()

        signature = self._tree_signature(statuses)
        if signature == self._last_tree_signature:
            return False

        tree = self.jobTreeWidget

        # Remember the user's place so a background refresh doesn't move it.
        selected = self._selected_job()
        selection_key: Optional[tuple[str, bool]] = (
            (selected.name, selected.is_printed) if selected is not None else None
        )
        first_build = self._last_tree_signature is None
        if first_build:
            active_expanded, printed_expanded = True, False
        else:
            active_expanded = (
                self._active_root.isExpanded()
                if self._active_root is not None else True
            )
            printed_expanded = (
                self._printed_root.isExpanded()
                if self._printed_root is not None else False
            )
        scroll_value = tree.verticalScrollBar().value()

        # Suppress painting and selection signals for the whole rebuild:
        # clear() and each addChild() would otherwise trigger layout work and
        # re-entrant selection handling per item.
        tree.setUpdatesEnabled(False)
        blocked = tree.blockSignals(True)
        try:
            tree.clear()

            active_root = QTreeWidgetItem(["Active Jobs"])
            printed_root = QTreeWidgetItem(
                [f"Printed Jobs ({len(self._printed_jobs)})"]
            )

            tree.addTopLevelItem(active_root)
            tree.addTopLevelItem(printed_root)

            for job in self._active_jobs:
                active_root.addChild(self._build_job_item(job, statuses))

            for job in self._printed_jobs:
                item = self._build_job_item(job, statuses)
                # Printed jobs always wear the grey colour, regardless of what
                # the history file says — they were explicitly moved out of
                # Active.
                item.setForeground(0, COLOR_PRINTED_FINAL)
                printed_root.addChild(item)

            active_root.setExpanded(active_expanded)
            printed_root.setExpanded(printed_expanded)

            self._active_root = active_root
            self._printed_root = printed_root

            self._restore_selection(selection_key)
            tree.verticalScrollBar().setValue(scroll_value)
        finally:
            tree.blockSignals(blocked)
            tree.setUpdatesEnabled(True)

        self._last_tree_signature = signature
        # Signals were blocked during the rebuild, so drive the selection
        # handler once by hand to bring the buttons back in sync.
        self._on_selection_changed()
        return True

    def _restore_selection(self, key: Optional[tuple[str, bool]]) -> None:
        """Re-select the job identified by *key* after a rebuild.

        The key is ``(job.name, is_printed)`` so a job present in both the
        Active and Printed trees (shouldn't happen in practice, but safe) is
        re-selected in the same tree it was chosen from. A job that no longer
        exists clears the selection cleanly.
        """
        if key is None:
            return

        target_name, target_is_printed = key
        root = self._printed_root if target_is_printed else self._active_root
        if root is not None:
            for i in range(root.childCount()):
                child = root.child(i)
                job = child.data(0, Qt.UserRole)
                if isinstance(job, Job) and job.name == target_name:
                    # A restored selection inside a collapsed root would be
                    # invisible — make sure the user can see it.
                    root.setExpanded(True)
                    self.jobTreeWidget.setCurrentItem(child)
                    return

        self.jobTreeWidget.setCurrentItem(None)

    def _build_job_item(
        self, job: Job, statuses: dict[str, str]
    ) -> QTreeWidgetItem:
        """Create a QTreeWidgetItem for a Job with label, tooltip, and colour.

        *statuses* is the pre-read ``{job_name: status}`` map from
        :meth:`TransferHistory.get_all_statuses`, so building a row costs no
        file I/O.
        """
        tag = "CO" if job.job_type == JobType.CABINETRY_ONLINE else "CD"
        label = f"[{tag}] {job.display_name or job.name}"
        item = QTreeWidgetItem([label])
        item.setData(0, Qt.UserRole, job)
        item.setToolTip(0, _build_tooltip(job.files))

        status = statuses.get(job.name, "Ready")
        if status == "Ready":
            item.setForeground(0, COLOR_READY)
        elif status == "In Progress":
            item.setForeground(0, COLOR_IN_PROGRESS)
        elif status == "Printed":
            item.setForeground(0, COLOR_PRINTED_FINAL)
        else:
            item.setForeground(0, COLOR_DEFAULT)
        return item

    # -- Selection --

    def _selected_job(self) -> Optional[Job]:
        """Return the currently selected Job, or None.

        Returns None if no item is selected or if a root header is selected.
        """
        item = self.jobTreeWidget.currentItem()
        if item is None:
            return None
        # Root items (Active Jobs / Printed Jobs) have no parent.
        if item.parent() is None:
            return None
        data = item.data(0, Qt.UserRole)
        if isinstance(data, Job):
            return data
        return None

    def _on_selection_changed(self) -> None:
        job = self._selected_job()
        if job is None:
            self._set_action_buttons_enabled(False)
            self.restoreButton.setVisible(False)
            self.printButton.setToolTip("")
            return

        if job.is_printed:
            # Printed jobs are read-only — only Restore is available.
            self._set_action_buttons_enabled(False)
            self.restoreButton.setVisible(True)
            self.printButton.setToolTip("")
            return

        # Active job — enable actions based on file presence.
        self.restoreButton.setVisible(False)
        files = job.files
        self.transferButton.setEnabled(bool(files.mdb_files or files.wmf_files))
        self.copyNCButton.setEnabled(bool(files.nc_files))
        self.completeButton.setEnabled(True)

        # Soft-block Print Labels when the Zebra is offline: it still
        # takes file presence into account, but overlays an "offline"
        # tooltip so Marinko understands *why* the button is disabled.
        has_labels = bool(files.ljd_files)
        if not has_labels:
            self.printButton.setEnabled(False)
            self.printButton.setToolTip("")
        elif not self._zebra_online:
            self.printButton.setEnabled(False)
            self.printButton.setToolTip(
                "Zebra printer disconnected — check USB cable"
            )
        else:
            self.printButton.setEnabled(True)
            self.printButton.setToolTip("")

    # -- Double-click to open folder --

    def _open_job_folder(self) -> None:
        """Open the selected job's folder in Windows Explorer."""
        job = self._selected_job()
        if job is None:
            return
        if os.path.isdir(job.path):
            os.startfile(job.path)
        else:
            self.statusbar.showMessage(f"Folder not found: {job.path}")

    # -- NestLabel integration (placeholder) --

    # def _open_in_nestlabel(self) -> None:
    #     """Launch NestLabel with the selected CO job's .mdb file."""
    #     job = self._selected_job()
    #     if job is None or not job.files.mdb_files:
    #         return
    #     mdb_path = job.files.mdb_files[0]
    #     nestlabel_exe = NESTLABEL_EXE
    #     if not os.path.exists(nestlabel_exe):
    #         QMessageBox.warning(self, "NestLabel Not Found",
    #                             f"NestLabel not found at:\n{nestlabel_exe}")
    #         return
    #     import subprocess
    #     subprocess.Popen([nestlabel_exe, mdb_path])
    #     self.statusbar.showMessage(f"Opened NestLabel: {os.path.basename(mdb_path)}")

    # -- Preflight helpers --

    def _show_preflight_failure(
        self, result: preflight.PreflightResult
    ) -> None:
        """Render a failing :class:`PreflightResult` as a modal warning."""
        QMessageBox.warning(self, result.title, result.message)

    # -- File transfer (CO jobs: .mdb / .wmf) --

    def _transfer_files(self) -> None:
        job = self._selected_job()
        if job is None:
            return

        # Silent preflight: no dialog on pass, warning + abort on fail.
        s_result = check_s_drive_reachable(r"S:\Jobs")
        if not s_result.ok:
            self._show_preflight_failure(s_result)
            return
        cad_result = check_cadcode_free_space(self._dest_path, min_mb=500)
        if not cad_result.ok:
            self._show_preflight_failure(cad_result)
            return

        self._set_ui_busy(True)
        self._active_thread = FileTransferThread(
            mdb_files=job.files.mdb_files,
            wmf_files=job.files.wmf_files,
            dest_base=self._dest_path,
        )
        self._active_thread.progress.connect(self._update_status)
        self._active_thread.finished.connect(
            lambda ok, msg: self._on_operation_finished(ok, msg, "transferred", job.name, job.job_type.name)
        )
        self._active_thread.start()

    # -- Label printing (CD jobs: .ljd) --

    def _print_labels(self) -> None:
        job = self._selected_job()
        if job is None:
            return

        if not job.files.ljd_files:
            QMessageBox.warning(
                self,
                "Nothing to Print",
                f"No .ljd label files were found for '{job.name}'.",
            )
            return

        # Preflight: S drive (ljd paths live there) + Zebra printer.
        s_result = check_s_drive_reachable(r"S:\Jobs")
        if not s_result.ok:
            self._show_preflight_failure(s_result)
            return
        printer_result = check_printer_available(self._settings.zebra_printer_name)
        if not printer_result.ok:
            self._show_preflight_failure(printer_result)
            return

        # Resolve the actual printer name: user override wins, then the name
        # the status widget already resolved on its last poll, and only as a
        # last resort another spooler enumeration.
        zebra = self._settings.zebra_printer_name
        if not zebra and self._printer_status is not None:
            zebra = self._printer_status.resolved_printer_name()
        if not zebra:
            zebra = printer_service.find_zebra_printer()
        if not zebra:
            QMessageBox.warning(
                self,
                "Printer Not Found",
                "No Zebra label printer could be detected.\n\n"
                "Check that the Zebra GC420D is powered on and connected via USB.",
            )
            return

        # Auto-detect materials for this job, seeded with the sticky default
        # priority from the last print run. Top of the returned list = peeled
        # first on the roll.
        materials = print_sequencer.detect_materials_in_job(
            list(job.files.ljd_files),
            self._settings.material_priority,
        )
        if not materials:
            QMessageBox.warning(
                self,
                "No Labels",
                "No valid .ljd files found in this job.",
            )
            return

        display_job = job.display_name or job.name

        # The reorder dialog IS the confirmation — the user sees the
        # detected materials in a draggable visual stack, adjusts if needed,
        # and clicks Print to commit.
        order_dialog = PrintOrderDialog(
            job_name=display_job,
            materials=materials,
            include_separators=self._settings.print_separators,
            parent=self,
        )
        if order_dialog.exec_() != QDialog.Accepted:
            return  # user cancelled

        ordered_priority = order_dialog.get_ordered_materials()

        # Persist the user's chosen order as the new sticky default so the
        # next job they print starts from their most recent preference.
        new_settings = update_settings(
            self._settings, material_priority=ordered_priority
        )
        try:
            save_settings(new_settings)
        except OSError as exc:
            logger.exception("Failed to persist material priority: %s", exc)
        self._settings = new_settings

        # Build the full sequence with the user's chosen order. This runs
        # AFTER the reorder dialog so the sequence reflects exactly what the
        # user saw in the dialog's preview.
        sequence = print_sequencer.build_print_sequence(
            display_job,
            list(job.files.ljd_files),
            material_priority=ordered_priority,
            reverse_within=self._settings.reverse_order,
            include_separators=self._settings.print_separators,
        )
        if not sequence:
            QMessageBox.warning(
                self,
                "Nothing to Print",
                "Could not build a valid print sequence for this job.",
            )
            return

        self._set_ui_busy(True)
        self._active_thread = LabelPrinterThread(
            sequence=sequence,
            settings=self._settings,
            zebra_printer=zebra,
        )
        self._active_thread.progress.connect(self._on_print_progress)
        self._active_thread.finished.connect(
            lambda ok, msg: self._on_operation_finished(ok, msg, "printed", job.name, job.job_type.name)
        )
        self._active_thread.start()

    def _on_print_progress(self, current: int, total: int, description: str) -> None:
        """Route the rich (current,total,description) print progress signal
        into the single-string status bar."""
        self.statusbar.showMessage(
            f"Printing {current}/{total}: {description}"
        )

    # -- NC copy to USB --

    def _copy_nc_to_usb(self) -> None:
        job = self._selected_job()
        if job is None:
            return

        drives = detect_usb_drives()
        if not drives:
            QMessageBox.warning(self, "No USB Drive", "Please insert a USB drive and try again.")
            return

        if len(drives) == 1:
            target_drive = drives[0]
        else:
            drive, ok = QInputDialog.getItem(
                self, "Select USB Drive", "Choose a drive:", drives, 0, False,
            )
            if not ok:
                return
            target_drive = drive

        # USB drive preflight: make sure the target has room for every NC
        # file we're about to copy, with a 1 MB safety buffer.
        usb_path = f"{target_drive}\\"
        required_bytes = estimate_nc_files_size(job.files.nc_files)
        required_mb = (required_bytes // (1024 * 1024)) + 1
        usb_result = check_usb_free_space(usb_path, required_mb=required_mb)
        if not usb_result.ok:
            self._show_preflight_failure(usb_result)
            return

        self._set_ui_busy(True)
        self._active_thread = USBTransferThread(
            nc_files=job.files.nc_files,
            target_drive=target_drive,
        )
        self._active_thread.progress.connect(self._update_status)
        self._active_thread.finished.connect(
            lambda ok, msg: self._on_operation_finished(ok, msg, "nc_copied", job.name, job.job_type.name)
        )
        self._active_thread.start()

    # -- Move to Printed --

    def _move_to_printed(self) -> None:
        job = self._selected_job()
        if job is None:
            return
        if job.is_printed:
            # Safety: can't re-move an already-printed job.
            return

        reply = QMessageBox.question(
            self,
            "Move to Printed",
            f"Move job '{job.name}' to the Printed folder and remove it from the list?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        try:
            os.makedirs(PRINTED_PATH, exist_ok=True)
            printed_dest = os.path.join(PRINTED_PATH, job.name)
            shutil.move(job.path, printed_dest)
            self._history.mark_moved_to_printed(job.name, job.job_type.name)
            logger.info("Moved job %s to %s", job.name, printed_dest)
        except Exception:
            logger.exception("Failed to move job %s to Printed", job.name)
            QMessageBox.critical(
                self, "Error", f"Could not move job to Printed: {job.name}"
            )
            return

        # Remove dropped reference if present
        self._dropped_jobs.pop(job.name, None)
        self._refresh_preserving_selection()

    # -- Restore from Printed --

    def _restore_to_active(self) -> None:
        """Move a printed job back to its original active source folder.

        Source detection order:
            1. ``.mdb`` files   -> Cabinetry Online
            2. ``.ljd`` files   -> Custom Design
            3. No recognised files -> ask the user
        """
        job = self._selected_job()
        if job is None or not job.is_printed:
            return

        source_type = self._detect_restore_target(job)
        if source_type is None:
            return  # User cancelled the picker.

        target_base = os.path.join(r"S:\Jobs", source_type)
        target_path = os.path.join(target_base, job.name)

        if os.path.exists(target_path):
            QMessageBox.critical(
                self,
                "Restore Failed",
                f"A job named '{job.name}' already exists at:\n{target_path}\n\n"
                "Rename or remove the existing folder before restoring.",
            )
            return

        try:
            os.makedirs(target_base, exist_ok=True)
            shutil.move(job.path, target_path)
            self._history.clear_moved_to_printed(job.name)
            logger.info("Restored %s from Printed to %s", job.name, target_path)
        except Exception:
            logger.exception("Failed to restore %s to %s", job.name, target_path)
            QMessageBox.critical(
                self,
                "Restore Failed",
                f"Could not restore '{job.name}' to {source_type}.",
            )
            return

        self.statusbar.showMessage(f"Restored {job.name} to {source_type}")
        self._refresh_preserving_selection()

    def _detect_restore_target(self, job: Job) -> Optional[str]:
        """Decide which source folder a printed job should be restored to.

        Returns "Cabinetry Online" / "Custom Design" based on file heuristics,
        or asks the user via a dialog if the folder has no recognised files.
        Returns ``None`` if the user cancels the dialog.
        """
        if job.files.mdb_files or job.files.wmf_files:
            return "Cabinetry Online"
        if job.files.ljd_files:
            return "Custom Design"

        reply = QMessageBox.question(
            self,
            "Restore Destination",
            f"Could not auto-detect the original source for '{job.name}'.\n\n"
            "Restore to Cabinetry Online?\n"
            "(Click No to restore to Custom Design, Cancel to abort.)",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
        )
        if reply == QMessageBox.Yes:
            return "Cabinetry Online"
        if reply == QMessageBox.No:
            return "Custom Design"
        return None

    # -- Operation callbacks --

    def _update_status(self, message: str) -> None:
        self.statusbar.showMessage(message)

    def _on_operation_finished(
        self, success: bool, message: str, history_action: str,
        job_name: str, job_type_name: str,
    ) -> None:
        self._set_ui_busy(False)

        if success:
            if history_action == "transferred":
                self._history.mark_transferred(job_name, job_type_name)
            elif history_action == "printed":
                self._history.mark_printed(job_name, job_type_name)
            elif history_action == "nc_copied":
                self._history.mark_nc_copied(job_name, job_type_name)
            _beep(True)
            QMessageBox.information(self, "Success", message)
            self._refresh_preserving_selection()
        else:
            _beep(False)
            QMessageBox.critical(self, "Error", message)

        self.statusbar.showMessage("Ready")

    # -- Drop zone --

    def _handle_dropped_folder(self, path: str) -> None:
        """Process a folder dropped onto the drop zone."""
        try:
            files = scan_folder_files(path)
            job_type = detect_job_type(files)
        except Exception:
            logger.exception("Failed to scan dropped folder %s", path)
            self.statusbar.showMessage(f"Error scanning folder: {os.path.basename(path)}")
            return

        name = os.path.basename(path)
        job = Job(
            name=name, path=path, job_type=job_type, files=files,
            source_folder="Dropped", display_name=build_display_name(name, files),
        )
        self._dropped_jobs[name] = job

        # The dropped job is fully scanned already — add it to the tree
        # directly rather than re-walking the whole S: share to rediscover
        # something we're holding in hand.
        if job not in self._active_jobs:
            self._active_jobs.append(job)
        self._populate_tree()

        # Select the dropped job under the Active root.
        if self._active_root is not None:
            for i in range(self._active_root.childCount()):
                child = self._active_root.child(i)
                child_job = child.data(0, Qt.UserRole)
                if isinstance(child_job, Job) and child_job.name == name:
                    self._active_root.setExpanded(True)
                    self.jobTreeWidget.setCurrentItem(child)
                    break

        self.statusbar.showMessage(f"Added dropped job: {name}")

    # -- Update system --

    def _check_for_updates(self) -> None:
        # Rebinding _update_checker while a check is in flight would drop the
        # only reference to a running QThread and PyQt would destroy it
        # mid-run. The Help menu item and the start-up check share this path.
        existing = getattr(self, "_update_checker", None)
        if existing is not None and existing.isRunning():
            return

        self.statusbar.showMessage("Checking for updates...")
        self._update_checker = UpdateChecker()
        self._update_checker.update_available.connect(self._handle_update_available)
        self._update_checker.error.connect(
            lambda msg: self.statusbar.showMessage("Could not check for updates"),
        )
        self._update_checker.start()

    def _handle_update_available(self, info: dict) -> None:
        self.statusbar.showMessage("Update available!")
        self._pending_update_info = info
        notes = info.get("release_notes") or "No release notes available."
        reply = QMessageBox.question(
            self,
            "Update Available",
            f"Version {info['version']} is available!\n\n"
            f"Release notes:\n{notes}\n\n"
            "Would you like to download and install it?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self._download_update(info)

    def _download_update(self, info: dict) -> None:
        progress = QProgressDialog("Downloading update...", "Cancel", 0, 100, self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setAutoClose(False)

        self._downloader = UpdateDownloader(
            download_url=info.get("download_url", ""),
        )
        self._downloader.progress.connect(progress.setValue)
        self._downloader.finished.connect(
            lambda ok, result: self._handle_download_finished(ok, result, progress),
        )
        self._downloader.start()

    def _handle_download_finished(
        self, success: bool, result: str, progress_dialog: QProgressDialog,
    ) -> None:
        progress_dialog.close()
        if success:
            reply = QMessageBox.question(
                self,
                "Update Downloaded",
                "Update downloaded successfully!\n\n"
                "The application will now restart to apply the update.",
                QMessageBox.Ok | QMessageBox.Cancel,
            )
            if reply == QMessageBox.Ok:
                apply_update(result)
        else:
            QMessageBox.critical(self, "Download Failed", f"Failed to download update:\n{result}")

    # -- Settings dialog --

    def _on_settings_triggered(self) -> None:
        """Open the Print Settings dialog and wire its Apply signal."""
        dialog = SettingsDialog(self._settings, parent=self)
        dialog.settingsApplied.connect(self._on_settings_applied)
        dialog.exec_()

    def _on_settings_applied(self, new_settings: AppSettings) -> None:
        """Propagate a newly-saved :class:`AppSettings` to live components.

        Keeps the assignment immutable — ``self._settings`` is rebound to
        the new frozen dataclass rather than mutated in-place.
        """
        self._settings = new_settings

        # The parallel Phase 5 agent owns the PrinterStatusWidget. Check
        # for its presence so this method is safe to run before that
        # widget is wired up.
        if hasattr(self, "_printer_status") and self._printer_status is not None:
            try:
                self._printer_status.set_poll_interval(
                    new_settings.status_poll_interval_ms
                )
                self._printer_status.set_printer_name(
                    new_settings.zebra_printer_name
                )
            except AttributeError:
                logger.debug(
                    "Printer status widget missing expected setters; "
                    "ignoring settings propagation"
                )

        self.statusbar.showMessage("Print settings updated")

    # -- About --

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "About Job Manager CK",
            f"Job Manager CK v{CURRENT_VERSION}\n\n"
            "Manages job files from S drive for Continental Kitchens.",
        )

    # -- Window lifecycle ------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Stop all polling and tear background work down cleanly."""
        timer = getattr(self, "_refresh_timer", None)
        if timer is not None:
            timer.stop()

        if self._printer_status is not None:
            try:
                self._printer_status.stop()
                self._printer_status.deleteLater()
            except Exception:  # noqa: BLE001
                logger.exception("Failed to stop printer status widget")
            self._printer_status = None

        # Let an in-flight scan finish before the window goes away, so its
        # completion slot never runs against half-destroyed widgets.
        if self._scan_thread is not None:
            try:
                self._scan_thread.scanned.disconnect(self._on_scan_finished)
                self._scan_thread.failed.disconnect(self._on_scan_failed)
            except TypeError:
                pass  # already disconnected
            if self._scan_thread.isRunning():
                self._scan_thread.wait(5000)
            self._scan_thread = None

        super().closeEvent(event)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    app = QApplication(sys.argv)
    window = JobManager()
    window.show()
    sys.exit(app.exec_())
