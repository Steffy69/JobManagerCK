"""Programmatic Settings dialog for JobManagerCK v2.1.

Built entirely in code (no .ui file) so layout, signals, and validation
stay colocated in a single small module. Exposes a single public class,
:class:`SettingsDialog`, which edits a copy of :class:`AppSettings` and
emits ``settingsApplied`` when the user commits changes via OK / Apply.

The dialog never mutates the passed-in ``AppSettings`` instance — it
builds a fresh one via :func:`update_settings` so callers can trust the
immutability contract of the dataclass.
"""

from __future__ import annotations

import logging
from typing import Optional

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import printer_service
import zpl_templates
from settings import AppSettings, save_settings, update_settings

logger = logging.getLogger(__name__)


class SettingsDialog(QDialog):
    """Edit :class:`AppSettings` with a programmatic Qt layout.

    The dialog keeps the supplied settings immutable — every Apply / OK
    builds a new ``AppSettings`` via ``update_settings`` and emits it on
    ``settingsApplied`` so the main window can propagate changes without
    reaching back into the dialog's widgets.
    """

    settingsApplied = pyqtSignal(object)

    def __init__(
        self, settings: AppSettings, parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)
        self._initial_settings: AppSettings = settings

        self.setWindowTitle("Print Settings")
        self.setMinimumWidth(460)

        root_layout = QVBoxLayout(self)

        root_layout.addWidget(self._build_behavior_group())
        root_layout.addWidget(self._build_workflow_group())
        root_layout.addWidget(self._build_troubleshooting_group())

        self._button_box = QDialogButtonBox(
            QDialogButtonBox.Ok
            | QDialogButtonBox.Cancel
            | QDialogButtonBox.Apply
        )
        self._button_box.accepted.connect(self._on_accept)
        self._button_box.rejected.connect(self.reject)
        apply_button = self._button_box.button(QDialogButtonBox.Apply)
        if apply_button is not None:
            apply_button.clicked.connect(self._on_apply)
        root_layout.addWidget(self._button_box)

    # -- group builders --

    def _build_behavior_group(self) -> QGroupBox:
        group = QGroupBox("Printing Behavior")
        form = QFormLayout(group)

        self.reverse_order_checkbox = QCheckBox(
            "Reverse board order within material"
        )
        self.reverse_order_checkbox.setChecked(
            self._initial_settings.reverse_order
        )
        form.addRow(self.reverse_order_checkbox)

        self.print_delay_spinbox = QDoubleSpinBox()
        self.print_delay_spinbox.setRange(0.5, 30.0)
        self.print_delay_spinbox.setSingleStep(0.5)
        self.print_delay_spinbox.setDecimals(1)
        self.print_delay_spinbox.setSuffix(" s")
        self.print_delay_spinbox.setValue(
            self._initial_settings.print_delay_seconds
        )
        form.addRow("Delay between prints:", self.print_delay_spinbox)

        self.print_separators_checkbox = QCheckBox(
            "Print separator labels between materials"
        )
        self.print_separators_checkbox.setChecked(
            self._initial_settings.print_separators
        )
        form.addRow(self.print_separators_checkbox)

        return group

    def _build_workflow_group(self) -> QGroupBox:
        group = QGroupBox("Workflow")
        layout = QVBoxLayout(group)

        self.auto_mark_printed_checkbox = QCheckBox(
            "Auto-move to Printed on successful print"
        )
        self.auto_mark_printed_checkbox.setChecked(
            self._initial_settings.auto_mark_printed
        )
        layout.addWidget(self.auto_mark_printed_checkbox)

        return group

    def _build_troubleshooting_group(self) -> QGroupBox:
        group = QGroupBox("Troubleshooting")
        layout = QHBoxLayout(group)

        self.test_print_button = QPushButton("Test Print Separator")
        self.test_print_button.clicked.connect(self._on_test_print)
        layout.addWidget(self.test_print_button)

        self.clear_queue_button = QPushButton("Clear Print Queue")
        self.clear_queue_button.clicked.connect(self._on_clear_queue)
        layout.addWidget(self.clear_queue_button)

        return group

    # -- settings collection & commit --

    def _collect_settings(self) -> AppSettings:
        # Note: ``material_priority`` is NOT touched by this dialog — it is
        # owned by :class:`print_order_dialog.PrintOrderDialog` which updates
        # it per-job from the user's drag-reorder. Leaving it out of the
        # update_settings call preserves whatever the last job chose.
        return update_settings(
            self._initial_settings,
            reverse_order=self.reverse_order_checkbox.isChecked(),
            print_delay_seconds=float(self.print_delay_spinbox.value()),
            print_separators=self.print_separators_checkbox.isChecked(),
            auto_mark_printed=self.auto_mark_printed_checkbox.isChecked(),
        )

    def _commit(self) -> Optional[AppSettings]:
        new_settings = self._collect_settings()
        try:
            save_settings(new_settings)
        except OSError as exc:
            logger.exception("Failed to save settings: %s", exc)
            QMessageBox.critical(
                self,
                "Save Failed",
                f"Could not save settings:\n{exc}",
            )
            return None
        self.settingsApplied.emit(new_settings)
        self._initial_settings = new_settings
        return new_settings

    def _on_accept(self) -> None:
        if self._commit() is not None:
            self.accept()

    def _on_apply(self) -> None:
        self._commit()

    # -- troubleshooting handlers --

    def _resolve_zebra_printer(self) -> Optional[str]:
        configured = self._initial_settings.zebra_printer_name
        if configured:
            return configured
        return printer_service.find_zebra_printer()

    def _on_test_print(self) -> None:
        zebra = self._resolve_zebra_printer()
        if not zebra:
            QMessageBox.warning(
                self,
                "No Zebra Printer",
                "No Zebra printer detected.\n\n"
                "Check that the Zebra GC420D is powered on and connected "
                "via USB.",
            )
            return

        try:
            zpl = zpl_templates.build_test_separator()
            printer_service.send_raw_zpl(zebra, zpl)
        except Exception as exc:  # noqa: BLE001 — surface any error to user
            logger.exception("Test print failed")
            QMessageBox.critical(
                self,
                "Test Print Failed",
                f"Could not send test label to {zebra!r}:\n{exc}",
            )
            return

        QMessageBox.information(
            self,
            "Test Print Sent",
            f"Test label sent to {zebra!r}.",
        )

    def _on_clear_queue(self) -> None:
        reply = QMessageBox.question(
            self,
            "Clear Print Queue",
            "Delete all pending print jobs for the Zebra printer?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        zebra = self._resolve_zebra_printer()
        if not zebra:
            QMessageBox.warning(
                self,
                "No Zebra Printer",
                "No Zebra printer detected — nothing to clear.",
            )
            return

        try:
            deleted = printer_service.clear_print_queue(zebra)
        except PermissionError as exc:
            logger.warning("Clear print queue permission denied: %s", exc)
            QMessageBox.warning(
                self,
                "Admin Privileges Required",
                "Clearing the print queue needs administrator privileges.\n"
                "Try running JobManager as administrator and retry.",
            )
            return
        except Exception as exc:  # noqa: BLE001 — surface any error
            logger.exception("Clear print queue failed")
            QMessageBox.critical(
                self,
                "Clear Queue Failed",
                f"Could not clear the print queue:\n{exc}",
            )
            return

        QMessageBox.information(
            self,
            "Queue Cleared",
            f"Cleared {deleted} job{'s' if deleted != 1 else ''} "
            f"from {zebra!r}.",
        )
