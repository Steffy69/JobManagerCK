"""Tests for source/label_printer.py.

Covers ``LabelPrinterThread``'s description helper and its ``run()`` loop —
the run-level default-printer swap fallback, restore-on-every-exit, and
cancellation. ``run()`` is invoked synchronously with ``printer_service``
stubbed, so no real printer (or thread) is involved.
"""

from __future__ import annotations


import pytest

pytest.importorskip("PyQt5.QtWidgets")

import printer_service  # noqa: E402
from label_printer import LabelPrinterThread  # noqa: E402
from print_sequencer import (  # noqa: E402
    LABEL_KIND,
    SEPARATOR_LABEL_KIND,
    PrintItem,
)
from settings import AppSettings  # noqa: E402


@pytest.fixture()
def _qapp():
    """Ensure a QApplication exists before instantiating any QObject."""
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def _label(path: str = "/tmp/JOB_WHMR_0001.ljd", board: int = 1) -> PrintItem:
    return PrintItem(
        kind=LABEL_KIND, file_path=path, material="WHMR", board_number=board
    )


def _separator(material: str = "WHMR", job: str = "JOB") -> PrintItem:
    return PrintItem(
        kind=SEPARATOR_LABEL_KIND,
        file_path="",
        material=material,
        board_number=None,
        job_name=job,
    )


def _fast_settings() -> AppSettings:
    """Settings with zero delays so run() completes instantly in tests."""
    return AppSettings(print_delay_seconds=0.0, separator_delay_seconds=0.0)


@pytest.fixture()
def printer_stub(monkeypatch):
    """Record printer_service calls; individual tests adjust behaviour."""
    calls: dict[str, list] = {
        "printto": [],
        "print_verb": [],
        "raw_zpl": [],
        "set_default": [],
        "marker_saved": [],
        "marker_cleared": [],
    }
    state = {"printto_fails": False, "default": "HP LaserJet"}

    def fake_printto(printer, path):
        if state["printto_fails"]:
            raise OSError("printto rejected")
        calls["printto"].append((printer, path))

    monkeypatch.setattr(
        printer_service, "print_via_shellexecute", fake_printto
    )
    monkeypatch.setattr(
        printer_service,
        "print_via_print_verb",
        lambda path: calls["print_verb"].append(path),
    )
    monkeypatch.setattr(
        printer_service,
        "send_raw_zpl",
        lambda printer, zpl, doc_name="": calls["raw_zpl"].append(
            (printer, zpl)
        ),
    )
    monkeypatch.setattr(
        printer_service,
        "get_default_printer",
        lambda: state["default"],
    )
    monkeypatch.setattr(
        printer_service,
        "set_default_printer",
        lambda name: calls["set_default"].append(name),
    )
    monkeypatch.setattr(
        printer_service,
        "save_default_printer_marker",
        lambda name: calls["marker_saved"].append(name),
    )
    monkeypatch.setattr(
        printer_service,
        "clear_default_printer_marker",
        lambda: calls["marker_cleared"].append(True),
    )
    return {"calls": calls, "state": state}


def _run_thread(sequence, printer="Zebra GC420D", settings=None):
    """Run the thread body synchronously, returning collected signals."""
    thread = LabelPrinterThread(
        sequence=sequence,
        settings=settings or _fast_settings(),
        zebra_printer=printer,
    )
    progress: list[tuple] = []
    finished: list[tuple] = []
    thread.progress.connect(lambda *a: progress.append(a))
    thread.finished.connect(lambda *a: finished.append(a))
    thread.run()
    return thread, progress, finished


# ---------------------------------------------------------------------------
# _describe_item
# ---------------------------------------------------------------------------


def test_describe_item_label_includes_material_and_board() -> None:
    desc = LabelPrinterThread._describe_item(
        _label("/some/path/JOB_WHMR_0007.ljd", board=7)
    )
    assert "WHMR" in desc
    assert "#7" in desc
    assert "JOB_WHMR_0007.ljd" in desc


def test_describe_item_separator_includes_job_name() -> None:
    desc = LabelPrinterThread._describe_item(
        _separator("WALNUT", "JELPREWIR CL")
    )
    assert "JELPREWIR CL" in desc
    assert "WALNUT" in desc
    assert desc.startswith("Separator:")


def test_describe_item_separator_without_job_name_falls_back() -> None:
    desc = LabelPrinterThread._describe_item(_separator("WALNUT", ""))
    assert desc == "Separator: WALNUT"


# ---------------------------------------------------------------------------
# run() — happy path
# ---------------------------------------------------------------------------


def test_run_prints_labels_and_separators(_qapp, printer_stub) -> None:
    sequence = [_label(board=2), _label(board=1), _separator()]

    _, progress, finished = _run_thread(sequence)

    assert len(printer_stub["calls"]["printto"]) == 2
    assert len(printer_stub["calls"]["raw_zpl"]) == 1
    assert len(progress) == 3
    assert finished == [(True, "Printed 3 items (2 labels + 1 separators)")]
    # No fallback fired: default printer untouched.
    assert printer_stub["calls"]["set_default"] == []


def test_run_empty_sequence_fails_cleanly(_qapp, printer_stub) -> None:
    _, _, finished = _run_thread([])
    assert finished == [(False, "No labels to print")]


