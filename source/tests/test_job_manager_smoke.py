"""Smoke tests for the main JobManager window — construct with mocks and
verify the QTreeWidget behaves correctly for both active and printed jobs.

These tests use pytest-qt to drive the PyQt5 event loop. The JobManager
window is constructed with ``scan_jobs`` and ``scan_printed_jobs`` stubbed
so we don't touch the real S drive.
"""

from __future__ import annotations

# IMPORTANT: this must run before any PyQt/pytest-qt import so pytest-qt
# binds to PyQt5 rather than the (also-installed) PyQt6.
import os as _os

_os.environ.setdefault("PYTEST_QT_API", "pyqt5")

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from job_scanner import Job
from job_types import JobFiles, JobType

# Skip the whole module gracefully if PyQt5 is somehow missing.
pytest.importorskip("PyQt5.QtWidgets")

from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtWidgets import QTreeWidgetItem  # noqa: E402


def _make_job(
    name: str,
    *,
    is_printed: bool = False,
    job_type: JobType = JobType.CABINETRY_ONLINE,
    has_mdb: bool = False,
    has_ljd: bool = False,
    has_nc: bool = False,
) -> Job:
    mdb = (f"/fake/{name}/data.mdb",) if has_mdb else ()
    ljd = (f"/fake/{name}/labels.ljd",) if has_ljd else ()
    nc = (f"/fake/{name}/part.nc",) if has_nc else ()
    files = JobFiles(
        nc_files=nc,
        mdb_files=mdb,
        wmf_files=(),
        ljd_files=ljd,
        emf_files=(),
    )
    return Job(
        name=name,
        path=f"/fake/{name}",
        job_type=job_type,
        files=files,
        source_folder=("Printed" if is_printed else "Cabinetry Online"),
        display_name=name,
        is_printed=is_printed,
    )


@pytest.fixture()
def job_manager_window(qtbot, monkeypatch, tmp_path):
    """Build a JobManager window with stubbed scanners and history dir."""
    # Stub out scanners so refresh_jobs doesn't touch the real S drive.
    fake_active = [
        _make_job("Active CO Job", has_mdb=True),
        _make_job(
            "Active CD Job",
            job_type=JobType.CUSTOM_DESIGN,
            has_ljd=True,
            has_nc=True,
        ),
    ]
    fake_printed = [
        _make_job("Printed Job One", is_printed=True, has_mdb=True),
        _make_job(
            "Printed Job Two",
            is_printed=True,
            job_type=JobType.CUSTOM_DESIGN,
            has_ljd=True,
        ),
    ]

    monkeypatch.setattr("job_manager.scan_jobs", lambda: list(fake_active))
    monkeypatch.setattr(
        "job_manager.scan_printed_jobs", lambda *a, **k: list(fake_printed)
    )

    # Silence the Archive->Printed migration for test mode.
    monkeypatch.setattr("job_manager._migrate_archive_to_printed", lambda: None)

    # Route history to a tmp dir so we don't pollute the user's real file.
    monkeypatch.setattr(
        "transfer_history.DEFAULT_HISTORY_DIR", str(tmp_path / "history")
    )

    # Suppress the "check for updates" background thread.
    monkeypatch.setattr(
        "job_manager.QTimer.singleShot", lambda *a, **k: None
    )

    from job_manager import JobManager

    window = JobManager()
    qtbot.addWidget(window)
    return window


# -- tree structure ---------------------------------------------------------


def test_tree_has_two_top_level_roots(job_manager_window) -> None:
    tree = job_manager_window.jobTreeWidget
    assert tree.topLevelItemCount() == 2
    assert tree.topLevelItem(0).text(0) == "Active Jobs"
    assert tree.topLevelItem(1).text(0).startswith("Printed Jobs")


def test_active_root_is_expanded_printed_root_is_collapsed(
    job_manager_window,
) -> None:
    tree = job_manager_window.jobTreeWidget
    assert tree.topLevelItem(0).isExpanded() is True
    assert tree.topLevelItem(1).isExpanded() is False


def test_printed_root_label_shows_count(job_manager_window) -> None:
    tree = job_manager_window.jobTreeWidget
    assert tree.topLevelItem(1).text(0) == "Printed Jobs (2)"


