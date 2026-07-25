"""Tests for ``source/printer_status_widget.PrinterStatusWidget``.

Uses pytest-qt's ``qtbot`` fixture and monkeypatches
``printer_service.is_printer_available`` / ``find_zebra_printer`` so polls
are deterministic and do not touch the real Windows print spooler.
"""

from __future__ import annotations

import os
import sys

import pytest

# Ensure pytest-qt binds to PyQt5 — the test suite also has PyQt6 in the env
# on some dev machines, and the root conftest does the same dance.
os.environ.setdefault("PYTEST_QT_API", "pyqt5")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

pytest.importorskip("PyQt5.QtWidgets")
pytest.importorskip("pytestqt")

import printer_status_widget  # noqa: E402
from printer_status_widget import PrinterStatusWidget  # noqa: E402


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_printer_service(monkeypatch):
    """Mutable container tests mutate to control poll results.

    The widget resolves both "which printer" and "is it there" from a single
    ``list_printers`` enumeration, so that is the one call to stub. Tests
    write ``state["available"]`` to simulate the printer coming online or
    offline, ``state["find_result"]`` to change which Zebra auto-detect
    should discover, and ``state["raise"]`` to force the backend to raise.
    """
    state = {
        # Printers the spooler reports as installed. A widget is "online"
        # when its target printer appears in here.
        "names": [],
        "list_calls": 0,
        "raise": False,
    }

    def fake_list_printers() -> list[str]:
        state["list_calls"] += 1
        if state["raise"]:
            raise RuntimeError("simulated printer service failure")
        return list(state["names"])

    monkeypatch.setattr(
        printer_status_widget, "list_printers", fake_list_printers
    )
    return state


# ---------------------------------------------------------------------------
# construction / appearance
# ---------------------------------------------------------------------------


def test_initial_state_disconnected(qtbot, stub_printer_service):
    """Widget starts offline until the first poll runs."""
    widget = PrinterStatusWidget(poll_interval_ms=10_000, printer_name="Zebra")
    qtbot.addWidget(widget)

    assert widget.is_online() is False
    assert widget._text_label.text() == "Zebra: Disconnected"
    # No polls should have happened yet — start() was never called.
    assert stub_printer_service["list_calls"] == 0


def test_start_runs_immediate_check_and_emits_online(
    qtbot, stub_printer_service
):
    """Calling start() should poll immediately and transition to online."""
    stub_printer_service["names"] = ["Zebra"]
    widget = PrinterStatusWidget(poll_interval_ms=10_000, printer_name="Zebra")
    qtbot.addWidget(widget)

    with qtbot.waitSignal(widget.statusChanged, timeout=1000) as blocker:
        widget.start()

    assert blocker.args == [True]
    assert widget.is_online() is True
    assert widget._text_label.text() == "Zebra: Connected"


def test_check_status_emits_on_transition(qtbot, stub_printer_service):
    """Transition False -> True emits exactly one statusChanged(True)."""
    stub_printer_service["names"] = ["Zebra"]
    widget = PrinterStatusWidget(poll_interval_ms=10_000, printer_name="Zebra")
    qtbot.addWidget(widget)

    with qtbot.waitSignal(widget.statusChanged, timeout=1000) as blocker:
        widget._check_status()

    assert blocker.args == [True]


def test_check_status_no_emit_on_same_state(qtbot, stub_printer_service):
    """Polling twice while the state is unchanged emits only once."""
    stub_printer_service["names"] = ["Zebra"]
    widget = PrinterStatusWidget(poll_interval_ms=10_000, printer_name="Zebra")
    qtbot.addWidget(widget)

    emissions: list[bool] = []
    widget.statusChanged.connect(emissions.append)

    widget._check_status()  # offline -> online (emits)
    widget._check_status()  # online -> online (no emit)

    assert emissions == [True]


def test_auto_detect_matches_stock_zdesigner_driver(qtbot, stub_printer_service):
    """The pill must recognise the stock Zebra driver name.

    Zebra's standard Windows driver installs as "ZDesigner GC420d", which
    contains no "zebra" substring. The widget shares printer_service's hint
    matching so the status pill agrees with preflight and the Print click —
    otherwise the printer passes preflight while the pill reads Disconnected,
    which leaves the Print button disabled.
    """
    stub_printer_service["names"] = ["Microsoft Print to PDF", "ZDesigner GC420d"]
    widget = PrinterStatusWidget(poll_interval_ms=10_000, printer_name="")
    qtbot.addWidget(widget)

    widget._check_status()

    assert widget.is_online() is True
    assert widget.resolved_printer_name() == "ZDesigner GC420d"


def test_single_enumeration_per_poll(qtbot, stub_printer_service):
    """Auto-detect resolves target and availability from one enumeration."""
    stub_printer_service["names"] = ["Zebra GC420D"]
    widget = PrinterStatusWidget(poll_interval_ms=10_000, printer_name="")
    qtbot.addWidget(widget)

    widget._check_status()

    assert stub_printer_service["list_calls"] == 1
    assert widget.is_online() is True


