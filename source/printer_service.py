"""Thin service wrapper around Windows print APIs (`win32print`, `win32api`).

Isolates the UI and tests from direct win32 calls so they can be mocked, and so
the app can still import on dev machines without pywin32 installed.

When pywin32 is unavailable, the module exposes ``HAS_WIN32 = False`` and each
function either returns a sensible default (see individual docstrings) or
raises :class:`PrinterServiceUnavailable`.
"""

from __future__ import annotations

import logging
import time
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


_ZEBRA_NAME_HINTS: tuple[str, ...] = (
    "zebra",
    "zdesigner",
    "gc420",
    "gk420",
    "gx420",
    "gt800",
    "zd410",
    "zd420",
    "zd421",
    "zd500",
    "zd620",
    "zt230",
    "zt410",
    "zt420",
)


def is_zebra_name(name: str) -> bool:
    """Return True if ``name`` matches a known Zebra driver hint.

    Matches "zebra" (the friendly driver name), "zdesigner" (Zebra's default
    Windows driver prefix), and common GC/GK/GX/ZD/ZT model families. All
    matches are case-insensitive substring checks.

    Exposed separately from :func:`find_zebra_printer` so callers that have
    already enumerated the spooler — such as the status widget's poll — can
    reuse the same matching rules without paying for a second enumeration.
    """
    lowered = name.lower()
    return any(hint in lowered for hint in _ZEBRA_NAME_HINTS)


def match_zebra_printer(names: list[str]) -> Optional[str]:
    """Return the first name in ``names`` that looks like a Zebra, else None."""
    return next((name for name in names if is_zebra_name(name)), None)


def find_zebra_printer() -> Optional[str]:
    """Return the first installed printer matching a known Zebra driver hint."""
    return match_zebra_printer(list_printers())


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


def print_via_default_swap(
    printer_name: str,
    file_path: str,
    settle_seconds: float = 1.5,
) -> None:
    """Print ``file_path`` via a temporary Windows-default-printer swap.

    Some third-party print handlers (e.g. labelMaker for ``.ljd`` files)
    reject the ``printto`` verb or mis-parse printer names that contain
    parentheses or other special characters. This fallback sidesteps the
    issue by:

    1. Saving the current Windows default printer.
    2. Setting the default to ``printer_name``.
    3. Invoking the plain ``print`` verb via ``ShellExecute`` (no printer arg).
    4. Sleeping ``settle_seconds`` so the handler has time to read the new
       default before we restore.
    5. Restoring the previous default in a ``finally`` block — the user's
       default is never left pointing at the label printer on error.

    Raises :class:`PrinterServiceUnavailable` if pywin32 is unavailable.
    """
    if not HAS_WIN32:
        raise PrinterServiceUnavailable(
            "pywin32 is not installed; cannot swap default printer"
        )

    previous_default: Optional[str] = None
    try:
        previous_default = win32print.GetDefaultPrinter()
    except Exception:  # noqa: BLE001 - non-fatal; we just won't restore
        logger.exception("GetDefaultPrinter failed while preparing swap")

    win32print.SetDefaultPrinter(printer_name)
    try:
        win32api.ShellExecute(0, "print", file_path, None, ".", 0)
        time.sleep(max(0.0, settle_seconds))
    finally:
        if previous_default and previous_default != printer_name:
            try:
                win32print.SetDefaultPrinter(previous_default)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Failed to restore default printer to %r", previous_default
                )


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
