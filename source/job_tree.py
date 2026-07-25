"""Job tree management for the main window.

Owns everything about the two-root QTreeWidget (Active Jobs / Printed
Jobs): building rows, status colours, the rebuild-skipping signature, and
preserving the user's place (selection, expansion, scroll) across the
background refreshes that repaint it every few seconds.
"""

from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QTreeWidget, QTreeWidgetItem

from job_scanner import Job
from job_types import JobFiles
from transfer_history import TransferHistory

# Colour constants for job status
COLOR_READY = QColor(0, 128, 0)              # green — no actions taken
COLOR_IN_PROGRESS = QColor(0, 0, 200)        # blue — at least one action taken
COLOR_PRINTED_FINAL = QColor(120, 120, 120)  # grey — moved to Printed folder
COLOR_DEFAULT = QColor(0, 0, 0)              # black — fallback


def build_tooltip(files: JobFiles) -> str:
    """Build a tooltip string showing file counts for a job."""
    parts: list[str] = []
    if files.nc_files:
        parts.append(f"{len(files.nc_files)} NC files")
    if files.mdb_files:
        parts.append(f"{len(files.mdb_files)} MDB files")
    if files.wmf_files:
        parts.append(f"{len(files.wmf_files)} WMF files")
    if files.ljd_files:
        parts.append(f"{len(files.ljd_files)} LJD files")
    return ", ".join(parts) if parts else "No recognised files"