def test_check_status_toggles(qtbot, stub_printer_service):
    """True -> False -> True yields exactly three emissions."""
    widget = PrinterStatusWidget(poll_interval_ms=10_000, printer_name="Zebra")
    qtbot.addWidget(widget)

    emissions: list[bool] = []
    widget.statusChanged.connect(emissions.append)

    # After construction the widget is offline. Starting from offline:
    stub_printer_service["names"] = ["Zebra"]
    widget._check_status()  # -> True  (emit)
    stub_printer_service["names"] = []
    widget._check_status()  # -> False (emit)
    stub_printer_service["names"] = ["Zebra"]
    widget._check_status()  # -> True  (emit)

    assert emissions == [True, False, True]


# ---------------------------------------------------------------------------
# printer-name handling
# ---------------------------------------------------------------------------


def test_auto_detect_when_printer_name_empty(qtbot, stub_printer_service):
    """Empty printer_name should discover the Zebra among installed printers."""
    stub_printer_service["names"] = ["Microsoft Print to PDF", "Some Zebra Clone"]
    widget = PrinterStatusWidget(poll_interval_ms=10_000, printer_name="")
    qtbot.addWidget(widget)

    widget._check_status()

    assert widget.is_online() is True
    assert widget.resolved_printer_name() == "Some Zebra Clone"


def test_auto_detect_none_marks_offline(qtbot, stub_printer_service):
    """No Zebra among the installed printers leaves the widget offline."""
    stub_printer_service["names"] = ["Microsoft Print to PDF", "HP LaserJet"]
    widget = PrinterStatusWidget(poll_interval_ms=10_000, printer_name="")
    qtbot.addWidget(widget)

    widget._check_status()

    assert widget.is_online() is False
    assert widget.resolved_printer_name() == ""


def test_explicit_printer_name_used_when_provided(
    qtbot, stub_printer_service
):
    """A non-empty printer_name should be used verbatim, not auto-detected."""
    stub_printer_service["names"] = ["ZDesigner GC420D", "Some Zebra Clone"]
    widget = PrinterStatusWidget(
        poll_interval_ms=10_000, printer_name="ZDesigner GC420D"
    )
    qtbot.addWidget(widget)

    widget._check_status()

    assert widget.is_online() is True
    # The Zebra-named printer is present but must not win over the override.
    assert widget.resolved_printer_name() == "ZDesigner GC420D"


def test_explicit_printer_name_absent_marks_offline(
    qtbot, stub_printer_service
):
    """An override naming a printer that isn't installed reads as offline."""
    stub_printer_service["names"] = ["Some Zebra Clone"]
    widget = PrinterStatusWidget(
        poll_interval_ms=10_000, printer_name="ZDesigner GC420D"
    )
    qtbot.addWidget(widget)

    widget._check_status()

    assert widget.is_online() is False


def test_set_printer_name_triggers_recheck(qtbot, stub_printer_service):
    """set_printer_name should immediately re-poll against the new target."""
    stub_printer_service["names"] = ["Old Printer", "New Printer"]
    widget = PrinterStatusWidget(
        poll_interval_ms=10_000, printer_name="Old Printer"
    )
    qtbot.addWidget(widget)

    # Prime: one poll against "Old Printer".
    widget._check_status()
    assert widget.resolved_printer_name() == "Old Printer"
    calls_after_first = stub_printer_service["list_calls"]

    # Re-point at a new printer. set_printer_name should issue another poll.
    widget.set_printer_name("New Printer")

    assert stub_printer_service["list_calls"] == calls_after_first + 1
    assert widget.resolved_printer_name() == "New Printer"


# ---------------------------------------------------------------------------
# timer lifecycle
# ---------------------------------------------------------------------------


def test_start_stops_timer_on_stop(qtbot, stub_printer_service):
    """Starting the widget runs the timer; stop() halts it."""
    widget = PrinterStatusWidget(poll_interval_ms=10_000, printer_name="Zebra")
    qtbot.addWidget(widget)

    assert widget._timer.isActive() is False
    widget.start()
    assert widget._timer.isActive() is True
    widget.stop()
    assert widget._timer.isActive() is False


def test_set_poll_interval_updates_timer(qtbot, stub_printer_service):
    widget = PrinterStatusWidget(poll_interval_ms=10_000, printer_name="Zebra")
    qtbot.addWidget(widget)

    widget.set_poll_interval(5_000)

    assert widget._timer.interval() == 5_000


# ---------------------------------------------------------------------------
# exception resilience
# ---------------------------------------------------------------------------


def test_check_status_never_crashes_on_exception(qtbot, stub_printer_service):
    """If the backend raises, the widget catches it, marks offline, and
    propagates nothing."""
    # First make the widget believe the printer is online so we can observe
    # the transition to offline caused by the exception.
    stub_printer_service["names"] = ["Zebra"]
    widget = PrinterStatusWidget(poll_interval_ms=10_000, printer_name="Zebra")
    qtbot.addWidget(widget)
    widget._check_status()
    assert widget.is_online() is True

    # Now break the backend and poll again — should transition to offline
    # with exactly one statusChanged(False) emission.
    stub_printer_service["raise"] = True
    emissions: list[bool] = []
    widget.statusChanged.connect(emissions.append)

    widget._check_status()  # must not raise

    assert widget.is_online() is False
    assert emissions == [False]
    assert widget._text_label.text() == "Zebra: Disconnected"