def test_run_without_printer_fails_cleanly(_qapp, printer_stub) -> None:
    _, _, finished = _run_thread([_label()], printer="")
    assert finished == [(False, "No Zebra printer configured")]


# ---------------------------------------------------------------------------
# run() — run-level default-printer swap fallback
# ---------------------------------------------------------------------------


def test_printto_failure_swaps_once_for_whole_run(_qapp, printer_stub) -> None:
    """First printto failure flips the run into swap mode; the swap happens
    once, every remaining label uses the print verb, and the original
    default is restored at the end."""
    printer_stub["state"]["printto_fails"] = True
    sequence = [_label(board=3), _label(board=2), _label(board=1)]

    _, _, finished = _run_thread(sequence)

    calls = printer_stub["calls"]
    # All three labels went out via the print verb (fallback).
    assert len(calls["print_verb"]) == 3
    # Exactly one swap to the Zebra, then one restore to the saved default.
    assert calls["set_default"] == ["Zebra GC420D", "HP LaserJet"]
    # Crash-recovery marker saved before the swap, cleared after restore.
    assert calls["marker_saved"] == ["HP LaserJet"]
    assert calls["marker_cleared"] == [True]
    # Clean restore -> success with no warning appended.
    assert finished[0][0] is True
    assert "WARNING" not in finished[0][1]


def test_failed_restore_appends_warning_and_keeps_marker(
    _qapp, printer_stub, monkeypatch
) -> None:
    printer_stub["state"]["printto_fails"] = True

    restore_attempts: list[str] = []

    def failing_set_default(name):
        if name == "Zebra GC420D":
            return  # the swap itself succeeds
        restore_attempts.append(name)
        raise OSError("spooler busy")

    monkeypatch.setattr(
        printer_service, "set_default_printer", failing_set_default
    )

    _, _, finished = _run_thread([_label()])

    assert restore_attempts == ["HP LaserJet"]
    success, message = finished[0]
    assert success is True  # the print itself worked
    assert "WARNING" in message
    assert "HP LaserJet" in message
    # Marker must survive so the next launch can retry the restore.
    assert printer_stub["calls"]["marker_cleared"] == []


def test_no_previous_default_warns_instead_of_crashing(
    _qapp, printer_stub
) -> None:
    printer_stub["state"]["printto_fails"] = True
    printer_stub["state"]["default"] = None

    _, _, finished = _run_thread([_label()])

    success, message = finished[0]
    assert success is True
    assert "WARNING" in message
    # Only the swap-to-Zebra happened; nothing to restore.
    assert printer_stub["calls"]["set_default"] == ["Zebra GC420D"]


# ---------------------------------------------------------------------------
# run() — cancellation
# ---------------------------------------------------------------------------


def test_cancel_before_first_item(_qapp, printer_stub) -> None:
    thread = LabelPrinterThread(
        sequence=[_label(), _label()],
        settings=_fast_settings(),
        zebra_printer="Zebra",
    )
    thread.isInterruptionRequested = lambda: True  # type: ignore[method-assign]
    finished: list[tuple] = []
    thread.finished.connect(lambda *a: finished.append(a))

    thread.run()

    assert finished[0][0] is False
    assert "Cancelled" in finished[0][1]
    assert "sent 0 of 2" in finished[0][1]
    assert printer_stub["calls"]["printto"] == []


def test_cancel_mid_run_reports_progress_and_restores(
    _qapp, printer_stub
) -> None:
    """Cancel after the first item: the message reports 1 of N sent, and a
    swapped default printer is restored on the cancel path too."""
    printer_stub["state"]["printto_fails"] = True

    thread = LabelPrinterThread(
        sequence=[_label(board=2), _label(board=1)],
        settings=AppSettings(
            print_delay_seconds=0.2, separator_delay_seconds=0.2
        ),
        zebra_printer="Zebra GC420D",
    )
    # Allow the first item through, then request cancellation (the check
    # inside the inter-item sleep picks it up).
    checks = {"count": 0}

    def interruption() -> bool:
        checks["count"] += 1
        return checks["count"] > 1

    thread.isInterruptionRequested = interruption  # type: ignore[method-assign]
    finished: list[tuple] = []
    thread.finished.connect(lambda *a: finished.append(a))

    thread.run()

    success, message = finished[0]
    assert success is False
    assert "Cancelled" in message
    assert "sent 1 of 2" in message
    # Swap happened for item 1; cancel path must still restore.
    assert printer_stub["calls"]["set_default"] == [
        "Zebra GC420D",
        "HP LaserJet",
    ]


# ---------------------------------------------------------------------------
# run() — per-kind delays
# ---------------------------------------------------------------------------


def test_separator_uses_its_own_delay(_qapp, printer_stub, monkeypatch) -> None:
    sleeps: list[float] = []

    thread = LabelPrinterThread(
        sequence=[_separator(), _label(board=2), _label(board=1)],
        settings=AppSettings(
            print_delay_seconds=1.0, separator_delay_seconds=2.5
        ),
        zebra_printer="Zebra",
    )
    monkeypatch.setattr(
        thread,
        "_interruptible_sleep",
        lambda seconds: sleeps.append(seconds) or True,
    )
    finished: list[tuple] = []
    thread.finished.connect(lambda *a: finished.append(a))

    thread.run()

    # Two inter-item gaps: after the separator (2.5s) and after label 1 (1.0s).
    assert sleeps == [2.5, 1.0]
    assert finished[0][0] is True
