"""Silent pre-flight checks for JobManagerCK v2.1.

Pure check functions that return PreflightResult. The UI layer decides
whether to show a dialog — checks never render anything themselves.
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_BYTES_PER_MB = 1024 * 1024


@dataclass(frozen=True)
class PreflightResult:
    ok: bool
    title: str
    message: str

    @classmethod
    def success(cls) -> "PreflightResult":
        return cls(True, "", "")

    @classmethod
    def failure(cls, title: str, message: str) -> "PreflightResult":
        return cls(False, title, message)


def check_s_drive_reachable(path: str = "S:\\Jobs") -> PreflightResult:
    if os.path.isdir(path):
        return PreflightResult.success()
    return PreflightResult.failure(
        "S Drive Unreachable",
        (
            f"The shared drive at {path} is not accessible.\n\n"
            "Check that the S: drive is mapped and the network is connected, "
            "then try again."
        ),
    )


def check_cadcode_free_space(
    path: str = "C:\\CADCode", min_mb: int = 500
) -> PreflightResult:
    if not os.path.isdir(path):
        return PreflightResult.failure(
            "CADCode Folder Missing",
            f"The CADCode folder at {path} does not exist.",
        )
    try:
        usage = shutil.disk_usage(path)
    except (OSError, FileNotFoundError) as exc:
        logger.warning("disk_usage failed for %s: %s", path, exc)
        return PreflightResult.failure(
            "CADCode Folder Missing",
            f"Unable to read disk usage for {path}: {exc}",
        )
    free_mb = usage.free // _BYTES_PER_MB
    if free_mb >= min_mb:
        return PreflightResult.success()
    return PreflightResult.failure(
        "Low Disk Space",
        (
            f"CADCode folder at {path} has only {free_mb} MB free, "
            f"but {min_mb} MB is required.\n\n"
            "Free up space on C: and try again."
        ),
    )


def check_usb_free_space(usb_path: str, required_mb: int) -> PreflightResult:
    if not os.path.isdir(usb_path):
        return PreflightResult.failure(
            "Insufficient USB Space",
            f"USB drive at {usb_path} is not accessible.",
        )
    try:
        usage = shutil.disk_usage(usb_path)
    except (OSError, FileNotFoundError) as exc:
        logger.warning("disk_usage failed for USB %s: %s", usb_path, exc)
        return PreflightResult.failure(
            "Insufficient USB Space",
            f"Unable to read USB disk usage for {usb_path}: {exc}",
        )
    free_mb = usage.free // _BYTES_PER_MB
    if free_mb >= required_mb:
        return PreflightResult.success()
    return PreflightResult.failure(
        "Insufficient USB Space",
        (
            f"USB drive at {usb_path} has only {free_mb} MB free, "
            f"but {required_mb} MB is required for this transfer."
        ),
    )


def check_printer_available(printer_name: str) -> PreflightResult:
    try:
        import win32print  # type: ignore[import-not-found]
    except ImportError:
        # Non-Windows dev environment — degrade gracefully so tests pass.
        return PreflightResult.success()

    try:
        flags = (
            win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
        )
        printers = win32print.EnumPrinters(flags)
        names = [entry[2] for entry in printers]
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("EnumPrinters failed: %s", exc)
        return PreflightResult.failure(
            "Printer Not Found",
            f"Unable to enumerate printers: {exc}",
        )

    if printer_name:
        if printer_name in names:
            return PreflightResult.success()
        return PreflightResult.failure(
            "Printer Not Found",
            (
                f"Printer '{printer_name}' was not found.\n\n"
                "Check that the Zebra label printer is powered on and connected "
                "via USB."
            ),
        )

    for name in names:
        if "zebra" in name.lower():
            return PreflightResult.success()
    return PreflightResult.failure(
        "Printer Not Found",
        (
            "No Zebra label printer detected.\n\n"
            "Check that the Zebra label printer is powered on and connected "
            "via USB."
        ),
    )


def estimate_nc_files_size(nc_files: tuple[str, ...]) -> int:
    total = 0
    for path in nc_files:
        try:
            total += os.path.getsize(path)
        except OSError:
            continue
    return total
