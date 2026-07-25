"""Main window for JobManagerCK v2.1.

Manages job files from S drive for Continental Kitchens workshop.
Supports Cabinetry Online and Custom Design job workflows.
"""

import logging
import os
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
from PyQt5.QtCore import QEvent, QTimer, pyqtSignal
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QInputDialog,
    QMainWindow,
    QMessageBox,
    QPushButton,
)

import preflight
import print_sequencer
import printer_service
from drop_zone import DropZone
from file_transfer import FileTransferThread
from job_scan_worker import JobScanThread
from job_scanner import (
    PRINTED_DIR,
    Job,
    migrate_archive_to_printed,
    scan_jobs,
    scan_printed_jobs,
)
from job_tree import JobTreeController
from job_types import (
    JobType,
    build_display_name,
    detect_job_type,
    scan_folder_files,
)
from label_printer import LabelPrinterThread
from move_job import MoveJobThread
from preflight import check_cadcode_free_space
from print_order_dialog import PrintOrderDialog
from printer_status_widget import PrinterStatusWidget
from settings import AppSettings, load_settings, save_settings, update_settings
from settings_dialog import SettingsDialog
from transfer_history import TransferHistory
from update_flow import UpdateFlow
from updater import CURRENT_VERSION
from usb_transfer import USBTransferThread, detect_usb_drives

logger = logging.getLogger(__name__)

DEST_PATH = r"C:\CADCode"
PRINTED_PATH = PRINTED_DIR  # re-export for any external callers that import PRINTED_PATH
AUTO_REFRESH_MS = 5000  # Poll S drive every 5 seconds

# Module-level alias so tests can monkeypatch the migration seam without
# reaching into job_scanner.
_migrate_archive_to_printed = migrate_archive_to_printed


def _resource_path(filename: str) -> str:
    """Resolve a resource file path for both bundled and script modes."""
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, filename)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)


