"""Tests for source/usb_transfer.py.

Pins the duplicate-basename hard stop (a silent overwrite would send the
wrong program to the CNC machine) and per-file failure reporting.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PyQt5.QtWidgets")

from usb_transfer import USBTransferThread, find_duplicate_basenames  # noqa: E402


@pytest.fixture()
def _qapp():
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def _make_nc(tmp_path, rel_paths):
    paths = []
    for rel in rel_paths:
        p = tmp_path / "src" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"nc:{rel}")
        paths.append(str(p))
    return tuple(paths)


def _run(tmp_path, nc_files):
    target = tmp_path / "usb"
    target.mkdir(exist_ok=True)
    thread = USBTransferThread(nc_files=nc_files, target_drive=str(target))
    finished: list[tuple] = []
    thread.finished.connect(lambda *a: finished.append(a))
    thread.run()
    return target, finished


# ---------------------------------------------------------------------------
# find_duplicate_basenames
# ---------------------------------------------------------------------------


def test_no_duplicates_for_distinct_names() -> None:
    assert find_duplicate_basenames(("/a/one.nc", "/a/two.nc")) == {}


def test_detects_same_name_in_different_folders() -> None:
    dupes = find_duplicate_basenames(
        ("/job/sub1/PART.nc", "/job/sub2/PART.nc", "/job/other.nc")
    )
    assert list(dupes) == ["PART.nc"]
    assert len(dupes["PART.nc"]) == 2


# ---------------------------------------------------------------------------
# USBTransferThread.run
# ---------------------------------------------------------------------------


def test_copies_all_files(_qapp, tmp_path) -> None:
    nc = _make_nc(tmp_path, ["one.nc", "two.nc"])

    target, finished = _run(tmp_path, nc)

    assert finished[0][0] is True
    assert sorted(p.name for p in target.iterdir()) == ["one.nc", "two.nc"]


def test_duplicate_basenames_hard_stop(_qapp, tmp_path) -> None:
    nc = _make_nc(tmp_path, ["sub1/PART.nc", "sub2/PART.nc"])

    target, finished = _run(tmp_path, nc)

    success, message = finished[0]
    assert success is False
    assert "PART.nc" in message
    assert "overwrite" in message
    # Nothing was copied — the stop happens before any file moves.
    assert list(target.iterdir()) == []


def test_missing_source_reported_per_file(_qapp, tmp_path) -> None:
    nc = _make_nc(tmp_path, ["one.nc", "two.nc"])
    os.unlink(nc[0])

    target, finished = _run(tmp_path, nc)

    success, message = finished[0]
    assert success is False
    assert "one.nc" in message
    assert "Copied 1 of 2" in message
    # The other file still made it.
    assert [p.name for p in target.iterdir()] == ["two.nc"]


def test_empty_file_list_fails_cleanly(_qapp, tmp_path) -> None:
    target, finished = _run(tmp_path, ())
    assert finished[0] == (False, "No NC files to copy")


def test_cancel_reports_progress(_qapp, tmp_path) -> None:
    nc = _make_nc(tmp_path, ["one.nc", "two.nc"])
    target = tmp_path / "usb"
    target.mkdir()
    thread = USBTransferThread(nc_files=nc, target_drive=str(target))

    calls = {"count": 0}

    def interruption() -> bool:
        calls["count"] += 1
        return calls["count"] > 1  # allow the first file through

    thread.isInterruptionRequested = interruption  # type: ignore[method-assign]
    finished: list[tuple] = []
    thread.finished.connect(lambda *a: finished.append(a))
    thread.run()

    success, message = finished[0]
    assert success is False
    assert "Cancelled" in message
    assert "copied 1 of 2" in message
