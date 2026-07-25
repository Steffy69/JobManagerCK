"""Shared helpers for the transfer worker threads.

The workers report failures to a workshop operator, not a developer — raw
``[WinError 59]`` text gives no verb to act on. ``describe_failure`` maps the
common Windows error families to plain instructions; the raw exception is
always preserved in the log by the caller's ``logger.exception``.
"""

from __future__ import annotations

import os

# Windows error codes that mean "the network path went away".
_NETWORK_WINERRORS = {
    53,  # ERROR_BAD_NETPATH
    59,  # ERROR_UNEXP_NET_ERR
    64,  # ERROR_NETNAME_DELETED
    121,  # ERROR_SEM_TIMEOUT
    1231,  # ERROR_NETWORK_UNREACHABLE
}

_DISK_FULL_WINERRORS = {39, 112}  # ERROR_DISK_FULL, ERROR_DISK_FULL (copy)


def describe_failure(exc: BaseException) -> str:
    """Return an operator-actionable description of *exc*."""
    winerror = getattr(exc, "winerror", None)

    if winerror in _NETWORK_WINERRORS:
        return (
            "Lost the connection to the network drive. Check the S: drive "
            "is available, then try again."
        )
    if isinstance(exc, PermissionError) or winerror == 5:
        return (
            "Access was denied — the file may be open in another program. "
            "Close it and try again."
        )
    if winerror in _DISK_FULL_WINERRORS or getattr(exc, "errno", None) == 28:
        return "The destination drive is full. Free up space and try again."
    if isinstance(exc, FileNotFoundError):
        return "A file has moved or been deleted since the job was scanned."
    return str(exc)


def files_identical(src: str, dst: str) -> bool:
    """Cheap same-file check: size equal and mtime within 2 seconds.

    ``shutil.copy2`` preserves timestamps, so an unchanged file copied
    earlier matches its source. The 2-second tolerance covers FAT-style
    mtime granularity. Any stat failure counts as "not identical" so the
    caller just copies.
    """
    try:
        src_stat = os.stat(src)
        dst_stat = os.stat(dst)
    except OSError:
        return False
    return (
        src_stat.st_size == dst_stat.st_size
        and abs(src_stat.st_mtime - dst_stat.st_mtime) < 2.0
    )
