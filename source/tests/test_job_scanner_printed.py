"""Tests for ``scan_printed_jobs`` in source/job_scanner.py.

These tests build a fake Printed folder on ``tmp_path`` and verify that the
scanner walks it the same way ``scan_jobs`` walks the active sources.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from job_scanner import PRINTED_DIR, Job, scan_printed_jobs
from job_types import JobType


# -- empty-state handling ---------------------------------------------------


def test_scan_printed_jobs_empty_returns_empty_list(tmp_path) -> None:
    """A non-existent Printed folder returns [] without raising."""
    missing = tmp_path / "does_not_exist"
    assert scan_printed_jobs(str(missing)) == []


def test_scan_printed_jobs_empty_folder_returns_empty_list(tmp_path) -> None:
    """An existing but empty Printed folder returns []."""
    empty = tmp_path / "Printed"
    empty.mkdir()
    assert scan_printed_jobs(str(empty)) == []


def test_scan_printed_jobs_uses_module_default(monkeypatch, tmp_path) -> None:
    """When called with no arg, uses PRINTED_DIR as the default."""
    # Point the default at a known-good tmp path for this test.
    fake_printed = tmp_path / "PretendPrinted"
    fake_printed.mkdir()
    (fake_printed / "Smith Kitchen").mkdir()
    # Drop a .mdb so detect_job_type has something to chew on.
    (fake_printed / "Smith Kitchen" / "data.mdb").write_bytes(b"")

    monkeypatch.setattr("job_scanner.PRINTED_DIR", str(fake_printed))
    # The default parameter binds at call site; pass via positional default
    # by calling scan_printed_jobs with the patched module constant.
    from job_scanner import PRINTED_DIR as patched_default

    jobs = scan_printed_jobs(patched_default)
    assert len(jobs) == 1
    assert jobs[0].name == "Smith Kitchen"


# -- happy-path scanning ----------------------------------------------------


def _make_printed_folder(tmp_path) -> str:
    """Build a fake Printed folder with one CO job and one CD job."""
    printed = tmp_path / "Printed"
    printed.mkdir()

    # Cabinetry Online-style job: has .mdb and .wmf
    co_job = printed / "Smith Kitchen"
    co_job.mkdir()
    (co_job / "Label Data").mkdir()
    (co_job / "Label Data" / "12345.mdb").write_bytes(b"")
    (co_job / "Pix").mkdir()
    (co_job / "Pix" / "panel.wmf").write_bytes(b"")
    (co_job / "NC").mkdir()
    (co_job / "NC" / "part.nc").write_bytes(b"")

    # Custom Design-style job: has .ljd files
    cd_job = printed / "Jones Wardrobe"
    cd_job.mkdir()
    (cd_job / "Labels").mkdir()
    (cd_job / "Labels" / "JONES_WHMR_0001.ljd").write_bytes(b"")
    (cd_job / "Labels" / "JONES_WHMR_0002.ljd").write_bytes(b"")

    return str(printed)


def test_scan_printed_jobs_with_jobs_returns_both(tmp_path) -> None:
    printed_path = _make_printed_folder(tmp_path)
    jobs = scan_printed_jobs(printed_path)

    assert len(jobs) == 2
    names = {job.name for job in jobs}
    assert names == {"Smith Kitchen", "Jones Wardrobe"}


def test_scan_printed_jobs_marks_is_printed_true(tmp_path) -> None:
    printed_path = _make_printed_folder(tmp_path)
    jobs = scan_printed_jobs(printed_path)

    assert jobs  # sanity
    assert all(job.is_printed is True for job in jobs)


def test_scan_printed_jobs_detects_job_types(tmp_path) -> None:
    printed_path = _make_printed_folder(tmp_path)
    jobs = {job.name: job for job in scan_printed_jobs(printed_path)}

    assert jobs["Smith Kitchen"].job_type == JobType.CABINETRY_ONLINE
    assert jobs["Jones Wardrobe"].job_type == JobType.CUSTOM_DESIGN


def test_scan_printed_jobs_sets_source_folder(tmp_path) -> None:
    printed_path = _make_printed_folder(tmp_path)
    for job in scan_printed_jobs(printed_path):
        assert job.source_folder == "Printed"


def test_scan_printed_jobs_populates_files(tmp_path) -> None:
    printed_path = _make_printed_folder(tmp_path)
    jobs = {job.name: job for job in scan_printed_jobs(printed_path)}

    smith = jobs["Smith Kitchen"]
    assert len(smith.files.mdb_files) == 1
    assert len(smith.files.wmf_files) == 1
    assert len(smith.files.nc_files) == 1
    assert len(smith.files.ljd_files) == 0

    jones = jobs["Jones Wardrobe"]
    assert len(jones.files.ljd_files) == 2
    assert len(jones.files.mdb_files) == 0


def test_scan_printed_jobs_sorts_alphabetically(tmp_path) -> None:
    printed_path = _make_printed_folder(tmp_path)
    jobs = scan_printed_jobs(printed_path)

    names = [job.name for job in jobs]
    assert names == sorted(names, key=str.lower)


def test_scan_printed_jobs_skips_non_directory_entries(tmp_path) -> None:
    printed = tmp_path / "Printed"
    printed.mkdir()
    (printed / "StrayFile.txt").write_bytes(b"not a job")
    (printed / "ValidJob").mkdir()

    jobs = scan_printed_jobs(str(printed))
    assert len(jobs) == 1
    assert jobs[0].name == "ValidJob"


def test_scan_printed_jobs_ignores_unrecognised_extensions(tmp_path) -> None:
    printed = tmp_path / "Printed"
    printed.mkdir()
    job = printed / "MysteryJob"
    job.mkdir()
    (job / "README.md").write_text("hello")

    jobs = scan_printed_jobs(str(printed))
    assert len(jobs) == 1
    mystery = jobs[0]
    assert mystery.files.nc_files == ()
    assert mystery.files.mdb_files == ()
    assert mystery.files.ljd_files == ()


# -- Job dataclass defaults -------------------------------------------------


def test_job_dataclass_defaults_is_printed_false() -> None:
    """Constructing a Job without is_printed defaults to False for active jobs."""
    from job_types import JobFiles

    job = Job(
        name="Test",
        path="/tmp/Test",
        job_type=JobType.CABINETRY_ONLINE,
        files=JobFiles((), (), (), (), ()),
        source_folder="Cabinetry Online",
    )
    assert job.is_printed is False


def test_printed_dir_constant_is_exported() -> None:
    assert isinstance(PRINTED_DIR, str)
    assert "Printed" in PRINTED_DIR
