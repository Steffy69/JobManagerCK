"""Tests for source/transfer_history.py — three-state status model."""

from __future__ import annotations

import glob
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from transfer_history import JobRecord, TransferHistory


@pytest.fixture()
def history(tmp_path) -> TransferHistory:
    return TransferHistory(history_dir=str(tmp_path))


# -- status transitions -----------------------------------------------------


def test_fresh_history_returns_ready(history: TransferHistory) -> None:
    assert history.get_status("UNKNOWN_JOB") == "Ready"


def test_get_status_on_unknown_job(history: TransferHistory) -> None:
    history.mark_transferred("OTHER_JOB", "CABINETRY_ONLINE")
    assert history.get_status("STILL_UNKNOWN") == "Ready"


def test_transfer_moves_to_in_progress(history: TransferHistory) -> None:
    history.mark_transferred("JOB1", "CABINETRY_ONLINE")
    assert history.get_status("JOB1") == "In Progress"


def test_print_moves_to_in_progress(history: TransferHistory) -> None:
    history.mark_printed("JOB2", "CUSTOM_DESIGN")
    assert history.get_status("JOB2") == "In Progress"


def test_nc_copy_moves_to_in_progress(history: TransferHistory) -> None:
    history.mark_nc_copied("JOB3", "CABINETRY_ONLINE")
    assert history.get_status("JOB3") == "In Progress"


def test_move_to_printed_sets_final_status(history: TransferHistory) -> None:
    history.mark_moved_to_printed("JOB4", "CUSTOM_DESIGN")
    assert history.get_status("JOB4") == "Printed"


def test_move_to_printed_after_transfers_still_printed(
    history: TransferHistory,
) -> None:
    history.mark_transferred("JOB5", "CABINETRY_ONLINE")
    assert history.get_status("JOB5") == "In Progress"
    history.mark_printed("JOB5", "CABINETRY_ONLINE")
    assert history.get_status("JOB5") == "In Progress"
    history.mark_moved_to_printed("JOB5", "CABINETRY_ONLINE")
    assert history.get_status("JOB5") == "Printed"


def test_mark_moved_to_printed_creates_record_if_absent(
    history: TransferHistory,
) -> None:
    assert history.get_record("GHOST") is None
    history.mark_moved_to_printed("GHOST", "CUSTOM_DESIGN")
    record = history.get_record("GHOST")
    assert record is not None
    assert record.job_name == "GHOST"
    assert record.job_type == "CUSTOM_DESIGN"
    assert record.completed_at is not None
    assert history.get_status("GHOST") == "Printed"


# -- backwards compatibility ------------------------------------------------


def test_deprecated_mark_completed_still_works(history: TransferHistory) -> None:
    history.mark_completed("LEGACY", "CABINETRY_ONLINE")
    assert history.get_status("LEGACY") == "Printed"
    record = history.get_record("LEGACY")
    assert record is not None
    assert record.completed_at is not None


def test_completed_at_field_preserved_in_record(history: TransferHistory) -> None:
    record = history.mark_moved_to_printed("JOB6", "CABINETRY_ONLINE")
    assert isinstance(record, JobRecord)
    assert record.completed_at is not None


def test_legacy_record_with_completed_at_reports_printed(
    tmp_path,
) -> None:
    """A history file written by an older version (with completed_at but no
    new status strings) should still be read correctly and report 'Printed'."""
    history_file = tmp_path / "history.json"
    history_file.write_text(
        '{"jobs": {"OLDJOB": {"job_name": "OLDJOB", "job_type": "CUSTOM_DESIGN",'
        ' "transferred": true, "printed": true, "nc_copied": false,'
        ' "transferred_at": "2024-01-01T00:00:00+00:00",'
        ' "printed_at": "2024-01-02T00:00:00+00:00",'
        ' "nc_copied_at": null,'
        ' "completed_at": "2024-01-03T00:00:00+00:00"}}}',
        encoding="utf-8",
    )
    history = TransferHistory(history_dir=str(tmp_path))
    assert history.get_status("OLDJOB") == "Printed"


# -- persistence ------------------------------------------------------------


def test_status_persists_across_instances(tmp_path) -> None:
    first = TransferHistory(history_dir=str(tmp_path))
    first.mark_transferred("PERSIST", "CABINETRY_ONLINE")
    first.mark_moved_to_printed("PERSIST", "CABINETRY_ONLINE")

    second = TransferHistory(history_dir=str(tmp_path))
    assert second.get_status("PERSIST") == "Printed"


def test_atomic_write_cleanup(tmp_path, history: TransferHistory) -> None:
    history.mark_transferred("CLEAN", "CABINETRY_ONLINE")
    history.mark_printed("CLEAN", "CABINETRY_ONLINE")
    history.mark_nc_copied("CLEAN", "CABINETRY_ONLINE")
    history.mark_moved_to_printed("CLEAN", "CABINETRY_ONLINE")

    leftover_tmps = glob.glob(os.path.join(str(tmp_path), "history_*.tmp"))
    assert leftover_tmps == []
    assert os.path.exists(os.path.join(str(tmp_path), "history.json"))


# -- clear_moved_to_printed (Restore to Active) -----------------------------


def test_clear_moved_to_printed_reverts_to_in_progress_if_other_actions(
    history: TransferHistory,
) -> None:
    """Transfer + move_to_printed + clear → status should be In Progress."""
    history.mark_transferred("JOB_A", "CABINETRY_ONLINE")
    history.mark_moved_to_printed("JOB_A", "CABINETRY_ONLINE")
    assert history.get_status("JOB_A") == "Printed"

    history.clear_moved_to_printed("JOB_A")
    assert history.get_status("JOB_A") == "In Progress"

    record = history.get_record("JOB_A")
    assert record is not None
    assert record.completed_at is None
    # Other action flags survive the clear.
    assert record.transferred is True
    assert record.transferred_at is not None


def test_clear_moved_to_printed_reverts_to_ready_if_no_other_actions(
    history: TransferHistory,
) -> None:
    """move_to_printed + clear (no other actions) → status should be Ready."""
    history.mark_moved_to_printed("JOB_B", "CUSTOM_DESIGN")
    assert history.get_status("JOB_B") == "Printed"

    history.clear_moved_to_printed("JOB_B")
    assert history.get_status("JOB_B") == "Ready"

    record = history.get_record("JOB_B")
    assert record is not None
    assert record.completed_at is None
    assert record.transferred is False
    assert record.printed is False
    assert record.nc_copied is False


def test_clear_moved_to_printed_returns_none_for_unknown_job(
    history: TransferHistory,
) -> None:
    """Clearing a job that was never tracked is a silent no-op."""
    assert history.clear_moved_to_printed("UNKNOWN_JOB") is None
    # get_status should still report the default Ready for an untracked job.
    assert history.get_status("UNKNOWN_JOB") == "Ready"


def test_clear_moved_to_printed_preserves_print_flag(
    history: TransferHistory,
) -> None:
    """Print + move + clear → status still In Progress (printed flag stays)."""
    history.mark_printed("JOB_C", "CUSTOM_DESIGN")
    history.mark_moved_to_printed("JOB_C", "CUSTOM_DESIGN")
    history.clear_moved_to_printed("JOB_C")

    assert history.get_status("JOB_C") == "In Progress"
    record = history.get_record("JOB_C")
    assert record is not None
    assert record.printed is True
    assert record.printed_at is not None
    assert record.completed_at is None
