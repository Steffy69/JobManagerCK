"""Tests for source/printer_service.py."""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, call

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

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


# ---------------------------------------------------------------------------
# is_printer_available
# ---------------------------------------------------------------------------


def test_is_printer_available_true(fake_win32print):
    fake_win32print.EnumPrinters.return_value = [
        (0, "", "Zebra GC420D", "port1"),
    ]

    assert printer_service.is_printer_available("Zebra GC420D") is True


def test_is_printer_available_false_on_exception(fake_win32print):
    fake_win32print.EnumPrinters.side_effect = OSError("boom")

    assert printer_service.is_printer_available("Zebra GC420D") is False


def test_is_printer_available_false_when_missing(fake_win32print):
    fake_win32print.EnumPrinters.return_value = [
        (0, "", "HP LaserJet", "port1"),
    ]

    assert printer_service.is_printer_available("Zebra GC420D") is False


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


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


def test_printer_service_unavailable_is_runtime_error():
    assert issubclass(PrinterServiceUnavailable, RuntimeError)
    with pytest.raises(RuntimeError):
        raise PrinterServiceUnavailable("nope")
