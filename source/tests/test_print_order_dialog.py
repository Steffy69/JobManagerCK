"""Tests for :class:`print_order_dialog.PrintOrderDialog`.

Uses pytest-qt. The dialog has no I/O or external dependencies so tests
are fully in-process — we just construct, inspect, and programmatically
reorder rows.
"""

from __future__ import annotations

# IMPORTANT: this must run before any PyQt import so pytest-qt binds to
# PyQt5 rather than a concurrently installed PyQt6.
import os as _os

_os.environ.setdefault("PYTEST_QT_API", "pyqt5")

import os
import sys

import pytest

sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
)

pytest.importorskip("PyQt5.QtWidgets")

from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtGui import QColor  # noqa: E402
from PyQt5.QtWidgets import QDialog, QDialogButtonBox  # noqa: E402

from print_order_dialog import PrintOrderDialog  # noqa: E402


# ---------------------------------------------------------------------------
# construction
# ---------------------------------------------------------------------------


def test_dialog_constructs(qtbot) -> None:
    dlg = PrintOrderDialog(
        job_name="JELPREWIR",
        materials=[("WHMR", 34), ("WALNUT", 16), ("BlackHMR", 3)],
        include_separators=True,
    )
    qtbot.addWidget(dlg)
    assert dlg.windowTitle() == "Print Order - JELPREWIR"
    assert dlg._list.count() == 3


def test_list_populated_in_given_order(qtbot) -> None:
    materials = [("WHMR", 34), ("WALNUT", 16)]
    dlg = PrintOrderDialog(
        job_name="JOB",
        materials=materials,
        include_separators=True,
    )
    qtbot.addWidget(dlg)
    assert dlg._list.count() == 2
    row0 = dlg._list.item(0).data(Qt.UserRole)
    row1 = dlg._list.item(1).data(Qt.UserRole)
    assert row0 == "WHMR"
    assert row1 == "WALNUT"
    # Label text should echo both the material and its count.
    assert "WHMR" in dlg._list.item(0).text()
    assert "34" in dlg._list.item(0).text()
    assert "WALNUT" in dlg._list.item(1).text()
    assert "16" in dlg._list.item(1).text()


def test_get_ordered_materials_returns_current_order(qtbot) -> None:
    dlg = PrintOrderDialog(
        job_name="JOB",
        materials=[("WHMR", 10), ("WALNUT", 5), ("BlackHMR", 2)],
        include_separators=True,
    )
    qtbot.addWidget(dlg)
    assert dlg.get_ordered_materials() == ("WHMR", "WALNUT", "BlackHMR")


def test_rows_have_pastel_backgrounds(qtbot) -> None:
    dlg = PrintOrderDialog(
        job_name="JOB",
        materials=[
            ("WHMR", 10),
            ("WALNUT", 5),
            ("BlackHMR", 2),
            ("MO", 4),
        ],
        include_separators=True,
    )
    qtbot.addWidget(dlg)
    palette = PrintOrderDialog.default_palette()
    assert len(palette) >= 4
    for row in range(dlg._list.count()):
        brush = dlg._list.item(row).background()
        color = brush.color()
        assert isinstance(color, QColor)
        expected = palette[row % len(palette)]
        assert color.rgb() == expected.rgb()


def test_palette_cycles_when_more_rows_than_colors(qtbot) -> None:
    palette_len = len(PrintOrderDialog.default_palette())
    materials = [(f"M{i}", i + 1) for i in range(palette_len + 2)]
    dlg = PrintOrderDialog(
        job_name="JOB",
        materials=materials,
        include_separators=False,
    )
    qtbot.addWidget(dlg)
    palette = PrintOrderDialog.default_palette()
    for row in range(dlg._list.count()):
        assert (
            dlg._list.item(row).background().color().rgb()
            == palette[row % palette_len].rgb()
        )


# ---------------------------------------------------------------------------
# preview
# ---------------------------------------------------------------------------


