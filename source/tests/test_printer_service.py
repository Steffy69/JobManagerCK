"""Tests for source/printer_service.py."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, call

import pytest

import printer_service
from printer_service import PrinterServiceUnavailable


@pytest.fixture
def fake_win32print(monkeypatch):
    """Provide a MagicMock standing in for the win32print module."""
    mock = MagicMock(name="win32print")
    mock.PRINTER_ENUM_LOCAL = 0x02
    mock.PRINTER_ENUM_CONNECTIONS = 0x04
    mock.JOB_CONTROL_DELETE = 5
    monkeypatch.setattr(printer_service, "win32print", mock)
    monkeypatch.setattr(printer_service, "HAS_WIN32", True)
    return mock


@pytest.fixture
def fake_win32api(monkeypatch):
    mock = MagicMock(name="win32api")
    monkeypatch.setattr(printer_service, "win32api", mock)
    monkeypatch.setattr(printer_service, "HAS_WIN32", True)
    return mock


@pytest.fixture
def no_win32(monkeypatch):
    monkeypatch.setattr(printer_service, "HAS_WIN32", False)
    monkeypatch.setattr(printer_service, "win32print", None)
    monkeypatch.setattr(printer_service, "win32api", None)


# ---------------------------------------------------------------------------
# list_printers
# ---------------------------------------------------------------------------


def test_list_printers_returns_names(fake_win32print):
    fake_win32print.EnumPrinters.return_value = [
        (0, "", "ZEBRA GC420D", "port1"),
        (0, "", "Microsoft Print to PDF", "port2"),
        (0, "", "HP LaserJet", "port3"),
    ]

    result = printer_service.list_printers()

    assert result == ["ZEBRA GC420D", "Microsoft Print to PDF", "HP LaserJet"]
    fake_win32print.EnumPrinters.assert_called_once_with(
        fake_win32print.PRINTER_ENUM_LOCAL | fake_win32print.PRINTER_ENUM_CONNECTIONS
    )


def test_list_printers_empty_when_unavailable(no_win32):
    assert printer_service.list_printers() == []


# ---------------------------------------------------------------------------
# find_zebra_printer
# ---------------------------------------------------------------------------


def test_find_zebra_printer_case_insensitive(fake_win32print):
    fake_win32print.EnumPrinters.return_value = [
        (0, "", "HP LaserJet", "port1"),
        (0, "", "ZEBRA GC420D", "port2"),
    ]

    assert printer_service.find_zebra_printer() == "ZEBRA GC420D"


def test_find_zebra_printer_none_found(fake_win32print):
    fake_win32print.EnumPrinters.return_value = [
        (0, "", "HP LaserJet", "port1"),
        (0, "", "Microsoft Print to PDF", "port2"),
    ]

    assert printer_service.find_zebra_printer() is None


def test_find_zebra_printer_matches_zdesigner_default_driver(fake_win32print):
    fake_win32print.EnumPrinters.return_value = [
        (0, "", "HP LaserJet", "port1"),
        (0, "", "ZDesigner GC420d", "port2"),
    ]

    assert printer_service.find_zebra_printer() == "ZDesigner GC420d"


def test_find_zebra_printer_matches_model_only_name(fake_win32print):
    fake_win32print.EnumPrinters.return_value = [
        (0, "", "GC420d", "port1"),
    ]

    assert printer_service.find_zebra_printer() == "GC420d"


# ---------------------------------------------------------------------------
# get_default_printer
# ---------------------------------------------------------------------------


def test_get_default_printer_returns_name(fake_win32print):
    fake_win32print.GetDefaultPrinter.return_value = "Zebra GC420D"

    assert printer_service.get_default_printer() == "Zebra GC420D"


def test_get_default_printer_none_on_error(fake_win32print):
    fake_win32print.GetDefaultPrinter.side_effect = RuntimeError("nope")

    assert printer_service.get_default_printer() is None


def test_get_default_printer_none_when_unavailable(no_win32):
    assert printer_service.get_default_printer() is None


# ---------------------------------------------------------------------------
# send_raw_zpl
# ---------------------------------------------------------------------------


def test_send_raw_zpl_calls_api_sequence(fake_win32print):
    fake_win32print.OpenPrinter.return_value = "HPRINTER"
    zpl = b"^XA^FDhi^FS^XZ"

    printer_service.send_raw_zpl("Zebra GC420D", zpl, doc_name="doc")

    fake_win32print.OpenPrinter.assert_called_once_with("Zebra GC420D")
    fake_win32print.StartDocPrinter.assert_called_once_with(
        "HPRINTER", 1, ("doc", None, "RAW")
    )
    fake_win32print.StartPagePrinter.assert_called_once_with("HPRINTER")
    fake_win32print.WritePrinter.assert_called_once_with("HPRINTER", zpl)
    fake_win32print.EndPagePrinter.assert_called_once_with("HPRINTER")
    fake_win32print.EndDocPrinter.assert_called_once_with("HPRINTER")
    fake_win32print.ClosePrinter.assert_called_once_with("HPRINTER")

    # Enforce ordering: Open -> StartDoc -> StartPage -> Write -> EndPage ->
    # EndDoc -> Close
    method_order = [
        c[0]
        for c in fake_win32print.mock_calls
        if c[0]
        in {
            "OpenPrinter",
            "StartDocPrinter",
            "StartPagePrinter",
            "WritePrinter",
            "EndPagePrinter",
            "EndDocPrinter",
            "ClosePrinter",
        }
    ]
    assert method_order == [
        "OpenPrinter",
        "StartDocPrinter",
        "StartPagePrinter",
        "WritePrinter",
        "EndPagePrinter",
        "EndDocPrinter",
        "ClosePrinter",
    ]


def test_send_raw_zpl_closes_handle_on_error(fake_win32print):
    fake_win32print.OpenPrinter.return_value = "HPRINTER"
    fake_win32print.WritePrinter.side_effect = OSError("write failed")

    with pytest.raises(OSError, match="write failed"):
        printer_service.send_raw_zpl("Zebra GC420D", b"data")

    fake_win32print.ClosePrinter.assert_called_once_with("HPRINTER")
    fake_win32print.EndDocPrinter.assert_called_once_with("HPRINTER")


def test_send_raw_zpl_raises_when_unavailable(no_win32):
    with pytest.raises(PrinterServiceUnavailable):
        printer_service.send_raw_zpl("Zebra", b"data")


# ---------------------------------------------------------------------------
# print_via_shellexecute
# ---------------------------------------------------------------------------


def test_print_via_shellexecute_calls_api(fake_win32api):
    printer_service.print_via_shellexecute("Zebra GC420D", "C:/tmp/label.pdf")

    fake_win32api.ShellExecute.assert_called_once_with(
        0, "printto", "C:/tmp/label.pdf", '"Zebra GC420D"', ".", 0
    )


def test_print_via_shellexecute_raises_when_unavailable(no_win32):
    with pytest.raises(PrinterServiceUnavailable):
        printer_service.print_via_shellexecute("Zebra", "C:/tmp/label.pdf")


# ---------------------------------------------------------------------------
# print_via_print_verb / set_default_printer
# ---------------------------------------------------------------------------


def test_print_via_print_verb_uses_plain_print_verb(fake_win32api):
    printer_service.print_via_print_verb("C:/tmp/label.ljd")

    fake_win32api.ShellExecute.assert_called_once_with(
        0, "print", "C:/tmp/label.ljd", None, ".", 0
    )


def test_print_via_print_verb_raises_when_unavailable(no_win32):
    with pytest.raises(PrinterServiceUnavailable):
        printer_service.print_via_print_verb("C:/tmp/label.ljd")


def test_set_default_printer_delegates(fake_win32print):
    printer_service.set_default_printer("ZDesigner GC420d (EPL)")

    fake_win32print.SetDefaultPrinter.assert_called_once_with(
        "ZDesigner GC420d (EPL)"
    )


def test_set_default_printer_raises_when_unavailable(no_win32):
    with pytest.raises(PrinterServiceUnavailable):
        printer_service.set_default_printer("Zebra")


# ---------------------------------------------------------------------------
# Default-printer crash-recovery marker
# ---------------------------------------------------------------------------


@pytest.fixture()
def marker_path(tmp_path, monkeypatch):
    path = str(tmp_path / "default_printer_backup.txt")
    monkeypatch.setattr(printer_service, "DEFAULT_PRINTER_MARKER", path)
    return path


def test_marker_round_trip(marker_path):
    printer_service.save_default_printer_marker("HP LaserJet")
    with open(marker_path, encoding="utf-8") as fh:
        assert fh.read() == "HP LaserJet"

    printer_service.clear_default_printer_marker()
    assert not os.path.exists(marker_path)


def test_clear_marker_is_noop_when_absent(marker_path):
    printer_service.clear_default_printer_marker()  # must not raise


def test_restore_if_marked_restores_and_clears(marker_path, fake_win32print):
    printer_service.save_default_printer_marker("HP LaserJet")

    restored = printer_service.restore_default_printer_if_marked()

    assert restored == "HP LaserJet"
    fake_win32print.SetDefaultPrinter.assert_called_once_with("HP LaserJet")
    assert not os.path.exists(marker_path)


def test_restore_if_marked_noop_without_marker(marker_path, fake_win32print):
    assert printer_service.restore_default_printer_if_marked() is None
    fake_win32print.SetDefaultPrinter.assert_not_called()


def test_restore_if_marked_keeps_marker_on_failure(
    marker_path, fake_win32print
):
    printer_service.save_default_printer_marker("HP LaserJet")
    fake_win32print.SetDefaultPrinter.side_effect = OSError("spooler down")

    assert printer_service.restore_default_printer_if_marked() is None
    # Marker survives so the next launch can retry.
    assert os.path.exists(marker_path)


def test_restore_if_marked_empty_marker_just_clears(
    marker_path, fake_win32print
):
    printer_service.save_default_printer_marker("")

    assert printer_service.restore_default_printer_if_marked() is None
    fake_win32print.SetDefaultPrinter.assert_not_called()
    assert not os.path.exists(marker_path)


# ---------------------------------------------------------------------------
# clear_print_queue
# ---------------------------------------------------------------------------


def test_clear_print_queue_deletes_all_jobs(fake_win32print):
    fake_win32print.OpenPrinter.return_value = "HPRINTER"
    fake_win32print.EnumJobs.return_value = [
        {"JobId": 11},
        {"JobId": 22},
        {"JobId": 33},
    ]

    deleted = printer_service.clear_print_queue("Zebra GC420D")

    assert deleted == 3
    fake_win32print.EnumJobs.assert_called_once_with("HPRINTER", 0, 999, 1)
    assert fake_win32print.SetJob.call_args_list == [
        call("HPRINTER", 11, 0, None, fake_win32print.JOB_CONTROL_DELETE),
        call("HPRINTER", 22, 0, None, fake_win32print.JOB_CONTROL_DELETE),
        call("HPRINTER", 33, 0, None, fake_win32print.JOB_CONTROL_DELETE),
    ]
    fake_win32print.ClosePrinter.assert_called_once_with("HPRINTER")


def test_clear_print_queue_returns_zero_when_unavailable(no_win32):
    assert printer_service.clear_print_queue("Zebra") == 0


def test_clear_print_queue_permission_error_reraised(fake_win32print):
    fake_win32print.OpenPrinter.return_value = "HPRINTER"
    fake_win32print.EnumJobs.return_value = [{"JobId": 1}]
    fake_win32print.SetJob.side_effect = PermissionError("denied")

    with pytest.raises(PermissionError, match="Access denied"):
        printer_service.clear_print_queue("Zebra GC420D")

    fake_win32print.ClosePrinter.assert_called_once_with("HPRINTER")


def test_clear_print_queue_translates_pywintypes_access_denied(
    fake_win32print,
):
    """The REAL exception pywin32 raises for access-denied is
    ``pywintypes.error`` with winerror 5 — not Python's PermissionError.
    This is what an unelevated SetJob actually produces, and it must reach
    the settings dialog as PermissionError so the "run as administrator"
    guidance fires."""
    pywintypes = pytest.importorskip("pywintypes")

    fake_win32print.OpenPrinter.return_value = "HPRINTER"
    fake_win32print.EnumJobs.return_value = [{"JobId": 1}]
    fake_win32print.SetJob.side_effect = pywintypes.error(
        5, "SetJob", "Access is denied."
    )

    with pytest.raises(PermissionError, match="administrator"):
        printer_service.clear_print_queue("Zebra GC420D")

    fake_win32print.ClosePrinter.assert_called_once_with("HPRINTER")


def test_clear_print_queue_other_pywintypes_errors_pass_through(
    fake_win32print,
):
    pywintypes = pytest.importorskip("pywintypes")

    fake_win32print.OpenPrinter.return_value = "HPRINTER"
    fake_win32print.EnumJobs.return_value = [{"JobId": 1}]
    fake_win32print.SetJob.side_effect = pywintypes.error(
        1722, "SetJob", "The RPC server is unavailable."
    )

    with pytest.raises(pywintypes.error):
        printer_service.clear_print_queue("Zebra GC420D")


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


def test_printer_service_unavailable_is_runtime_error():
    assert issubclass(PrinterServiceUnavailable, RuntimeError)
    with pytest.raises(RuntimeError):
        raise PrinterServiceUnavailable("nope")
