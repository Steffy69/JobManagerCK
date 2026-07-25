"""Worker thread for batch printing CD labels in the computed peel order.

The Zebra GC420D is a roll-fed thermal printer: the first label printed ends
up at the bottom of the stack and is the last one peeled, so the print queue
has to be fed in reverse of the desired peel order. That ordering is computed
upstream by :mod:`print_sequencer` and arrives here as a ready-to-emit
``list[PrintItem]`` — this module's only job is to execute the sequence and
emit progress signals, so it stays dead simple and testable.

Two item kinds live in the sequence:

``kind == "label"``
    A real ``.ljd`` file on disk. Printed via
    :func:`printer_service.print_via_shellexecute` which uses the Windows
    ``printto`` verb — routes the file directly to the Zebra regardless of
    the system default printer.

``kind == "separator"``
    A raw ZPL label rendered by :mod:`zpl_templates`. Sent as bytes to the
    Zebra via :func:`printer_service.send_raw_zpl`.

After each LABEL we sleep ``settings.print_delay_seconds`` to pace
submissions into the handler; after each SEPARATOR we sleep
``settings.separator_delay_seconds`` to let the Zebra's small internal
buffer drain. Both sleeps run on the worker thread in interruptible slices,
so the GUI never blocks and Cancel takes effect within ~100 ms.

If ``printto`` fails (some handlers reject the verb, or mis-parse printer
names containing parentheses), the run falls back ONCE: it saves the user's
default printer, makes the Zebra the system default for the REST of the run,
prints via the plain ``print`` verb, and restores the saved default when the
run ends — success, failure, or cancel. A marker file lets the next launch
restore the default even if the process dies mid-run.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt5.QtCore import QThread, pyqtSignal

import printer_service
import zpl_templates
from print_sequencer import LABEL_KIND, SEPARATOR_LABEL_KIND, PrintItem
from settings import AppSettings

logger = logging.getLogger(__name__)


class LabelPrinterThread(QThread):
    """Background thread that executes a pre-computed label print sequence.

    Signals
    -------
    progress : (int current, int total, str description)
        Emitted once per item in the sequence just before it is sent to the
        printer. Current is 1-indexed; ``current == total`` on the final item.

    finished : (bool success, str message)
        Emitted exactly once when the sequence is exhausted or on error. On
        success, ``message`` reports total labels + separators printed.
    """

    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(bool, str)

    def __init__(
        self,
        sequence: list[PrintItem],
        settings: AppSettings,
        zebra_printer: str,
    ) -> None:
        super().__init__()
        self._sequence = list(sequence)
        self._settings = settings
        self._zebra_printer = zebra_printer
        # Run-level default-printer swap state (see module docstring).
        self._swapped = False
        self._original_default: str | None = None

    # ------------------------------------------------------------------
    # Description helper (kept separate so it's easy to test / tweak)
    # ------------------------------------------------------------------

    @staticmethod
    def _describe_item(item: PrintItem) -> str:
        """Build a short, human-readable label for a sequence item."""
        if item.kind == LABEL_KIND:
            base = Path(item.file_path).name
            if item.material and item.board_number is not None:
                return f"{item.material} #{item.board_number} ({base})"
            return base
        if item.kind == SEPARATOR_LABEL_KIND:
            if item.job_name:
                return f"Separator: {item.job_name} / {item.material}"
            return f"Separator: {item.material}"
        return f"<unknown item kind: {item.kind}>"

    # ------------------------------------------------------------------
    # Cancellation / pacing helpers
    # ------------------------------------------------------------------

    def _interruptible_sleep(self, seconds: float) -> bool:
        """Sleep in ~100 ms slices, returning False if cancel was requested.

        ``time.sleep`` is uninterruptible — a full-length sleep between every
        item is what previously made a 2-minute print run impossible to
        cancel. Slicing bounds cancel latency at ~100 ms.
        """
        remaining_ms = int(max(0.0, seconds) * 1000)
        while remaining_ms > 0:
            if self.isInterruptionRequested():
                return False
            slice_ms = min(100, remaining_ms)
            self.msleep(slice_ms)
            remaining_ms -= slice_ms
        return not self.isInterruptionRequested()

    def _print_label(self, item: PrintItem) -> None:
        """Print one label, falling back to the default-printer swap ONCE.

        The first ``printto`` failure flips the whole run into swap mode:
        the user's default printer is saved (plus a crash-recovery marker on
        disk), the Zebra becomes the system default, and this and every
        remaining label goes out via the plain ``print`` verb. One swap per
        run instead of one per label means a single restore point and no
        settle-time race with the handler.
        """
        if self._swapped:
            printer_service.print_via_print_verb(item.file_path)
            return

        try:
            printer_service.print_via_shellexecute(
                self._zebra_printer, item.file_path
            )
        except Exception as exc:  # noqa: BLE001 - any printto failure
            logger.warning(
                "printto failed for %s (%s); swapping default printer to %r "
                "for the rest of the run",
                Path(item.file_path).name,
                exc,
                self._zebra_printer,
            )
            self._original_default = printer_service.get_default_printer()
            printer_service.save_default_printer_marker(
                self._original_default or ""
            )
            printer_service.set_default_printer(self._zebra_printer)
            self._swapped = True
            printer_service.print_via_print_verb(item.file_path)

    def _restore_default_printer(self) -> str | None:
        """Undo the run-level swap. Returns a warning string on failure.

        Idempotent — safe to call from both the normal exit paths and the
        ``finally`` backstop.
        """
        if not self._swapped:
            return None
        self._swapped = False

        if not self._original_default:
            # There was no default to restore. Leave the Zebra in place but
            # tell the user rather than staying silent.
            printer_service.clear_default_printer_marker()
            return (
                "The Windows default printer was changed to the Zebra during "
                "this run and there was no previous default to restore."
            )

        try:
            printer_service.set_default_printer(self._original_default)
            printer_service.clear_default_printer_marker()
            logger.info(
                "Restored default printer to %r", self._original_default
            )
            return None
        except Exception:  # noqa: BLE001 - must not mask the run result
            logger.exception(
                "Failed to restore default printer to %r",
                self._original_default,
            )
            # Keep the marker: the next launch will retry the restore.
            return (
                f"Could not restore the Windows default printer to "
                f"'{self._original_default}'. It will be restored "
                "automatically the next time Job Manager starts."
            )

    @staticmethod
    def _with_warning(message: str, warning: str | None) -> str:
        return f"{message}\n\nWARNING: {warning}" if warning else message

    # ------------------------------------------------------------------
    # Thread entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        if not self._sequence:
            self.finished.emit(False, "No labels to print")
            return

        if not self._zebra_printer:
            self.finished.emit(False, "No Zebra printer configured")
            return

        total = len(self._sequence)
        label_count = 0
        separator_count = 0
        label_delay = max(0.0, float(self._settings.print_delay_seconds))
        separator_delay = max(
            0.0, float(self._settings.separator_delay_seconds)
        )

        logger.info(
            "Printing sequence of %d items to %r "
            "(label delay=%.2fs, separator delay=%.2fs)",
            total,
            self._zebra_printer,
            label_delay,
            separator_delay,
        )

        def cancel_message(sent: int) -> str:
            return (
                f"Cancelled — sent {sent} of {total} items "
                f"({label_count} labels + {separator_count} separators). "
                "Remove any unwanted labels from the printer."
            )

        try:
            for index, item in enumerate(self._sequence, start=1):
                if self.isInterruptionRequested():
                    self.finished.emit(
                        False,
                        self._with_warning(
                            cancel_message(index - 1),
                            self._restore_default_printer(),
                        ),
                    )
                    return

                description = self._describe_item(item)
                self.progress.emit(index, total, description)

                if item.kind == LABEL_KIND:
                    self._print_label(item)
                    label_count += 1
                    logger.debug(
                        "Sent label %d/%d: %s", index, total, description
                    )
                elif item.kind == SEPARATOR_LABEL_KIND:
                    zpl = zpl_templates.build_job_separator(
                        item.job_name, item.material
                    )
                    printer_service.send_raw_zpl(
                        self._zebra_printer,
                        zpl,
                        doc_name=f"JobManagerCK Separator {index}/{total}",
                    )
                    separator_count += 1
                    logger.debug(
                        "Sent separator %d/%d: %s", index, total, description
                    )
                else:
                    # Should never happen — PrintItem.kind is built by our
                    # own sequencer. Log loudly and keep going.
                    logger.warning(
                        "Skipping unknown print item kind at %d: %r",
                        index,
                        item.kind,
                    )

                if index < total:
                    # Separators guard batch boundaries and the printer's
                    # small buffer — they keep their own (conservative)
                    # delay independent of the label pacing.
                    delay = (
                        separator_delay
                        if item.kind == SEPARATOR_LABEL_KIND
                        else label_delay
                    )
                    if not self._interruptible_sleep(delay):
                        self.finished.emit(
                            False,
                            self._with_warning(
                                cancel_message(index),
                                self._restore_default_printer(),
                            ),
                        )
                        return

            restore_warning = self._restore_default_printer()
            summary = (
                f"Printed {total} items "
                f"({label_count} labels + {separator_count} separators)"
            )
            self.finished.emit(
                True, self._with_warning(summary, restore_warning)
            )

        except Exception as exc:  # noqa: BLE001 - surface any failure to UI
            logger.exception("Label printing failed")
            restore_warning = self._restore_default_printer()
            self.finished.emit(
                False,
                self._with_warning(f"Printing failed: {exc}", restore_warning),
            )
        finally:
            # Backstop for any path that slipped past the explicit restores
            # (idempotent — a no-op when already restored).
            self._restore_default_printer()