class JobTreeController:
    """Populates and reads the job tree on behalf of the main window.

    ``on_rebuilt`` is called after every actual rebuild (signals are blocked
    during it, so the window re-syncs its buttons there).
    """

    def __init__(
        self,
        tree: QTreeWidget,
        history: TransferHistory,
        on_rebuilt,
    ) -> None:
        self._tree = tree
        self._history = history
        self._on_rebuilt = on_rebuilt
        self._active_root: Optional[QTreeWidgetItem] = None
        self._printed_root: Optional[QTreeWidgetItem] = None
        # Identifies what the tree currently displays. A refresh whose
        # result matches this skips the rebuild entirely, which is the
        # common case — job folders change rarely, but the refresh timer
        # fires constantly.
        self._last_signature: Optional[tuple] = None

    # -- reading -------------------------------------------------------

    def selected_job(self) -> Optional[Job]:
        """Return the currently selected Job, or None.

        Returns None if no item is selected or if a root header is selected.
        """
        item = self._tree.currentItem()
        if item is None:
            return None
        # Root items (Active Jobs / Printed Jobs) have no parent.
        if item.parent() is None:
            return None
        data = item.data(0, Qt.UserRole)
        if isinstance(data, Job):
            return data
        return None

    # -- building ------------------------------------------------------

    def _signature(
        self,
        active_jobs: list[Job],
        printed_jobs: list[Job],
        statuses: dict[str, str],
    ) -> tuple:
        """Return a value identifying exactly what the tree should display.

        ``Job`` and ``JobFiles`` are frozen dataclasses, so comparing them
        compares every rendered field. Statuses are folded in because they
        drive the row colour. If this is unchanged since the last rebuild,
        the tree is already correct and rebuilding it would only cost the
        user their scroll position and expansion state.
        """
        return (
            tuple(active_jobs),
            tuple(printed_jobs),
            tuple(statuses.get(j.name, "Ready") for j in active_jobs),
        )

    def populate(
        self, active_jobs: list[Job], printed_jobs: list[Job]
    ) -> bool:
        """Fill the tree with two roots: Active Jobs + Printed Jobs.

        Returns True if the tree was rebuilt, False if it was already
        showing exactly this content and the rebuild was skipped.
        """
        # One read of the history file for the whole tree, rather than one
        # read per job.
        statuses = self._history.get_all_statuses()

        signature = self._signature(active_jobs, printed_jobs, statuses)
        if signature == self._last_signature:
            return False

        tree = self._tree

        # Remember the user's place so a background refresh doesn't move it.
        selected = self.selected_job()
        selection_key: Optional[tuple[str, bool]] = (
            (selected.name, selected.is_printed)
            if selected is not None
            else None
        )
        first_build = self._last_signature is None
        if first_build:
            active_expanded, printed_expanded = True, False
        else:
            active_expanded = (
                self._active_root.isExpanded()
                if self._active_root is not None else True
            )
            printed_expanded = (
                self._printed_root.isExpanded()
                if self._printed_root is not None else False
            )
        scroll_value = tree.verticalScrollBar().value()

        # Suppress painting and selection signals for the whole rebuild:
        # clear() and each addChild() would otherwise trigger layout work
        # and re-entrant selection handling per item.
        tree.setUpdatesEnabled(False)
        blocked = tree.blockSignals(True)
        try:
            tree.clear()

            active_root = QTreeWidgetItem(["Active Jobs"])
            printed_root = QTreeWidgetItem(
                [f"Printed Jobs ({len(printed_jobs)})"]
            )

            tree.addTopLevelItem(active_root)
            tree.addTopLevelItem(printed_root)

            for job in active_jobs:
                active_root.addChild(self._build_job_item(job, statuses))

            for job in printed_jobs:
                item = self._build_job_item(job, statuses)
                # Printed jobs always wear the grey colour, regardless of
                # what the history file says — they were explicitly moved
                # out of Active.
                item.setForeground(0, COLOR_PRINTED_FINAL)
                printed_root.addChild(item)

            active_root.setExpanded(active_expanded)
            printed_root.setExpanded(printed_expanded)

            self._active_root = active_root
            self._printed_root = printed_root

            self._restore_selection(selection_key)
            tree.verticalScrollBar().setValue(scroll_value)
        finally:
            tree.blockSignals(blocked)
            tree.setUpdatesEnabled(True)

        self._last_signature = signature
        # Signals were blocked during the rebuild, so let the window bring
        # its buttons back in sync by hand.
        self._on_rebuilt()
        return True

    def select_job_by_name(self, name: str) -> bool:
        """Select the active job called *name*. Returns True on success."""
        root = self._active_root
        if root is None:
            return False
        for i in range(root.childCount()):
            child = root.child(i)
            job = child.data(0, Qt.UserRole)
            if isinstance(job, Job) and job.name == name:
                root.setExpanded(True)
                self._tree.setCurrentItem(child)
                return True
        return False

    def _restore_selection(self, key: Optional[tuple[str, bool]]) -> None:
        """Re-select the job identified by *key* after a rebuild.

        The key is ``(job.name, is_printed)`` so a job present in both the
        Active and Printed trees (shouldn't happen in practice, but safe)
        is re-selected in the same tree it was chosen from. A job that no
        longer exists clears the selection cleanly.
        """
        if key is None:
            return

        target_name, target_is_printed = key
        root = self._printed_root if target_is_printed else self._active_root
        if root is not None:
            for i in range(root.childCount()):
                child = root.child(i)
                job = child.data(0, Qt.UserRole)
                if isinstance(job, Job) and job.name == target_name:
                    # A restored selection inside a collapsed root would be
                    # invisible — make sure the user can see it.
                    root.setExpanded(True)
                    self._tree.setCurrentItem(child)
                    return

        self._tree.setCurrentItem(None)

    @staticmethod
    def _build_job_item(
        job: Job, statuses: dict[str, str]
    ) -> QTreeWidgetItem:
        """Create a QTreeWidgetItem for a Job with label, tooltip, colour.

        *statuses* is the pre-read ``{job_name: status}`` map from
        :meth:`TransferHistory.get_all_statuses`, so building a row costs
        no file I/O.
        """
        from job_types import JobType  # local import avoids a cycle risk

        tag = "CO" if job.job_type == JobType.CABINETRY_ONLINE else "CD"
        label = f"[{tag}] {job.display_name or job.name}"
        item = QTreeWidgetItem([label])
        item.setData(0, Qt.UserRole, job)
        item.setToolTip(0, build_tooltip(job.files))

        status = statuses.get(job.name, "Ready")
        if status == "Ready":
            item.setForeground(0, COLOR_READY)
        elif status == "In Progress":
            item.setForeground(0, COLOR_IN_PROGRESS)
        elif status == "Printed":
            item.setForeground(0, COLOR_PRINTED_FINAL)
        else:
            item.setForeground(0, COLOR_DEFAULT)
        return item
