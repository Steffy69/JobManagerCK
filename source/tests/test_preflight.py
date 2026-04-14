"""Unit tests for source/preflight.py."""

from __future__ import annotations

import dataclasses
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from preflight import (  # noqa: E402
    PreflightResult,
    check_cadcode_free_space,
    check_printer_available,
    check_s_drive_reachable,
    check_usb_free_space,
    estimate_nc_files_size,
)


def _usage(free_bytes: int) -> SimpleNamespace:
    return SimpleNamespace(total=free_bytes * 2, used=free_bytes, free=free_bytes)


def test_preflight_result_success() -> None:
    result = PreflightResult.success()
    assert result.ok is True
    assert result.title == ""
    assert result.message == ""


def test_preflight_result_failure() -> None:
    result = PreflightResult.failure("Boom", "Something went wrong")
    assert result.ok is False
    assert result.title == "Boom"
    assert result.message == "Something went wrong"


def test_preflight_result_frozen() -> None:
    result = PreflightResult.success()
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.ok = False  # type: ignore[misc]


def test_check_s_drive_reachable_exists(tmp_path: Path) -> None:
    result = check_s_drive_reachable(str(tmp_path))
    assert result.ok is True


def test_check_s_drive_reachable_missing(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist"
    result = check_s_drive_reachable(str(missing))
    assert result.ok is False
    assert "S Drive" in result.title


def test_check_cadcode_free_space_ok(tmp_path: Path) -> None:
    plenty = 5000 * 1024 * 1024  # 5000 MB
    with patch("preflight.shutil.disk_usage", return_value=_usage(plenty)):
        result = check_cadcode_free_space(str(tmp_path), min_mb=500)
    assert result.ok is True


def test_check_cadcode_free_space_low(tmp_path: Path) -> None:
    low = 100 * 1024 * 1024  # 100 MB
    with patch("preflight.shutil.disk_usage", return_value=_usage(low)):
        result = check_cadcode_free_space(str(tmp_path), min_mb=500)
    assert result.ok is False
    assert result.title == "Low Disk Space"
    assert "100" in result.message
    assert "500" in result.message


def test_check_cadcode_free_space_missing_folder(tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    result = check_cadcode_free_space(str(missing), min_mb=500)
    assert result.ok is False
    assert result.title == "CADCode Folder Missing"


def test_check_usb_free_space_ok(tmp_path: Path) -> None:
    plenty = 2000 * 1024 * 1024
    with patch("preflight.shutil.disk_usage", return_value=_usage(plenty)):
        result = check_usb_free_space(str(tmp_path), required_mb=100)
    assert result.ok is True


def test_check_usb_free_space_insufficient(tmp_path: Path) -> None:
    tiny = 50 * 1024 * 1024
    with patch("preflight.shutil.disk_usage", return_value=_usage(tiny)):
        result = check_usb_free_space(str(tmp_path), required_mb=500)
    assert result.ok is False
    assert result.title == "Insufficient USB Space"
    assert "50" in result.message
    assert "500" in result.message


def test_check_usb_missing_path(tmp_path: Path) -> None:
    missing = tmp_path / "no_usb"
    result = check_usb_free_space(str(missing), required_mb=100)
    assert result.ok is False
    assert result.title == "Insufficient USB Space"


def test_estimate_nc_files_size(tmp_path: Path) -> None:
    f1 = tmp_path / "a.nc"
    f2 = tmp_path / "b.nc"
    f1.write_bytes(b"A" * 100)
    f2.write_bytes(b"B" * 250)
    total = estimate_nc_files_size((str(f1), str(f2)))
    assert total == 350


def test_estimate_nc_files_size_skips_missing(tmp_path: Path) -> None:
    real = tmp_path / "real.nc"
    real.write_bytes(b"X" * 42)
    ghost = tmp_path / "ghost.nc"
    total = estimate_nc_files_size((str(real), str(ghost)))
    assert total == 42


def test_check_printer_available_without_win32print() -> None:
    saved_win32print = sys.modules.pop("win32print", None)
    sys.modules["win32print"] = None  # type: ignore[assignment]
    try:
        result = check_printer_available("")
    finally:
        if saved_win32print is not None:
            sys.modules["win32print"] = saved_win32print
        else:
            sys.modules.pop("win32print", None)
    assert result.ok is True
