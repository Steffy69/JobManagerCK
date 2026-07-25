"""Tests for source/job_scan_worker.py.

The failure semantics are load-bearing for the tree: an active-scan failure
must emit ``failed`` (so the window keeps the last good tree), while a
printed-scan failure alone degrades to an empty printed list.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt5.QtWidgets")

from job_scan_worker import JobScanThread  # noqa: E402


@pytest.fixture()
def _qapp():
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def _run(scan_active, scan_printed):
    thread = JobScanThread(scan_active=scan_active, scan_printed=scan_printed)
    got = {"scanned": [], "failed": []}
    thread.scanned.connect(lambda a, p: got["scanned"].append((a, p)))
    thread.failed.connect(got["failed"].append)
    thread.run()
    return got


def test_successful_scan_emits_both_lists(_qapp) -> None:
    got = _run(lambda: ["active1", "active2"], lambda: ["printed1"])

    assert got["scanned"] == [(["active1", "active2"], ["printed1"])]
    assert got["failed"] == []


def test_active_scan_failure_emits_failed_only(_qapp) -> None:
    def boom():
        raise OSError("S: drive unplugged")

    got = _run(boom, lambda: ["printed1"])

    assert got["scanned"] == []
    assert got["failed"] == ["S: drive unplugged"]


def test_printed_scan_failure_degrades_to_empty_list(_qapp) -> None:
    def boom():
        raise OSError("printed folder unreadable")

    got = _run(lambda: ["active1"], boom)

    assert got["scanned"] == [(["active1"], [])]
    assert got["failed"] == []
