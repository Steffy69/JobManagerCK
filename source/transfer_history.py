"""Local JSON state tracking for job actions.

Tracks what actions (transfer, print, NC copy) have been performed on each job.
State is stored at C:\\Users\\{USERNAME}\\.jobmanager\\history.json.
"""

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

DEFAULT_HISTORY_DIR = os.path.join(
    os.path.expanduser("~"), ".jobmanager"
)


@dataclass(frozen=True)
class JobRecord:
    """Immutable record of a job's action history."""

    job_name: str
    job_type: str  # "CABINETRY_ONLINE" or "CUSTOM_DESIGN"
    transferred: bool = False
    printed: bool = False
    nc_copied: bool = False
    transferred_at: str | None = None
    printed_at: str | None = None
    nc_copied_at: str | None = None
    completed_at: str | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TransferHistory:
    """Reads and writes job action history from a local JSON file.

    Uses atomic writes (write to temp file, then rename) to avoid
    corruption from crashes or concurrent access.
    """

    def __init__(self, history_dir: str | None = None) -> None:
        self._dir = history_dir or DEFAULT_HISTORY_DIR
        os.makedirs(self._dir, exist_ok=True)
        self._path = os.path.join(self._dir, "history.json")

    # -- public API --------------------------------------------------

    def get_record(self, job_name: str) -> JobRecord | None:
        """Return the record for *job_name*, or None if not tracked."""
        jobs = self._read_jobs()
        entry = jobs.get(job_name)
        if entry is None:
            return None
        return JobRecord(**entry)

    def mark_transferred(self, job_name: str, job_type: str) -> JobRecord:
        """Mark a job as transferred and persist the change."""
        record = self._ensure_record(job_name, job_type)
        updated = JobRecord(
            **{**asdict(record), "transferred": True, "transferred_at": _now_iso()}
        )
        self._save_record(updated)
        return updated

    def mark_printed(self, job_name: str, job_type: str) -> JobRecord:
        """Mark a job as printed and persist the change."""
        record = self._ensure_record(job_name, job_type)
        updated = JobRecord(
            **{**asdict(record), "printed": True, "printed_at": _now_iso()}
        )
        self._save_record(updated)
        return updated

    def mark_nc_copied(self, job_name: str, job_type: str) -> JobRecord:
        """Mark a job's NC files as copied and persist the change."""
        record = self._ensure_record(job_name, job_type)
        updated = JobRecord(
            **{**asdict(record), "nc_copied": True, "nc_copied_at": _now_iso()}
        )
        self._save_record(updated)
        return updated

    def mark_moved_to_printed(
        self, job_name: str, job_type: str = "UNKNOWN"
    ) -> JobRecord:
        """Mark a job as moved to the Printed folder.

        Sets ``completed_at`` to the current ISO timestamp. The field name is
        kept for backwards compatibility with existing history files even
        though the semantic meaning is now "moved to Printed folder".
        Creates a record if the job is not yet tracked.
        """
        record = self._ensure_record(job_name, job_type)
        updated = JobRecord(
            **{**asdict(record), "completed_at": _now_iso()}
        )
        self._save_record(updated)
        return updated

    # deprecated: use mark_moved_to_printed
    def mark_completed(self, job_name: str, job_type: str = "UNKNOWN") -> JobRecord:
        return self.mark_moved_to_printed(job_name, job_type)

    def clear_moved_to_printed(self, job_name: str) -> JobRecord | None:
        """Reset ``completed_at`` so a job returns from Printed to Active.

        Used by the "Restore to Active" button. Resets only ``completed_at``
        — other action flags (transferred/printed/nc_copied) are preserved,
        so a restored job becomes "In Progress" if it had any prior actions,
        otherwise "Ready".

        Returns the updated record, or ``None`` if the job was not tracked.
        """
        existing = self.get_record(job_name)
        if existing is None:
            return None
        updated = JobRecord(
            **{**asdict(existing), "completed_at": None}
        )
        self._save_record(updated)
        return updated

    def get_status(self, job_name: str) -> str:
        """Return a human-readable status string.

        Returns:
            "Printed"     - completed_at is set (job moved to Printed folder)
            "In Progress" - at least one action (transferred/printed/nc_copied)
                            has been taken but the job has not been moved
            "Ready"       - no actions taken or job not tracked
        """
        record = self.get_record(job_name)
        if record is None:
            return "Ready"
        if record.completed_at is not None:
            return "Printed"
        if record.transferred or record.printed or record.nc_copied:
            return "In Progress"
        return "Ready"

    # -- internal helpers --------------------------------------------

    def _read_all(self) -> dict:
        """Load the full JSON file, returning an empty structure on error."""
        if not os.path.exists(self._path):
            return {"jobs": {}}
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data.get("jobs"), dict):
                return {"jobs": {}}
            return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read history file: %s", exc)
            return {"jobs": {}}

    def _read_jobs(self) -> dict:
        return self._read_all()["jobs"]

    def _write_all(self, data: dict) -> None:
        """Atomically write *data* to the history file."""
        fd, tmp_path = tempfile.mkstemp(
            dir=self._dir, suffix=".tmp", prefix="history_"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
            # On Windows, os.rename fails if target exists; use os.replace.
            os.replace(tmp_path, self._path)
        except OSError:
            # Clean up temp file on failure.
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    def _ensure_record(self, job_name: str, job_type: str) -> JobRecord:
        """Return existing record or create a blank one (not persisted)."""
        existing = self.get_record(job_name)
        if existing is not None:
            return existing
        return JobRecord(job_name=job_name, job_type=job_type)

    def _save_record(self, record: JobRecord) -> None:
        """Persist a single record into the history file."""
        data = self._read_all()
        data["jobs"][record.job_name] = asdict(record)
        self._write_all(data)
