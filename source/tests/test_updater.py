"""Tests for source/updater.py — version parsing, check caching, download
integrity/cancel, and the apply_update safety guard."""

from __future__ import annotations

import json
import os
import sys
import time
import types

import pytest

pytest.importorskip("PyQt5.QtWidgets")

import updater  # noqa: E402
from updater import (  # noqa: E402
    DOWNLOAD_CANCELLED,
    UpdateChecker,
    UpdateDownloader,
    _parse_version,
    apply_update,
)


@pytest.fixture()
def _qapp():
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def cache_path(tmp_path, monkeypatch):
    path = str(tmp_path / "update_check.json")
    monkeypatch.setattr(updater, "CHECK_CACHE_PATH", path)
    return path


# ---------------------------------------------------------------------------
# _parse_version
# ---------------------------------------------------------------------------


def test_parse_plain_and_v_prefixed() -> None:
    assert _parse_version("2.2.0") == (2, 2, 0)
    assert _parse_version("v2.1.6") == (2, 1, 6)


def test_parse_prerelease_suffix_not_dropped_per_segment() -> None:
    """The old parser turned 2.1.6-rc1 into (2, 1) — OLDER than 2.1.6, so a
    pre-release tag would never be offered and, worse, a current rc build
    would see the final release as an update loop. Suffixes now cut cleanly."""
    assert _parse_version("2.1.6-rc1") == (2, 1, 6)
    assert not _parse_version("2.1.6-rc1") < _parse_version("2.1.6")


def test_parse_garbage_is_empty() -> None:
    assert _parse_version("not-a-version") == ()


# ---------------------------------------------------------------------------
# UpdateChecker — caching
# ---------------------------------------------------------------------------


def _collect(checker):
    got = {"available": [], "up_to_date": [], "error": []}
    checker.update_available.connect(got["available"].append)
    checker.up_to_date.connect(got["up_to_date"].append)
    checker.error.connect(got["error"].append)
    return got


def test_recent_check_is_skipped_without_network(
    _qapp, cache_path, monkeypatch
) -> None:
    with open(cache_path, "w", encoding="utf-8") as fh:
        json.dump({"last_check": time.time(), "latest": "2.1.6"}, fh)

    def explode(*a, **k):
        raise AssertionError("network must not be touched")

    checker = UpdateChecker(force=False)
    monkeypatch.setattr(checker, "_fetch_latest_release", explode)
    got = _collect(checker)

    checker.run()

    assert got["up_to_date"] == ["2.1.6"]
    assert got["error"] == []


def test_forced_check_bypasses_cache(_qapp, cache_path, monkeypatch) -> None:
    with open(cache_path, "w", encoding="utf-8") as fh:
        json.dump({"last_check": time.time(), "latest": "2.1.6"}, fh)

    fetches: list[bool] = []

    def fake_fetch(etag=None):
        fetches.append(True)
        return None, True  # 304: nothing new

    checker = UpdateChecker(force=True)
    monkeypatch.setattr(checker, "_fetch_latest_release", fake_fetch)
    got = _collect(checker)

    checker.run()

    assert fetches == [True]
    assert got["up_to_date"] == ["2.1.6"]


def test_newer_release_emits_update_available(
    _qapp, cache_path, monkeypatch
) -> None:
    info = {
        "version": "v99.0.0",
        "release_notes": "notes",
        "download_url": "https://example/JobManager.exe",
        "size": 123,
        "etag": '"abc"',
    }
    checker = UpdateChecker(force=True)
    monkeypatch.setattr(
        checker, "_fetch_latest_release", lambda etag=None: (info, False)
    )
    got = _collect(checker)

    checker.run()

    assert got["available"] == [info]
    assert got["up_to_date"] == []
    # The check result is cached for the next automatic run.
    with open(cache_path, encoding="utf-8") as fh:
        cached = json.load(fh)
    assert cached["latest"] == "v99.0.0"
    assert cached["etag"] == '"abc"'


def test_same_version_emits_up_to_date(_qapp, cache_path, monkeypatch) -> None:
    """A manual check must always answer — the old code emitted nothing on
    the up-to-date path and left 'Checking for updates...' forever."""
    info = {
        "version": f"v{updater.CURRENT_VERSION}",
        "release_notes": "",
        "download_url": "https://example/JobManager.exe",
        "size": 1,
        "etag": "",
    }
    checker = UpdateChecker(force=True)
    monkeypatch.setattr(
        checker, "_fetch_latest_release", lambda etag=None: (info, False)
    )
    got = _collect(checker)

    checker.run()

    assert got["up_to_date"] == [f"v{updater.CURRENT_VERSION}"]
    assert got["available"] == []


