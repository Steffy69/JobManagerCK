"""Tests for source/file_transfer.py — the app's one destructive operation.

``run()`` is called synchronously with real files in tmp dirs. The critical
behaviour pinned here: the OLD Label Data must survive any failure that
happens before every new file has staged successfully.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PyQt5.QtWidgets")

from file_transfer import FileTransferThread  # noqa: E402


@pytest.fixture()
def _qapp():
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def _make_sources(tmp_path, mdb=("a.mdb", "b.mdb"), wmf=("x.wmf",)):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    mdb_paths = []
    for name in mdb:
        p = src_dir / name
        p.write_text(f"mdb:{name}")
        mdb_paths.append(str(p))
    wmf_paths = []
    for name in wmf:
        p = src_dir / name
        p.write_text(f"wmf:{name}")
        wmf_paths.append(str(p))
    return tuple(mdb_paths), tuple(wmf_paths)


def _run(tmp_path, mdb, wmf):
    dest = tmp_path / "CADCode"
    thread = FileTransferThread(
        mdb_files=mdb, wmf_files=wmf, dest_base=str(dest)
    )
    finished: list[tuple] = []
    thread.finished.connect(lambda *a: finished.append(a))
    thread.run()
    return dest, finished


def test_successful_transfer_replaces_label_data(_qapp, tmp_path) -> None:
    mdb, wmf = _make_sources(tmp_path)
    # Pre-existing OLD label data that must be replaced.
    label_dir = tmp_path / "CADCode" / "Label Data"
    label_dir.mkdir(parents=True)
    (label_dir / "old.mdb").write_text("stale")

    dest, finished = _run(tmp_path, mdb, wmf)

    assert finished[0][0] is True
    names = sorted(p.name for p in (dest / "Label Data").iterdir())
    assert names == ["a.mdb", "b.mdb"]  # old.mdb gone, no staging leftovers
    assert (dest / "Pix" / "x.wmf").read_text() == "wmf:x.wmf"


def test_staging_failure_leaves_old_label_data_untouched(
    _qapp, tmp_path
) -> None:
    """A missing source file mid-stage must abort with the old data intact."""
    mdb, wmf = _make_sources(tmp_path, mdb=("a.mdb", "b.mdb"))
    os.unlink(mdb[1])  # second file vanishes (network blip analogue)

    label_dir = tmp_path / "CADCode" / "Label Data"
    label_dir.mkdir(parents=True)
    (label_dir / "old.mdb").write_text("precious")

    dest, finished = _run(tmp_path, mdb, wmf)

    success, message = finished[0]
    assert success is False
    assert "untouched" in message
    assert "b.mdb" in message
    # The old data survived, and no staging junk is left behind.
    assert sorted(p.name for p in label_dir.iterdir()) == ["old.mdb"]
    assert (label_dir / "old.mdb").read_text() == "precious"


def test_unchanged_pix_files_are_skipped(_qapp, tmp_path) -> None:
    mdb, wmf = _make_sources(tmp_path)
    # First transfer copies everything.
    dest, first = _run(tmp_path, mdb, wmf)
    assert first[0][0] is True

    # Second transfer of the identical job: image is skipped, not re-copied.
    dest, second = _run(tmp_path, mdb, wmf)
    assert second[0][0] is True
    assert "1 unchanged, skipped" in second[0][1]


def test_pix_failure_reports_per_file(_qapp, tmp_path) -> None:
    mdb, wmf = _make_sources(tmp_path, wmf=("x.wmf", "y.wmf"))
    os.unlink(wmf[1])

    dest, finished = _run(tmp_path, mdb, wmf)

    success, message = finished[0]
    assert success is False
    assert "y.wmf" in message
    # Label data still committed fine.
    assert sorted(p.name for p in (dest / "Label Data").iterdir()) == [
        "a.mdb",
        "b.mdb",
    ]


def test_cancel_during_staging_keeps_old_data(_qapp, tmp_path) -> None:
    mdb, wmf = _make_sources(tmp_path)
    label_dir = tmp_path / "CADCode" / "Label Data"
    label_dir.mkdir(parents=True)
    (label_dir / "old.mdb").write_text("precious")

    dest = tmp_path / "CADCode"
    thread = FileTransferThread(
        mdb_files=mdb, wmf_files=wmf, dest_base=str(dest)
    )
    thread.isInterruptionRequested = lambda: True  # type: ignore[method-assign]
    finished: list[tuple] = []
    thread.finished.connect(lambda *a: finished.append(a))
    thread.run()

    assert finished[0][0] is False
    assert "Cancelled" in finished[0][1]
    assert sorted(p.name for p in label_dir.iterdir()) == ["old.mdb"]