def _beep(success: bool) -> None:
    """Play the OK / error system sound, if the platform provides one."""
    if winsound is None:
        return
    winsound.MessageBeep(
        winsound.MB_OK if success else winsound.MB_ICONHAND
    )


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
        self._active_jobs: list[Job] = []
        self._printed_jobs: list[Job] = []
        self._dropped_jobs: dict[str, Job] = {}

        # Tree management (rows, colours, rebuild-skipping, selection
        # preservation) lives in its own controller.
        self._tree = JobTreeController(
            self.jobTreeWidget,
            self._history,
            on_rebuilt=self._on_selection_changed,
        )

        # Update check/download/apply flow.
        self._updates = UpdateFlow(self, self.statusbar)

        # In-flight background scan, if any.
        self._scan_thread: Optional[JobScanThread] = None

        # The one long operation allowed at a time (transfer / print / USB
        # copy / folder move). While it runs, self._busy is True and every
        # path that could start another operation — or re-enable the buttons
        # that start one — is gated on it. Rebinding _active_thread while a
        # thread is running would drop the only reference to a live QThread
        # and crash the app, so the gate is load-bearing, not cosmetic.
        self._busy = False
        self._active_thread = None

        # Printer status tracked via PrinterStatusWidget; assume offline
        # until the first poll reports otherwise. This is consulted by
        # _on_selection_changed when deciding whether to enable the
        # Print Labels button.
        self._zebra_online: bool = False
        self._printer_status: Optional[PrinterStatusWidget] = None

        # Paths
        self._dest_path = DEST_PATH

        self._migration_warning: Optional[str] = None

        # Drop zone
        self._drop_zone = DropZone()
        self._drop_zone.fileDropped.connect(self._handle_dropped_folder)
        self._drop_zone.dropRejected.connect(self.statusbar.showMessage)
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

        QTimer.singleShot(2000, lambda: self._updates.check(force=False))

    def _run_migration(self) -> None:
        """Deferred startup housekeeping (off the pre-show path).

        Restores the Windows default printer if a previous run died while it
        was swapped to the Zebra, then runs the one-shot Archive -> Printed
        migration.
        """
        try:
            restored = printer_service.restore_default_printer_if_marked()
        except Exception:  # noqa: BLE001 - never block startup on this
            logger.exception("Default-printer recovery failed")
            restored = None
        if restored:
            self.statusbar.showMessage(
                f"Restored the default printer to {restored} after an "
                "interrupted print run"
            )

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

        # Cancel button for long operations — lives in the status bar and
        # only appears while an operation is running. Without it, a 2-minute
        # print of the wrong job could only be stopped by killing the app.
        self._cancel_button = QPushButton("Cancel")
        self._cancel_button.setVisible(False)
        self._cancel_button.clicked.connect(self._cancel_active_operation)
        self.statusbar.addPermanentWidget(self._cancel_button)

        # Settings menu — Print Settings dialog for material priority,
        # print behaviour toggles, and troubleshooting actions.
        settings_menu = self.menuBar().addMenu("&Settings")
        settings_action = settings_menu.addAction("Print Settings...")
        settings_action.triggered.connect(self._on_settings_triggered)

        # Help menu. The lambda matters: QAction.triggered passes a checked
        # bool that would otherwise land in the force parameter.
        help_menu = self.menuBar().addMenu("Help")
        help_menu.addAction(
            "Check for Updates",
            lambda: self._updates.check(force=True),
        )
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

        EVERY control that can start (or feed into) another operation is
        gated: buttons, tree, drop zone and menu bar. Leaving any of them
        live lets a second operation rebind ``_active_thread`` and destroy
        the still-running first thread. The background polls are parked too
        — they compete with the worker for the same S: share and print
        spooler.
        """
        self._busy = busy
        enabled = not busy
        self.refreshButton.setEnabled(enabled)
        self.jobTreeWidget.setEnabled(enabled)
        self.restoreButton.setEnabled(enabled)
        self._drop_zone.setEnabled(enabled)
        self.menuBar().setEnabled(enabled)
        self._cancel_button.setVisible(busy)
        self._cancel_button.setEnabled(busy)

        if busy:
            self._set_action_buttons_enabled(False)
        else:
            self._active_thread = None
            # Re-validate rather than blanket-enable: a blanket enable would
            # e.g. light up Print Labels for a job with no label files.
            self._on_selection_changed()
            # Catch up on anything that changed on disk while we were busy —
            # scan results that arrived mid-operation were discarded.
            self.refresh_jobs()

        self._sync_polling()

    def _cancel_active_operation(self) -> None:
        """Ask the running worker to stop at its next checkpoint."""
        thread = self._active_thread
        if thread is not None and thread.isRunning():
            thread.requestInterruption()
            self._cancel_button.setEnabled(False)
            self.statusbar.showMessage("Cancelling...")

    def _sync_polling(self) -> None:
        """Start/stop the background polls from ONE combined predicate.

        Busy and minimised each previously toggled polling independently,
        last writer wins — so restoring the window mid-print restarted the
        polls, and finishing an operation while minimised restarted them
        into a hidden window. One predicate cannot fight itself.
        """
        active = not getattr(self, "_busy", False) and not self.isMinimized()

        # getattr: Qt can deliver events during uic.loadUi, before __init__
        # has finished assigning our own attributes.
        timer = getattr(self, "_refresh_timer", None)
        if timer is not None:
            if active and not timer.isActive():
                timer.start(AUTO_REFRESH_MS)
            elif not active and timer.isActive():
                timer.stop()

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
        """Re-evaluate polling when the window is minimised or restored.

        Minimised means nothing the polls produce can be seen, but the scans
        would still hammer the S: share and the print spooler all shift.
        """
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange:
            self._sync_polling()

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
        if self._busy:
            # The refresh timer is parked while busy, but a stray call must
            # not add S: load or rebuild the tree under a running operation.
            return
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
        if self._busy:
            # A scan that started before the operation must not rebuild the
            # tree now: _populate_tree ends by re-validating the action
            # buttons, which would re-enable them mid-print — the exact hole
            # the busy lockout exists to close. _set_ui_busy(False) refreshes
            # again, so nothing is lost.
            self.jobsRefreshed.emit()
            return

        # Prune dropped jobs whose folder has since been deleted — they can
        # never be acted on, and re-adding them each scan made them
        # immortal. Dropped jobs are rare, so these stats are cheap.
        for name in [
            n for n, j in self._dropped_jobs.items()
            if not os.path.isdir(j.path)
        ]:
            del self._dropped_jobs[name]

        self._active_jobs = list(active)
        # Re-add dropped jobs (treat as active), unless a job of the same
        # name was scanned from S: — a folder dropped from S:\Jobs itself
        # would otherwise appear twice, forever.
        scanned_names = {j.name for j in self._active_jobs}
        self._active_jobs.extend(
            j for n, j in self._dropped_jobs.items() if n not in scanned_names
        )
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

    def _populate_tree(self) -> bool:
        """Repaint the tree from the current job lists (via the controller).

        Returns True if the tree was rebuilt, False if the rebuild was
        skipped because nothing changed.
        """
        return self._tree.populate(self._active_jobs, self._printed_jobs)

    # -- Selection --

    def _selected_job(self) -> Optional[Job]:
        """Return the currently selected Job, or None."""
        return self._tree.selected_job()

    def _on_selection_changed(self) -> None:
        if self._busy:
            # Nothing may re-enable the action buttons while an operation
            # runs — starting a second one would rebind _active_thread and
            # destroy the live thread.
            return
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
        # No pre-stat: probing an S: path on the GUI thread hangs on a dead
        # share. startfile fails fast with the same information.
        try:
            os.startfile(job.path)
        except OSError:
            self.statusbar.showMessage(f"Folder not found: {job.path}")

    # -- Preflight helpers --

    def _show_preflight_failure(
        self, result: preflight.PreflightResult
    ) -> None:
        """Render a failing :class:`PreflightResult` as a modal warning."""
        QMessageBox.warning(self, result.title, result.message)

    # -- File transfer (CO jobs: .mdb / .wmf) --

    def _transfer_files(self) -> None:
        job = self._selected_job()
        if job is None or self._busy:
            return

        # CADCode free space is a local, bounded check — fine inline. The
        # S:-drive reachability probe moved into the worker: an SMB probe
        # has unbounded latency exactly when the share is sick, and here it
        # would freeze the GUI instead of showing the failure dialog.
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
            lambda ok, msg, j=job: self._on_operation_finished(
                ok, msg, "transferred", j
            )
        )
        self._active_thread.start()

    # -- Label printing (CD jobs: .ljd) --

    def _print_labels(self) -> None:
        job = self._selected_job()
        if job is None or self._busy:
            return

        if not job.files.ljd_files:
            QMessageBox.warning(
                self,
                "Nothing to Print",
                f"No .ljd label files were found for '{job.name}'.",
            )
            return

        # Printer availability comes from the status widget's background
        # poll (self._zebra_online gates the Print button already) — the old
        # inline EnumPrinters preflight re-enumerated the spooler on the GUI
        # thread on every click, freezing the window whenever the spooler
        # was slow. S:-drive reachability is the worker's problem: a label
        # that fails to submit surfaces a real error there.

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
            lambda ok, msg, j=job: self._on_operation_finished(
                ok, msg, "printed", j
            )
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
        if job is None or self._busy:
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

        # Size estimation and the free-space check happen inside the worker
        # now — sizing every NC file is one SMB stat per file, which used to
        # run on the GUI thread before the dialog could even appear.
        self._set_ui_busy(True)
        self._active_thread = USBTransferThread(
            nc_files=job.files.nc_files,
            target_drive=target_drive,
        )
        self._active_thread.progress.connect(self._update_status)
        self._active_thread.finished.connect(
            lambda ok, msg, j=job: self._on_operation_finished(
                ok, msg, "nc_copied", j
            )
        )
        self._active_thread.start()

    # -- Move to Printed --

    def _move_to_printed(self) -> None:
        job = self._selected_job()
        if job is None or self._busy:
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

        self._start_move_to_printed(job)

    def _start_move_to_printed(self, job: Job) -> None:
        """Move *job* into the Printed folder on a worker thread.

        A move on the same S: volume is a single rename, but a DROPPED job
        can live anywhere and ``shutil.move`` silently degrades to a full
        copy over the network — minutes of frozen GUI if run inline.
        """
        self._set_ui_busy(True)
        dest = os.path.join(PRINTED_PATH, job.name)
        self._active_thread = MoveJobThread(src=job.path, dest=dest)
        self._active_thread.finished.connect(
            lambda ok, msg, j=job: self._on_move_to_printed_finished(
                ok, msg, j
            )
        )
        self._active_thread.start()
        self.statusbar.showMessage(f"Moving {job.name} to Printed...")

    def _on_move_to_printed_finished(
        self, success: bool, message: str, job: Job
    ) -> None:
        if success:
            self._history.mark_moved_to_printed(job.name, job.job_type.name)
            self._dropped_jobs.pop(job.name, None)
            self._set_ui_busy(False)
            self.statusbar.showMessage(f"Moved {job.name} to Printed")
        else:
            self._set_ui_busy(False)
            QMessageBox.critical(self, "Move to Printed Failed", message)

    # -- Restore from Printed --

    def _restore_to_active(self) -> None:
        """Move a printed job back to its original active source folder.

        Source detection order:
            1. ``.mdb`` files   -> Cabinetry Online
            2. ``.ljd`` files   -> Custom Design
            3. No recognised files -> ask the user
        """
        job = self._selected_job()
        if job is None or not job.is_printed or self._busy:
            return

        source_type = self._detect_restore_target(job)
        if source_type is None:
            return  # User cancelled the picker.

        target_path = os.path.join(r"S:\Jobs", source_type, job.name)

        # The destination-exists check happens inside the worker — it is an
        # SMB round-trip, and MoveJobThread refuses to merge into an
        # existing folder anyway.
        self._set_ui_busy(True)
        self._active_thread = MoveJobThread(src=job.path, dest=target_path)
        self._active_thread.finished.connect(
            lambda ok, msg, j=job, st=source_type: (
                self._on_restore_finished(ok, msg, j, st)
            )
        )
        self._active_thread.start()
        self.statusbar.showMessage(f"Restoring {job.name}...")

    def _on_restore_finished(
        self, success: bool, message: str, job: Job, source_type: str
    ) -> None:
        if success:
            self._history.clear_moved_to_printed(job.name)
            self._set_ui_busy(False)
            self.statusbar.showMessage(
                f"Restored {job.name} to {source_type}"
            )
        else:
            self._set_ui_busy(False)
            QMessageBox.critical(self, "Restore Failed", message)

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
        self, success: bool, message: str, history_action: str, job: Job,
    ) -> None:
        if success:
            # Record history BEFORE _set_ui_busy(False): un-busying kicks a
            # refresh, and the tree should paint the new status first time.
            if history_action == "transferred":
                self._history.mark_transferred(job.name, job.job_type.name)
            elif history_action == "printed":
                self._history.mark_printed(job.name, job.job_type.name)
            elif history_action == "nc_copied":
                self._history.mark_nc_copied(job.name, job.job_type.name)

            self._set_ui_busy(False)
            self.statusbar.showMessage("Ready")
            _beep(True)
            QMessageBox.information(self, "Success", message)

            # Optional workflow shortcut: a job whose labels just printed
            # goes straight to the Printed folder without the confirm
            # dialog. (This setting existed in the UI for a while without
            # being wired to anything.)
            if (
                history_action == "printed"
                and self._settings.auto_mark_printed
                and not job.is_printed
            ):
                self._start_move_to_printed(job)
        else:
            self._set_ui_busy(False)
            self.statusbar.showMessage("Ready")
            _beep(False)
            QMessageBox.critical(self, "Error", message)

    # -- Drop zone --

    def _handle_dropped_folder(self, path: str) -> None:
        """Process a folder dropped onto the drop zone."""
        if self._busy:
            self.statusbar.showMessage(
                "Busy — drop the folder again after the current operation "
                "finishes"
            )
            return
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
        # something we're holding in hand. Dedupe by NAME: full dataclass
        # equality never matches a scanned copy of the same folder (its
        # source_folder differs), which used to double the row forever.
        self._active_jobs = [
            j for j in self._active_jobs if j.name != name
        ]
        self._active_jobs.append(job)
        self._populate_tree()
        self._tree.select_job_by_name(name)
        self.statusbar.showMessage(f"Added dropped job: {name}")

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
        """Stop all polling and tear background work down cleanly.

        The running-operation check comes FIRST: if the user keeps the app
        open, nothing must have been torn down yet. Closing with a live
        QThread aborts the process at interpreter teardown — and if the
        print thread dies inside its default-printer swap, the system
        default is left pointing at the Zebra.
        """
        active = self._active_thread
        if active is not None and active.isRunning():
            reply = QMessageBox.question(
                self,
                "Operation in Progress",
                "A transfer or print is still running.\n\n"
                "Cancel it and exit?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                event.ignore()
                return
            active.requestInterruption()
            if not active.wait(15000):
                logger.error("Active worker did not stop within 15s")

        self._updates.shutdown()

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
    from app_logging import setup_logging

    setup_logging()
    logger.info("Job Manager CK v%s starting", CURRENT_VERSION)
    app = QApplication(sys.argv)
    window = JobManager()
    window.show()
    sys.exit(app.exec_())