def test_unreachable_emits_error(_qapp, cache_path, monkeypatch) -> None:
    checker = UpdateChecker(force=True)
    monkeypatch.setattr(
        checker, "_fetch_latest_release", lambda etag=None: (None, False)
    )
    got = _collect(checker)

    checker.run()

    assert got["error"] == ["Could not reach GitHub"]


# ---------------------------------------------------------------------------
# UpdateDownloader
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, chunks: list[bytes], content_length: int | None):
        self._chunks = chunks
        self.headers = (
            {"content-length": str(content_length)}
            if content_length is not None
            else {}
        )
        self.status_code = 200

    def raise_for_status(self) -> None:
        pass

    def iter_content(self, chunk_size: int = 65536):
        yield from self._chunks


def _fake_requests(monkeypatch, chunks, content_length):
    fake = types.SimpleNamespace(
        get=lambda *a, **k: _FakeResponse(chunks, content_length),
        RequestException=Exception,
    )
    monkeypatch.setitem(sys.modules, "requests", fake)
    return fake


def _run_downloader(monkeypatch, tmp_path, downloader):
    monkeypatch.setattr(
        "tempfile.gettempdir", lambda: str(tmp_path)
    )
    finished: list[tuple] = []
    downloader.finished.connect(lambda *a: finished.append(a))
    downloader.run()
    return finished


def test_download_success(_qapp, tmp_path, monkeypatch) -> None:
    _fake_requests(monkeypatch, [b"x" * 10, b"y" * 5], content_length=15)
    downloader = UpdateDownloader("https://example/exe", expected_size=15)

    finished = _run_downloader(monkeypatch, tmp_path, downloader)

    success, path = finished[0]
    assert success is True
    assert os.path.getsize(path) == 15


def test_truncated_download_fails_and_removes_partial(
    _qapp, tmp_path, monkeypatch
) -> None:
    """A proxy cutting the stream short must not hand a broken exe to
    apply_update — that would brick the install."""
    _fake_requests(monkeypatch, [b"x" * 10], content_length=15)
    downloader = UpdateDownloader("https://example/exe", expected_size=15)

    finished = _run_downloader(monkeypatch, tmp_path, downloader)

    success, message = finished[0]
    assert success is False
    assert "incomplete" in message
    assert not os.path.exists(tmp_path / "JobManager_update.exe")


def test_cancel_emits_sentinel_and_removes_partial(
    _qapp, tmp_path, monkeypatch
) -> None:
    _fake_requests(monkeypatch, [b"x" * 10, b"y" * 5], content_length=15)
    downloader = UpdateDownloader("https://example/exe")
    downloader.isInterruptionRequested = lambda: True  # type: ignore[method-assign]

    finished = _run_downloader(monkeypatch, tmp_path, downloader)

    assert finished[0] == (False, DOWNLOAD_CANCELLED)
    assert not os.path.exists(tmp_path / "JobManager_update.exe")


# ---------------------------------------------------------------------------
# apply_update
# ---------------------------------------------------------------------------


def test_apply_update_refuses_unfrozen_run() -> None:
    """Run from source, sys.executable is python.exe — the .bat would
    overwrite the Python interpreter."""
    assert not getattr(sys, "frozen", False)
    with pytest.raises(RuntimeError, match="packaged app"):
        apply_update("C:/tmp/JobManager_update.exe")


def test_apply_update_bat_backs_up_and_restores(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\App\JobManager.exe")
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    started: list[str] = []
    monkeypatch.setattr(os, "startfile", started.append, raising=False)
    monkeypatch.setattr(sys, "exit", lambda code=0: None)

    apply_update(r"C:\Temp\JobManager_update.exe")

    bat = tmp_path / "update_jobmanager.bat"
    content = bat.read_text(encoding="utf-8")
    assert r'copy /y "C:\App\JobManager.exe" "C:\App\JobManager.exe.bak"' in content
    assert r'copy /y "C:\App\JobManager.exe.bak" "C:\App\JobManager.exe"' in content
    assert started == [str(bat)]
