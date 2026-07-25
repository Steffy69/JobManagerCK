"""Thin service wrapper around Windows print APIs (`win32print`, `win32api`).

Isolates the UI and tests from direct win32 calls so they can be mocked, and so
the app can still import on dev machines without pywin32 installed.

When pywin32 is unavailable, the module exposes ``HAS_WIN32 = False`` and each
function either returns a sensible default (see individual docstrings) or
raises :class:`PrinterServiceUnavailable`.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import pywintypes  # type: ignore[import-not-found]
    import win32api  # type: ignore[import-not-found]
    import win32print  # type: ignore[import-not-found]

    HAS_WIN32 = True
except ImportError:  # pragma: no cover - exercised via monkeypatching in tests
    pywintypes = None  # type: ignore[assignment]
    win32api = None  # type: ignore[assignment]
    win32print = None  # type: ignore[assignment]
    HAS_WIN32 = False

#: Marker file recording the user's default printer while a print run has it
#: temporarily swapped to the Zebra. If the app dies mid-run the marker
#: survives, and :func:`restore_default_printer_if_marked` puts things back
#: on the next launch.
DEFAULT_PRINTER_MARKER = os.path.join(
    os.path.expanduser("~"), ".jobmanager", "default_printer_backup.txt"
)

_ERROR_ACCESS_DENIED = 5


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


def print_via_print_verb(file_path: str) -> None:
    """Print ``file_path`` via the plain Windows ``print`` verb.

    Sends the file to the SYSTEM DEFAULT printer. Used by the label printer's
    fallback path: some third-party print handlers (e.g. labelMaker for
    ``.ljd`` files) reject the ``printto`` verb or mis-parse printer names
    containing parentheses, so the run temporarily makes the Zebra the
    default and prints with this verb instead. The swap/restore lifecycle is
    owned by the caller — see :class:`label_printer.LabelPrinterThread` —
    which swaps ONCE per run rather than per label, so the handler can read
    the default at any point during the run and still land on the Zebra.

    Raises :class:`PrinterServiceUnavailable` if pywin32 is unavailable.
    """
    if not HAS_WIN32:
        raise PrinterServiceUnavailable(
            "pywin32 is not installed; cannot ShellExecute print"
        )

    win32api.ShellExecute(0, "print", file_path, None, ".", 0)


def set_default_printer(printer_name: str) -> None:
    """Set the Windows default printer.

    Raises :class:`PrinterServiceUnavailable` if pywin32 is unavailable;
    other errors propagate so the caller can react (a failed RESTORE of the
    user's default must be surfaced, not swallowed).
    """
    if not HAS_WIN32:
        raise PrinterServiceUnavailable(
            "pywin32 is not installed; cannot set default printer"
        )
    win32print.SetDefaultPrinter(printer_name)


def save_default_printer_marker(previous_default: str) -> None:
    """Persist the pre-swap default printer name to the crash-recovery marker.

    Written immediately BEFORE the swap so that if the process dies while the
    Zebra is the system default, the next launch can put the user's real
    default back via :func:`restore_default_printer_if_marked`. An empty
    string records "there was no default to restore".
    """
    try:
        os.makedirs(os.path.dirname(DEFAULT_PRINTER_MARKER), exist_ok=True)
        with open(DEFAULT_PRINTER_MARKER, "w", encoding="utf-8") as fh:
            fh.write(previous_default)
    except OSError:
        # The marker is belt-and-braces; a failed write must not stop a
        # print run. The in-process finally still restores on a clean exit.
        logger.exception("Could not write default-printer marker")


def clear_default_printer_marker() -> None:
    """Remove the crash-recovery marker after a successful restore."""
    try:
        os.remove(DEFAULT_PRINTER_MARKER)
    except FileNotFoundError:
        pass
    except OSError:
        logger.exception("Could not remove default-printer marker")


def restore_default_printer_if_marked() -> Optional[str]:
    """Undo a default-printer swap that a crashed run left behind.

    Called once at app startup. If the marker file exists, a previous run
    died between swapping the default to the Zebra and restoring it. Restores
    the recorded default (when one was recorded) and removes the marker.

    Returns the restored printer name, or None if there was nothing to do.
    """
    try:
        with open(DEFAULT_PRINTER_MARKER, "r", encoding="utf-8") as fh:
            previous_default = fh.read().strip()
    except FileNotFoundError:
        return None
    except OSError:
        logger.exception("Could not read default-printer marker")
        return None

    restored: Optional[str] = None
    if previous_default and HAS_WIN32:
        try:
            win32print.SetDefaultPrinter(previous_default)
            restored = previous_default
            logger.info(
                "Restored default printer to %r after an interrupted print run",
                previous_default,
            )
        except Exception:  # noqa: BLE001 - startup must never crash on this
            logger.exception(
                "Could not restore default printer to %r", previous_default
            )
            return None  # keep the marker for the next attempt

    clear_default_printer_marker()
    return restored


def _is_access_denied(exc: BaseException) -> bool:
    """Return True if *exc* is a win32 access-denied error.

    pywin32 raises ``pywintypes.error`` — which is NOT a subclass of Python's
    ``OSError``/``PermissionError`` — with ``winerror == 5`` for
    ERROR_ACCESS_DENIED. Catching ``PermissionError`` around a win32 call
    therefore never fires; this helper is the one place that knows the
    translation.
    """
    if isinstance(exc, PermissionError):
        return True
    if pywintypes is not None and isinstance(exc, pywintypes.error):
        return exc.winerror == _ERROR_ACCESS_DENIED
    return False


def clear_print_queue(printer_name: str) -> int:
    """Delete all queued jobs for ``printer_name``. Returns count deleted.

    Returns 0 if pywin32 is unavailable. Access-denied errors from any of the
    underlying win32 calls are re-raised as :class:`PermissionError` with a
    user-friendly message, so the settings dialog's "run as administrator"
    guidance fires for the real pywin32 exception type.
    """
    if not HAS_WIN32:
        return 0

    try:
        hPrinter = win32print.OpenPrinter(printer_name)
    except Exception as exc:  # noqa: BLE001 - translate access-denied only
        if _is_access_denied(exc):
            raise PermissionError(
                f"Access denied opening printer {printer_name!r}. "
                "Try running JobManager as administrator."
            ) from exc
        raise

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
            except Exception as exc:  # noqa: BLE001 - translate access-denied
                if _is_access_denied(exc):
                    raise PermissionError(
                        f"Access denied clearing print queue for "
                        f"{printer_name!r}. "
                        "Try running JobManager as administrator."
                    ) from exc
                raise
    finally:
        win32print.ClosePrinter(hPrinter)
    return deleted
