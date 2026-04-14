"""Per-job material reorder dialog for JobManagerCK v2.1.

When the user clicks Print Labels the main window opens this dialog, which
shows the materials auto-detected from the job's ``.ljd`` files as a visual,
draggable stack. Top of list = peeled first = printed last on the roll.

Design notes
------------

The dialog intentionally replaces the old ``QMessageBox`` confirmation: it is
both the "preview" and the "are you sure?" step in a single screen. Rows are
painted with a fixed pastel palette cycled by index so the stack reads as an
intuitive graphic rather than a wall of text. A monospace preview label below
the list echoes the current order (matching the semantic of
``build_sequence_peel_preview``) and updates live as the user drags rows.

The dialog does not touch :mod:`settings` — callers are responsible for
persisting the user's chosen order back to ``AppSettings.material_priority``.
"""

from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QBrush, QColor, QFont
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


# Pastel palette cycled by row index. Picked to be distinct in hue while still
# low-saturation enough that the row text (default black) stays readable.
_PASTEL_PALETTE: tuple[QColor, ...] = (
    QColor(255, 249, 196),  # pastel yellow
    QColor(187, 222, 251),  # pastel blue
    QColor(200, 230, 201),  # pastel green
    QColor(255, 224, 178),  # pastel orange
    QColor(225, 190, 231),  # pastel purple
    QColor(255, 205, 210),  # pastel pink
)


