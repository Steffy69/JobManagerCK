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

Between every print we sleep ``settings.print_delay_seconds`` to give the
Zebra's little internal buffer a chance to drain. The sleep runs on the
worker thread (a real :class:`QThread`) so the GUI never blocks.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from pathlib import Path

from PyQt5.QtCore import QThread, pyqtSignal

import printer_service
import zpl_templates
from print_sequencer import LABEL_KIND, SEPARATOR_LABEL_KIND, PrintItem
from settings import AppSettings

logger = logging.getLogger(__name__)


def build_sequence_preview(sequence: list[PrintItem]) -> str:
    """Return a human-readable summary of a print sequence.

    Groups labels by material in **peel order** (top of list = peeled first)
    and summarises separators as a total count. Used by the confirmation
    dialog before printing starts so Marinko can eyeball the job.

    Example output::

        BlackHMR: 3 labels
        WALNUT: 16 labels
        WHMR: 34 labels
        3 separators

    An empty sequence returns the string ``"(empty sequence)"``.
    """
    if not sequence:
        return "(empty sequence)"

    label_counts: Counter[str] = Counter()
    separator_count = 0
    # Track first-seen order of materials in peel order. The sequence arrives
    # in *print* order (reverse peel), so we reverse-walk and record first
    # encounters — the first label of a material in peel order is the last
    # instance we see when walking print order.
    peel_order_materials: list[str] = []
    seen: set[str] = set()
    for item in reversed(sequence):
        if item.kind == LABEL_KIND:
            label_counts[item.material] += 1
            if item.material not in seen:
                seen.add(item.material)
                peel_order_materials.append(item.material)
        elif item.kind == SEPARATOR_LABEL_KIND:
            separator_count += 1

    lines: list[str] = []
    for material in peel_order_materials:
        count = label_counts[material]
        label_word = "label" if count == 1 else "labels"
        lines.append(f"{material}: {count} {label_word}")

    if separator_count:
        sep_word = "separator" if separator_count == 1 else "separators"
        lines.append(f"{separator_count} {sep_word}")

    return "\n".join(lines)


def build_sequence_peel_preview(
    sequence: list[PrintItem], job_name: str
) -> str:
    """Return a rich, numbered peel-order preview of a print sequence.

    Unlike :func:`build_sequence_preview` — which gives a flat per-material
    count — this function walks the sequence in **peel order** (the reverse
    of the print order in which ``sequence`` arrives) and renders a numbered,
    multi-line summary showing what Marinko will physically see coming off the
    roll, top-down.

    Separators are shown as their own ``[SEP] ...`` lines. Consecutive labels
    of the same material are collapsed into a single ``MATERIAL labels (N)``
    line so long jobs stay readable. The final line is a total:

    * with separators::

        Total: 56 items (53 labels + 3 separators)

    * without::

        Total: 53 items (53 labels)

    Parameters
    ----------
    sequence:
        Print-order list of :class:`PrintItem` as produced by
        :func:`print_sequencer.build_print_sequence`. May be empty.
    job_name:
        Display name of the job. Currently only used for the caller's context
        — the ``job_name`` that appears in ``[SEP]`` lines comes from the
        separator ``PrintItem`` itself, so heterogeneous job names inside one
        sequence still render correctly. Kept in the signature because the
        caller (:meth:`JobManager._print_labels`) already has it handy and it
        future-proofs the API.

    Returns
    -------
    str
        A multi-line string suitable for display in a ``QMessageBox``. An
        empty sequence returns ``"(empty sequence)"``.
    """
    # job_name is intentionally unused directly — separator items carry the
    # job name they were built with. We keep the parameter so callers don't
    # have to re-derive it later and so the signature matches the brief.
    del job_name

    if not sequence:
        return "(empty sequence)"

    lines: list[str] = ["Peel order (top of roll -> bottom):"]

    # Walk in peel order = reverse of the print order we were given.
    peel_items = list(reversed(sequence))

    label_total = 0
    separator_total = 0
    line_index = 0

    # Grouping state: accumulate consecutive labels of the same material.
    group_material: str | None = None
    group_count = 0

    def flush_group() -> None:
        nonlocal group_material, group_count, line_index
        if group_material is None or group_count == 0:
            group_material = None
            group_count = 0
            return
        line_index += 1
        label_word = "label" if group_count == 1 else "labels"
        lines.append(
            f"  {line_index}. {group_material} {label_word} ({group_count})"
        )
        group_material = None
        group_count = 0

    for item in peel_items:
        if item.kind == SEPARATOR_LABEL_KIND:
            flush_group()
            separator_total += 1
            line_index += 1
            if item.is_job_separator and item.job_name:
                sep_text = f"{item.job_name} / {item.material}"
            else:
                sep_text = item.material
            lines.append(f"  {line_index}. [SEP] {sep_text}")
        elif item.kind == LABEL_KIND:
            label_total += 1
            if group_material == item.material:
                group_count += 1
            else:
                flush_group()
                group_material = item.material
                group_count = 1
        else:
            # Unknown kind — flush and render a placeholder so nothing is
            # silently dropped from the preview.
            flush_group()
            line_index += 1
            lines.append(f"  {line_index}. <unknown: {item.kind}>")

    flush_group()

    total_items = label_total + separator_total
    label_word = "label" if label_total == 1 else "labels"
    if separator_total:
        sep_word = "separator" if separator_total == 1 else "separators"
        totals_suffix = f"({label_total} {label_word} + {separator_total} {sep_word})"
    else:
        totals_suffix = f"({label_total} {label_word})"

    lines.append("")
    lines.append(f"Total: {total_items} items {totals_suffix}")
    return "\n".join(lines)


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
            if item.is_job_separator and item.job_name:
                return f"Separator: {item.job_name} / {item.material}"
            return f"Separator: {item.material}"
        return f"<unknown item kind: {item.kind}>"

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
        delay = max(0.0, float(self._settings.print_delay_seconds))

        logger.info(
            "Printing sequence of %d items to %r (delay=%.2fs)",
            total,
            self._zebra_printer,
            delay,
        )

        try:
            for index, item in enumerate(self._sequence, start=1):
                description = self._describe_item(item)
                self.progress.emit(index, total, description)

                if item.kind == LABEL_KIND:
                    printer_service.print_via_shellexecute(
                        self._zebra_printer, item.file_path
                    )
                    label_count += 1
                    logger.debug("Sent label %d/%d: %s", index, total, description)
                elif item.kind == SEPARATOR_LABEL_KIND:
                    if item.is_job_separator:
                        zpl = zpl_templates.build_job_separator(
                            item.job_name, item.material
                        )
                    else:
                        zpl = zpl_templates.build_material_separator(item.material)
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

                if index < total and delay > 0:
                    time.sleep(delay)

            summary = (
                f"Printed {total} items "
                f"({label_count} labels + {separator_count} separators)"
            )
            self.finished.emit(True, summary)

        except Exception as exc:  # noqa: BLE001 - surface any failure to UI
            logger.exception("Label printing failed")
            self.finished.emit(False, f"Printing failed: {exc}")
