"""Unit tests for :mod:`label_printer`.

Focus is on the pure :func:`build_sequence_preview` helper. The Qt worker
thread itself is not covered here — it composes :mod:`printer_service` and
:mod:`zpl_templates` which both have their own dedicated test suites, and a
real ``QThread`` that also invokes win32 APIs is painful to mock usefully.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from label_printer import (
    LabelPrinterThread,
    build_sequence_peel_preview,
    build_sequence_preview,
)
from print_sequencer import (
    LABEL_KIND,
    SEPARATOR_LABEL_KIND,
    PrintItem,
    build_print_sequence,
)


# ---------------------------------------------------------------------------
# build_sequence_preview
# ---------------------------------------------------------------------------


def test_build_sequence_preview_empty_returns_placeholder() -> None:
    assert build_sequence_preview([]) == "(empty sequence)"


def test_build_sequence_preview_single_material_with_separator() -> None:
    """A single material with separators reports one group plus the separator count."""
    sequence = build_print_sequence(
        job_name="JOB1",
        ljd_files=[
            "JOB1_WHMR_0001.ljd",
            "JOB1_WHMR_0002.ljd",
            "JOB1_WHMR_0003.ljd",
            "JOB1_WHMR_0004.ljd",
            "JOB1_WHMR_0005.ljd",
        ],
        material_priority=("WHMR",),
        include_separators=True,
    )
    preview = build_sequence_preview(sequence)
    assert preview == "WHMR: 5 labels\n1 separator"


def test_build_sequence_preview_single_label_uses_singular_word() -> None:
    """A single label reports 'label' not 'labels'."""
    item = PrintItem(
        kind=LABEL_KIND,
        file_path="JOB1_WHMR_0001.ljd",
        material="WHMR",
        board_number=1,
    )
    preview = build_sequence_preview([item])
    assert preview == "WHMR: 1 label"


def test_build_sequence_preview_multi_material_jelprewir_example() -> None:
    """Mirrors the JELPREWIR CL example in V2.1-PLAN.md.

    Peel order (top of list = first peeled) is
    WHMR -> WALNUT -> BlackHMR, which is the default priority.
    Counts: WHMR=34, WALNUT=16, BlackHMR=3, plus 3 separators.
    """
    ljd_files: list[str] = []
    ljd_files.extend(
        f"JELPREWIR_WHMR_{n:04d}.ljd" for n in range(1, 35)
    )  # 34 WHMR
    ljd_files.extend(
        f"JELPREWIR_WALNUT_{n:04d}.ljd" for n in range(1, 17)
    )  # 16 WALNUT
    ljd_files.extend(
        f"JELPREWIR_BlackHMR_{n:04d}.ljd" for n in range(1, 4)
    )  # 3 BlackHMR

    sequence = build_print_sequence(
        job_name="JELPREWIR CL",
        ljd_files=ljd_files,
        material_priority=("WHMR", "WALNUT", "BlackHMR"),
        include_separators=True,
    )

    preview = build_sequence_preview(sequence)
    # Peel order = WHMR, WALNUT, BlackHMR (that's what the preview lists).
    expected_lines = [
        "WHMR: 34 labels",
        "WALNUT: 16 labels",
        "BlackHMR: 3 labels",
        "3 separators",
    ]
    assert preview == "\n".join(expected_lines)


def test_build_sequence_preview_no_separators() -> None:
    """With separators disabled, the preview omits the separator line."""
    ljd_files = [
        "JOB_WHMR_0001.ljd",
        "JOB_WHMR_0002.ljd",
        "JOB_WALNUT_0001.ljd",
    ]
    sequence = build_print_sequence(
        job_name="JOB",
        ljd_files=ljd_files,
        material_priority=("WHMR", "WALNUT"),
        include_separators=False,
    )
    preview = build_sequence_preview(sequence)
    assert preview == "WHMR: 2 labels\nWALNUT: 1 label"
    assert "separator" not in preview


def test_build_sequence_preview_preserves_peel_order_not_print_order() -> None:
    """The preview should list materials in peel order (first peeled first).

    The sequence arrives in *print* order, which is the reverse. This test
    pins the expected ordering so future refactors can't silently flip it.
    """
    ljd_files = [
        "JOB_WHMR_0001.ljd",
        "JOB_WALNUT_0001.ljd",
        "JOB_BlackHMR_0001.ljd",
    ]
    sequence = build_print_sequence(
        job_name="JOB",
        ljd_files=ljd_files,
        material_priority=("WHMR", "WALNUT", "BlackHMR"),
        include_separators=False,
    )
    preview = build_sequence_preview(sequence)

    # First material in the preview must be WHMR (top of peel priority).
    first_line = preview.splitlines()[0]
    assert first_line.startswith("WHMR")


# ---------------------------------------------------------------------------
# build_sequence_peel_preview
# ---------------------------------------------------------------------------


def test_peel_preview_empty_sequence() -> None:
    """Empty input collapses to the placeholder string."""
    assert build_sequence_peel_preview([], "JOB") == "(empty sequence)"


def test_peel_preview_single_material() -> None:
    """A single-material job renders one separator + one label group + total."""
    sequence = build_print_sequence(
        job_name="JOB1",
        ljd_files=[
            "JOB1_WHMR_0001.ljd",
            "JOB1_WHMR_0002.ljd",
            "JOB1_WHMR_0003.ljd",
        ],
        material_priority=("WHMR",),
        include_separators=True,
    )
    preview = build_sequence_peel_preview(sequence, "JOB1")
    lines = preview.splitlines()
    assert lines[0] == "Peel order (top of roll -> bottom):"
    # Topmost peel = job separator for the only material
    assert lines[1] == "  1. [SEP] JOB1 / WHMR"
    assert lines[2] == "  2. WHMR labels (3)"
    # Blank spacer then total
    assert lines[-2] == ""
    assert lines[-1] == "Total: 4 items (3 labels + 1 separator)"


def test_peel_preview_jelprewir_example() -> None:
    """Mirrors the JELPREWIR CL 56-item example from V2.1-PLAN.md.

    Peel order (from the top of the roll down): separator JOB / WHMR,
    34 WHMR labels, WALNUT separator, 16 WALNUT labels, BlackHMR separator,
    3 BlackHMR labels. Total: 56 items (53 labels + 3 separators).
    """
    ljd_files: list[str] = []
    ljd_files.extend(f"JELPREWIR_WHMR_{n:04d}.ljd" for n in range(1, 35))
    ljd_files.extend(f"JELPREWIR_WALNUT_{n:04d}.ljd" for n in range(1, 17))
    ljd_files.extend(f"JELPREWIR_BlackHMR_{n:04d}.ljd" for n in range(1, 4))

    sequence = build_print_sequence(
        job_name="JELPREWIR CL",
        ljd_files=ljd_files,
        material_priority=("WHMR", "WALNUT", "BlackHMR"),
        include_separators=True,
    )

    preview = build_sequence_peel_preview(sequence, "JELPREWIR CL")
    # Required substrings from the brief.
    assert "WHMR labels (34)" in preview
    assert "WALNUT labels (16)" in preview
    assert "BlackHMR labels (3)" in preview
    assert "JELPREWIR CL / WHMR" in preview
    assert "Total: 56 items (53 labels + 3 separators)" in preview

    # Full structural check — peel order is numbered 1..6 in a known layout.
    lines = preview.splitlines()
    assert lines[0] == "Peel order (top of roll -> bottom):"
    assert lines[1] == "  1. [SEP] JELPREWIR CL / WHMR"
    assert lines[2] == "  2. WHMR labels (34)"
    assert lines[3] == "  3. [SEP] WALNUT"
    assert lines[4] == "  4. WALNUT labels (16)"
    assert lines[5] == "  5. [SEP] BlackHMR"
    assert lines[6] == "  6. BlackHMR labels (3)"


def test_peel_preview_no_separators() -> None:
    """With separators disabled, no [SEP] lines and total omits the separator bracket."""
    ljd_files = [
        "JOB_WHMR_0001.ljd",
        "JOB_WHMR_0002.ljd",
        "JOB_WALNUT_0001.ljd",
    ]
    sequence = build_print_sequence(
        job_name="JOB",
        ljd_files=ljd_files,
        material_priority=("WHMR", "WALNUT"),
        include_separators=False,
    )
    preview = build_sequence_peel_preview(sequence, "JOB")
    assert "[SEP]" not in preview
    assert "separator" not in preview
    # Total must report raw label count only.
    assert "Total: 3 items (3 labels)" in preview
    # Label group lines still render in peel order. WHMR has 2 (plural),
    # WALNUT has 1 (singular — the grouping collapses exactly one item).
    lines = preview.splitlines()
    assert lines[1] == "  1. WHMR labels (2)"
    assert lines[2] == "  2. WALNUT label (1)"


# ---------------------------------------------------------------------------
# LabelPrinterThread._describe_item
# ---------------------------------------------------------------------------


def test_describe_item_label_includes_material_and_board() -> None:
    item = PrintItem(
        kind=LABEL_KIND,
        file_path="/some/path/JOB_WHMR_0007.ljd",
        material="WHMR",
        board_number=7,
    )
    desc = LabelPrinterThread._describe_item(item)
    assert "WHMR" in desc
    assert "#7" in desc
    assert "JOB_WHMR_0007.ljd" in desc


def test_describe_item_material_separator() -> None:
    item = PrintItem(
        kind=SEPARATOR_LABEL_KIND,
        file_path="",
        material="WALNUT",
        board_number=None,
        is_job_separator=False,
    )
    desc = LabelPrinterThread._describe_item(item)
    assert desc == "Separator: WALNUT"


def test_describe_item_job_separator() -> None:
    item = PrintItem(
        kind=SEPARATOR_LABEL_KIND,
        file_path="",
        material="WHMR",
        board_number=None,
        is_job_separator=True,
        job_name="JELPREWIR CL",
    )
    desc = LabelPrinterThread._describe_item(item)
    assert "JELPREWIR CL" in desc
    assert "WHMR" in desc
    assert desc.startswith("Separator:")


# ---------------------------------------------------------------------------
# LabelPrinterThread construction (no run — we don't touch the printer here)
# ---------------------------------------------------------------------------


@pytest.fixture()
def _qapp():
    """Ensure a QApplication exists before instantiating any QObject."""
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def test_thread_construction_stores_dependencies(_qapp) -> None:
    from settings import AppSettings

    settings = AppSettings()
    items = [
        PrintItem(
            kind=LABEL_KIND,
            file_path="/tmp/JOB_WHMR_0001.ljd",
            material="WHMR",
            board_number=1,
        )
    ]
    thread = LabelPrinterThread(
        sequence=items, settings=settings, zebra_printer="Zebra GC420D"
    )
    # Internal fields are private, but we can at least assert signals exist.
    assert thread.progress is not None
    assert thread.finished is not None