class PrintOrderDialog(QDialog):
    """Confirm + reorder the per-material peel order for a single job."""

    def __init__(
        self,
        job_name: str,
        materials: list[tuple[str, int]],
        include_separators: bool,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)

        self._job_name = job_name
        self._include_separators = include_separators
        self._materials: list[tuple[str, int]] = list(materials)

        self.setWindowTitle(f"Print Order - {job_name}")
        self.resize(480, 520)
        self.setMinimumSize(420, 460)

        root = QVBoxLayout(self)

        # -- header --------------------------------------------------------
        total_labels = sum(count for _, count in self._materials)
        label_word = "label" if total_labels == 1 else "labels"
        header = QLabel(
            f"Printing <b>{job_name}</b><br>"
            f"<span style='color:#555'>{total_labels} {label_word} across "
            f"{len(self._materials)} material"
            f"{'s' if len(self._materials) != 1 else ''}</span>"
        )
        header.setTextFormat(Qt.RichText)
        header.setAlignment(Qt.AlignCenter)
        header.setWordWrap(True)
        root.addWidget(header)

        # Small spacer-like instruction under the header.
        hint = QLabel("Drag rows to change the peel order.")
        hint.setAlignment(Qt.AlignCenter)
        hint_font = hint.font()
        hint_font.setItalic(True)
        hint.setFont(hint_font)
        hint.setStyleSheet("color: #666;")
        root.addWidget(hint)

        # -- top arrow (peel first) ----------------------------------------
        self._top_arrow = QLabel("\u2b06 PEEL FIRST  \u2014  top of roll")
        self._top_arrow.setAlignment(Qt.AlignCenter)
        top_font = QFont()
        top_font.setBold(True)
        top_font.setPointSize(10)
        self._top_arrow.setFont(top_font)
        self._top_arrow.setStyleSheet(
            "color: #2e7d32; padding: 4px; background: #f1f8e9; "
            "border-radius: 4px;"
        )
        root.addWidget(self._top_arrow)

        # -- draggable list ------------------------------------------------
        self._list = QListWidget()
        self._list.setDragDropMode(QAbstractItemView.InternalMove)
        self._list.setDefaultDropAction(Qt.MoveAction)
        self._list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._list.setMovement(QListWidget.Snap)
        self._list.setAlternatingRowColors(False)
        self._list.setUniformItemSizes(False)
        self._list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        list_font = QFont()
        list_font.setPointSize(14)
        self._list.setFont(list_font)

        palette = self.default_palette()
        for index, (material, count) in enumerate(self._materials):
            label_word = "label" if count == 1 else "labels"
            text = f"  {material}   -   {count} {label_word}"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, material)
            item.setSizeHint(
                self._list.sizeHintForIndex(
                    self._list.model().index(index, 0)
                )
            )
            # Explicit row height. sizeHintForIndex can return an invalid size
            # before the item is inserted, so fall back to a fixed 50px.
            current = item.sizeHint()
            item.setSizeHint(current.expandedTo(current.__class__(0, 50)))
            brush = QBrush(palette[index % len(palette)])
            item.setBackground(brush)
            item.setForeground(QBrush(QColor(30, 30, 30)))
            self._list.addItem(item)

        # Any change to row order emits rowsMoved on the internal model.
        self._list.model().rowsMoved.connect(
            lambda *_args: self._update_preview()
        )
        root.addWidget(self._list, stretch=1)

        # -- bottom arrow (printed first) ----------------------------------
        self._bottom_arrow = QLabel(
            "\u2b07 PRINTED FIRST  \u2014  bottom of roll"
        )
        self._bottom_arrow.setAlignment(Qt.AlignCenter)
        bot_font = QFont()
        bot_font.setBold(True)
        bot_font.setPointSize(10)
        self._bottom_arrow.setFont(bot_font)
        self._bottom_arrow.setStyleSheet(
            "color: #757575; padding: 4px; background: #fafafa; "
            "border-radius: 4px;"
        )
        root.addWidget(self._bottom_arrow)

        # -- preview -------------------------------------------------------
        preview_label = QLabel("Preview:")
        preview_font = preview_label.font()
        preview_font.setBold(True)
        preview_label.setFont(preview_font)
        root.addWidget(preview_label)

        self._preview = QLabel("")
        self._preview.setFont(QFont("Consolas", 9))
        self._preview.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._preview.setTextFormat(Qt.PlainText)
        self._preview.setStyleSheet(
            "background: #f5f5f5; padding: 6px; border: 1px solid #ddd;"
        )
        self._preview.setWordWrap(False)
        root.addWidget(self._preview)

        # -- button box ----------------------------------------------------
        self._button_box = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        self._ok_button = self._button_box.button(QDialogButtonBox.Ok)
        if self._ok_button is not None:
            self._ok_button.setText("Print")
            # If there are no materials there is nothing to print — disable
            # the affirmative action but leave Cancel available so the user
            # can close the dialog cleanly.
            self._ok_button.setEnabled(len(self._materials) > 0)
        self._button_box.accepted.connect(self.accept)
        self._button_box.rejected.connect(self.reject)
        root.addWidget(self._button_box)

        self._update_preview()

    # -- public API --------------------------------------------------------

    @staticmethod
    def default_palette() -> list[QColor]:
        """Return the pastel colours cycled across list rows, in order."""
        return list(_PASTEL_PALETTE)

    def get_ordered_materials(self) -> tuple[str, ...]:
        """Return current peel order top-to-bottom as a tuple of material names.

        The first element is the top-of-list row (peeled first / printed
        last). The caller uses this tuple as the new
        ``AppSettings.material_priority`` and also feeds it straight into
        :func:`print_sequencer.build_print_sequence` as ``material_priority``.
        """
        names: list[str] = []
        for row in range(self._list.count()):
            item = self._list.item(row)
            material = item.data(Qt.UserRole)
            if material is None:
                material = item.text().strip()
            names.append(str(material))
        return tuple(names)

    # -- internal helpers --------------------------------------------------

    def _current_order(self) -> list[tuple[str, int]]:
        """Walk the list widget and return ``[(material, count), ...]``.

        Uses the counts stashed in ``self._materials`` at construction — the
        counts never change, only the order, so we just re-key the originals.
        """
        count_by_name = {material: count for material, count in self._materials}
        ordered: list[tuple[str, int]] = []
        for row in range(self._list.count()):
            name = str(self._list.item(row).data(Qt.UserRole))
            ordered.append((name, count_by_name.get(name, 0)))
        return ordered

    def _update_preview(self) -> None:
        """Refresh the preview label from the list widget's current order."""
        ordered = self._current_order()
        if not ordered:
            self._preview.setText("(no materials detected)")
            return

        lines: list[str] = ["Peel order (top -> bottom):"]
        line_index = 0
        for material, count in ordered:
            if self._include_separators:
                line_index += 1
                if line_index == 1:
                    # Topmost separator carries the job name — matches the
                    # is_job_separator=True branch in build_print_sequence.
                    sep_text = f"{self._job_name} / {material}"
                else:
                    sep_text = material
                lines.append(f"  {line_index}. [SEP] {sep_text}")
            line_index += 1
            label_word = "label" if count == 1 else "labels"
            lines.append(f"  {line_index}. {material} - {count} {label_word}")

        self._preview.setText("\n".join(lines))
