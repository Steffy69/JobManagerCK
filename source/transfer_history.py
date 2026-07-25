"""Local JSON state tracking for job actions.

Tracks what actions (transfer, print, NC copy) have been performed on each job.
State is stored at C:\\Users\\{USERNAME}\\.jobmanager\\history.json.
"""

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, fields
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
        # Parsed-file cache, validated against the file's (mtime_ns, size).
        # The job tree asks for one status per job on every refresh; without
        # this each of those re-opened and re-parsed the whole file.
        self._cache: dict | None = None
        self._cache_stamp: tuple[int, int] | None = None

    # -- public API --------------------------------------------------

    def get_record(self, job_name: str) -> JobRecord | None:
        """Return the record for *job_name*, or None if not tracked.

        The history file is external input (a newer app version's file
        after a rollback, or a hand edit). Unknown keys are dropped and
        missing ones defaulted instead of letting ``JobRecord(**entry)``
        raise TypeError inside a Qt slot — which aborts the process.
        """
        jobs = self._read_jobs()
        entry = jobs.get(job_name)
        if not isinstance(entry, dict):
            return None

        known = {f.name for f in fields(JobRecord)}
        filtered = {k: v for k, v in entry.items() if k in known}
        filtered.setdefault("job_name", job_name)
        filtered.setdefault("job_type", "UNKNOWN")
        try:
            return JobRecord(**filtered)
        except TypeError:
            logger.warning("Malformed history entry for %r; ignoring", job_name)
            return None

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

    def get_all_statuses(self) -> dict[str, str]:
        """Return ``{job_name: status}`` for every tracked job in one read.

        Equivalent to calling :meth:`get_status` per job, but parses the
        history file once instead of once per lookup. Callers that need
        statuses for a whole list of jobs should prefer this. Untracked jobs
        are simply absent — treat a missing key as ``"Ready"``.
        """
        statuses: dict[str, str] = {}
        for job_name, entry in self._read_jobs().items():
            if entry.get("completed_at") is not None:
                statuses[job_name] = "Printed"
            elif (
                entry.get("transferred")
                or entry.get("printed")
                or entry.get("nc_copied")
            ):
                statuses[job_name] = "In Progress"
            else:
                statuses[job_name] = "Ready"
        return statuses

    # -- internal helpers --------------------------------------------

    def _stamp(self) -> tuple[int, int] | None:
        """Return the history file's (mtime_ns, size), or None if absent."""
        try:
            st = os.stat(self._path)
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def _read_all(self) -> dict:
        """Load the full JSON file, returning an empty structure on error.

        The parsed result is cached and reused while the file's
        (mtime_ns, size) is unchanged, so repeated lookups cost one ``stat``
        rather than a full open + parse. An external writer that modifies the
        file invalidates the cache naturally via the stamp.
        """
        stamp = self._stamp()
        if stamp is None:
            self._cache = None
            self._cache_stamp = None
            return {"jobs": {}}

        if self._cache is not None and self._cache_stamp == stamp:
            return self._cache

        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data.get("jobs"), dict):
                data = {"jobs": {}}
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read history file: %s", exc)
            return {"jobs": {}}

        self._cache = data
        self._cache_stamp = stamp
        return data

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
            # The on-disk state is now unknown — drop the cache so the next
            # read goes back to the file rather than trusting stale data.
            self._cache = None
            self._cache_stamp = None
            raise

        # Adopt what we just wrote as the cache, stamped with the new file
        # identity so the next read is served without re-parsing.
        self._cache = data
        self._cache_stamp = self._stamp()

    def _ensure_record(self, job_name: str, job_type: str) -> JobRecord:
        """Return existing record or create a blank one (not persisted)."""
        existing = self.get_record(job_name)
        if existing is not None:
            return existing
        return JobRecord(job_name=job_name, job_type=job_type)

    def _save_record(self, record: JobRecord) -> None:
        """Persist a single record into the history file."""
        current = self._read_all()
        # Copy before mutating: _read_all may have handed back the cached
        # dict, and the cache must not reflect a write that later fails.
        data = {**current, "jobs": {**current["jobs"], record.job_name: asdict(record)}}
        self._write_all(data)