def test_active_root_holds_stubbed_active_jobs(job_manager_window) -> None:
    root = job_manager_window.jobTreeWidget.topLevelItem(0)
    assert root.childCount() == 2
    job_names = {
        root.child(i).data(0, Qt.UserRole).name
        for i in range(root.childCount())
    }
    assert job_names == {"Active CO Job", "Active CD Job"}


def test_printed_root_holds_stubbed_printed_jobs(job_manager_window) -> None:
    root = job_manager_window.jobTreeWidget.topLevelItem(1)
    assert root.childCount() == 2
    for i in range(root.childCount()):
        job = root.child(i).data(0, Qt.UserRole)
        assert job.is_printed is True


# -- selection wiring -------------------------------------------------------


def _restore_button_is_shown(window) -> bool:
    """Return True when restoreButton is set to appear (independent of window.show()).

    ``QWidget.isVisible()`` only returns True after the parent window has been
    shown on screen. In headless tests we never call ``window.show()``, so we
    use ``isHidden()`` / ``isVisibleTo(parent)`` as a proxy for "the widget
    would be visible if the window were shown".
    """
    return not window.restoreButton.isHidden()


def test_selecting_active_job_enables_action_buttons(job_manager_window) -> None:
    window = job_manager_window
    root = window.jobTreeWidget.topLevelItem(0)
    # Find the CO job (has .mdb -> transfer enabled).
    co_item: QTreeWidgetItem | None = None
    for i in range(root.childCount()):
        child = root.child(i)
        if child.data(0, Qt.UserRole).name == "Active CO Job":
            co_item = child
            break
    assert co_item is not None

    window.jobTreeWidget.setCurrentItem(co_item)

    assert window.transferButton.isEnabled() is True
    assert window.completeButton.isEnabled() is True
    assert _restore_button_is_shown(window) is False


def test_selecting_printed_job_hides_actions_shows_restore(
    job_manager_window,
) -> None:
    window = job_manager_window
    printed_root = window.jobTreeWidget.topLevelItem(1)
    assert printed_root.childCount() > 0
    first_printed = printed_root.child(0)

    window.jobTreeWidget.setCurrentItem(first_printed)

    # Action buttons should all be disabled for printed jobs.
    assert window.transferButton.isEnabled() is False
    assert window.printButton.isEnabled() is False
    assert window.copyNCButton.isEnabled() is False
    assert window.completeButton.isEnabled() is False

    # Restore button should be set to visible (isHidden() False).
    assert _restore_button_is_shown(window) is True


def test_selecting_root_item_returns_no_job(job_manager_window) -> None:
    window = job_manager_window
    window.jobTreeWidget.setCurrentItem(window.jobTreeWidget.topLevelItem(0))
    assert window._selected_job() is None


def test_no_selection_hides_restore_disables_actions(job_manager_window) -> None:
    window = job_manager_window
    window.jobTreeWidget.setCurrentItem(None)
    # Trigger the handler explicitly since setCurrentItem(None) may not emit.
    window._on_selection_changed()

    assert _restore_button_is_shown(window) is False
    assert window.transferButton.isEnabled() is False


# -- refresh preservation ---------------------------------------------------


def test_refresh_preserving_selection_keeps_active_job(
    job_manager_window,
) -> None:
    window = job_manager_window
    root = window.jobTreeWidget.topLevelItem(0)
    target = None
    for i in range(root.childCount()):
        if root.child(i).data(0, Qt.UserRole).name == "Active CO Job":
            target = root.child(i)
            break
    assert target is not None
    window.jobTreeWidget.setCurrentItem(target)

    window._refresh_preserving_selection()

    still_selected = window._selected_job()
    assert still_selected is not None
    assert still_selected.name == "Active CO Job"
    assert still_selected.is_printed is False


def test_refresh_preserving_selection_keeps_printed_job(
    job_manager_window,
) -> None:
    window = job_manager_window
    printed_root = window.jobTreeWidget.topLevelItem(1)
    target = printed_root.child(0)
    target_name = target.data(0, Qt.UserRole).name
    window.jobTreeWidget.setCurrentItem(target)

    window._refresh_preserving_selection()

    still_selected = window._selected_job()
    assert still_selected is not None
    assert still_selected.name == target_name
    assert still_selected.is_printed is True