def test_preview_updates_on_init(qtbot) -> None:
    dlg = PrintOrderDialog(
        job_name="JOB",
        materials=[("WHMR", 34), ("WALNUT", 16), ("BlackHMR", 3)],
        include_separators=True,
    )
    qtbot.addWidget(dlg)
    text = dlg._preview.text()
    assert "WHMR" in text
    assert "WALNUT" in text
    assert "BlackHMR" in text
    assert "34" in text
    assert "16" in text
    assert "3" in text
    assert "Peel order" in text


def test_preview_without_separators(qtbot) -> None:
    dlg = PrintOrderDialog(
        job_name="JOB",
        materials=[("WHMR", 10), ("WALNUT", 5)],
        include_separators=False,
    )
    qtbot.addWidget(dlg)
    text = dlg._preview.text()
    assert "[SEP]" not in text
    assert "WHMR" in text
    assert "WALNUT" in text


def test_preview_with_separators_contains_sep_markers(qtbot) -> None:
    dlg = PrintOrderDialog(
        job_name="BIGJOB",
        materials=[("WHMR", 10), ("WALNUT", 5)],
        include_separators=True,
    )
    qtbot.addWidget(dlg)
    text = dlg._preview.text()
    assert "[SEP]" in text
    # Topmost separator carries the job name for clarity.
    assert "BIGJOB" in text


# ---------------------------------------------------------------------------
# reorder behaviour
# ---------------------------------------------------------------------------


def test_get_ordered_reflects_programmatic_reorder(qtbot) -> None:
    dlg = PrintOrderDialog(
        job_name="JOB",
        materials=[("WHMR", 10), ("WALNUT", 5), ("BlackHMR", 2)],
        include_separators=True,
    )
    qtbot.addWidget(dlg)

    # Move BlackHMR (row 2) to the top: take it out, insert at row 0.
    taken = dlg._list.takeItem(2)
    dlg._list.insertItem(0, taken)

    assert dlg.get_ordered_materials() == ("BlackHMR", "WHMR", "WALNUT")

    # Force a preview refresh — programmatic takeItem/insertItem does not
    # fire rowsMoved on every Qt build, so we update explicitly here and
    # assert the preview catches up.
    dlg._update_preview()
    assert dlg._preview.text().index("BlackHMR") < dlg._preview.text().index("WHMR")


# ---------------------------------------------------------------------------
# accept / cancel
# ---------------------------------------------------------------------------


def test_cancel_does_not_emit(qtbot) -> None:
    dlg = PrintOrderDialog(
        job_name="JOB",
        materials=[("WHMR", 10)],
        include_separators=True,
    )
    qtbot.addWidget(dlg)
    with qtbot.waitSignal(dlg.rejected, timeout=1000):
        dlg.reject()
    assert dlg.result() == QDialog.Rejected


def test_accept_closes_dialog(qtbot) -> None:
    dlg = PrintOrderDialog(
        job_name="JOB",
        materials=[("WHMR", 10)],
        include_separators=True,
    )
    qtbot.addWidget(dlg)
    with qtbot.waitSignal(dlg.accepted, timeout=1000):
        dlg.accept()
    assert dlg.result() == QDialog.Accepted


# ---------------------------------------------------------------------------
# empty materials
# ---------------------------------------------------------------------------


def test_empty_materials_handled(qtbot) -> None:
    dlg = PrintOrderDialog(
        job_name="JOB",
        materials=[],
        include_separators=True,
    )
    qtbot.addWidget(dlg)
    assert dlg._list.count() == 0
    # Print button should be disabled — nothing to print.
    ok_button = dlg._button_box.button(QDialogButtonBox.Ok)
    assert ok_button is not None
    assert ok_button.isEnabled() is False
    # get_ordered_materials returns an empty tuple.
    assert dlg.get_ordered_materials() == ()


def test_empty_materials_preview_message(qtbot) -> None:
    dlg = PrintOrderDialog(
        job_name="JOB",
        materials=[],
        include_separators=True,
    )
    qtbot.addWidget(dlg)
    assert "no materials" in dlg._preview.text().lower()
