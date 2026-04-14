"""Thin service wrapper around Windows print APIs (`win32print`, `win32api`).

Isolates the UI and tests from direct win32 calls so they can be mocked, and so
the app can still import on dev machines without pywin32 installed.

When pywin32 is unavailable, the module exposes ``HAS_WIN32 = False`` and each
function either returns a sensible default (see individual docstrings) or
raises :class:`PrinterServiceUnavailable`.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import win32api  # type: ignore[import-not-found]
    import win32print  # type: ignore[import-not-found]

    HAS_WIN32 = True
except ImportError:  # pragma: no cover - exercised via monkeypatching in tests
    win32api = None  # type: ignore[assignment]
    win32print = None  # type: ignore[assignment]
    HAS_WIN32 = False


class PrinterServiceUnavailable(RuntimeError):
    """Raised when a printer operation is attempted without pywin32 available."""


def list_printers() -> list[str]:
    """Return names of all local and connected printers.

    Returns an empty list (and logs a warning) if pywin32 is unavailable.
    """
    if not HAS_WIN32:
        logger.warning("list_printers called but pywin32 is unavailable")
        return []

    flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    printers = win32print.EnumPrinters(flags)
    return [entry[2] for entry in printers]


def find_zebra_printer() -> Optional[str]:
    """Return the first printer name containing 'Zebra' (case-insensitive)."""
    for name in list_printers():
        if "zebra" in name.lower():
            return name
    return None


def is_printer_available(printer_name: str) -> bool:
    """Return True if ``printer_name`` is present in the system printer list.

    Never raises — any exception is caught and treated as unavailable.
    """
    try:
        return printer_name in list_printers()
    except Exception:  # noqa: BLE001 - contract: must never raise
        logger.exception("is_printer_available failed for %r", printer_name)
        return False


def get_default_printer() -> Optional[str]:
    """Return the Windows default printer name, or None on error/unavailable."""
    if not HAS_WIN32:
        return None
    try:
        return win32print.GetDefaultPrinter()
    except Exception:  # noqa: BLE001
        logger.exception("GetDefaultPrinter failed")
        return None


def send_raw_zpl(
    printer_name: str,
    zpl_bytes: bytes,
    doc_name: str = "JobManagerCK ZPL",
) -> None:
    """Send raw ZPL bytes directly to the named printer.

    Raises :class:`PrinterServiceUnavailable` if pywin32 is unavailable. All
    other errors propagate unchanged so the caller can surface them to the user.
    """
    if not HAS_WIN32:
        raise PrinterServiceUnavailable(
            "pywin32 is not installed; cannot send raw ZPL"
        )

    hPrinter = win32print.OpenPrinter(printer_name)
    try:
        win32print.StartDocPrinter(hPrinter, 1, (doc_name, None, "RAW"))
        try:
            win32print.StartPagePrinter(hPrinter)
            win32print.WritePrinter(hPrinter, zpl_bytes)
            win32print.EndPagePrinter(hPrinter)
        finally:
            win32print.EndDocPrinter(hPrinter)
    finally:
        win32print.ClosePrinter(hPrinter)


def print_via_shellexecute(printer_name: str, file_path: str) -> None:
    """Print ``file_path`` to ``printer_name`` via the Windows ``printto`` verb.

    This routes the file's default-app print action to the named printer
    without touching the system default printer.

    Raises :class:`PrinterServiceUnavailable` if pywin32 is unavailable.
    """
    if not HAS_WIN32:
        raise PrinterServiceUnavailable(
            "pywin32 is not installed; cannot ShellExecute print"
        )

    win32api.ShellExecute(0, "printto", file_path, f'"{printer_name}"', ".", 0)


def clear_print_queue(printer_name: str) -> int:
    """Delete all queued jobs for ``printer_name``. Returns count deleted.

    Returns 0 if pywin32 is unavailable. Permission errors are re-raised as
    :class:`PermissionError` with a user-friendly message.
    """
    if not HAS_WIN32:
        return 0

    hPrinter = win32print.OpenPrinter(printer_name)
    deleted = 0
    try:
        jobs = win32print.EnumJobs(hPrinter, 0, 999, 1)
        for job in jobs:
            job_id = job["JobId"] if isinstance(job, dict) else job[0]
            try:
                win32print.SetJob(
                    hPrinter, job_id, 0, None, win32print.JOB_CONTROL_DELETE
                )
                deleted += 1
            except PermissionError as exc:
                raise PermissionError(
                    f"Access denied clearing print queue for {printer_name!r}. "
                    "Try running JobManager as administrator."
                ) from exc
    finally:
        win32print.ClosePrinter(hPrinter)
    return deleted
