"""Job scanner for JobManagerCK.

Scans source directories on the S drive for active jobs,
categorizes their files, and detects job types.
"""

import logging
import os
from dataclasses import dataclass

from job_types import JobFiles, JobType, build_display_name, detect_job_type, scan_folder_files

logger = logging.getLogger(__name__)

# Source directories on the shared S drive
SOURCE_DIRS: dict[str, str] = {
    "Cabinetry Online": r"S:\Jobs\Cabinetry Online",
    "Custom Design": r"S:\Jobs\Custom Design",
}

# Printed jobs live here once Marinko presses "Move to Printed".
# Single source of truth for the printed path — job_manager.py imports this
# constant to avoid duplicate literal definitions.
PRINTED_DIR = r"S:\Jobs\Printed"


@dataclass(frozen=True)
class Job:
    name: str            # Folder name (e.g., "Customer #12345 Kitchen Reno")
    path: str            # Full path to job folder
    job_type: JobType    # Detected type
    files: JobFiles      # Categorized files
    source_folder: str   # Which source ("Cabinetry Online" / "Custom Design" / "Printed" / "Dropped")
    display_name: str = ""  # Name with auto-detected job ID appended
    is_printed: bool = False  # True when the job lives under PRINTED_DIR


def _scan_source_directory(source_name: str, source_path: str) -> list[Job]:
    """Scan a single source directory for jobs.

    Each immediate subdirectory is treated as a job folder.

    Args:
        source_name: Human-readable source name.
        source_path: Absolute path to the source directory.

    Returns:
        List of Job objects found in this source.
    """
    jobs: list[Job] = []

    if not os.path.isdir(source_path):
        logger.warning(
            "Source directory unavailable: %s (%s)", source_name, source_path
        )
        return jobs

    try:
        entries = os.listdir(source_path)
    except OSError:
        logger.warning(
            "Cannot read source directory: %s (%s)", source_name, source_path
        )
        return jobs

    for entry in entries:
        job_path = os.path.join(source_path, entry)
        if not os.path.isdir(job_path):
            continue

        files = scan_folder_files(job_path)
        job_type = detect_job_type(files)

        jobs.append(Job(
            name=entry,
            path=job_path,
            job_type=job_type,
            files=files,
            source_folder=source_name,
            display_name=build_display_name(entry, files),
        ))

    logger.info("Found %d jobs in %s", len(jobs), source_name)
    return jobs


def scan_jobs() -> list[Job]:
    """Scan all source directories for active jobs.

    Walks S:\\Jobs\\Cabinetry Online and S:\\Jobs\\Custom Design.
    Does NOT include S:\\Jobs\\Printed.

    Returns:
        List of Job objects sorted alphabetically by name.
        Returns empty list if the S drive is unavailable.
    """
    all_jobs: list[Job] = []

    for source_name, source_path in SOURCE_DIRS.items():
        all_jobs.extend(_scan_source_directory(source_name, source_path))

    all_jobs.sort(key=lambda job: job.name.lower())

    logger.info("Total active jobs found: %d", len(all_jobs))
    return all_jobs


def scan_printed_jobs(printed_path: str = PRINTED_DIR) -> list[Job]:
    """Scan the Printed folder for jobs Marinko has moved out of Active.

    Uses the same file-detection pipeline as ``scan_jobs`` but stamps every
    result with ``is_printed=True`` so callers can branch on read-only rules.

    Non-existent ``printed_path`` is treated as "no printed jobs yet" — we
    return an empty list so the app survives cold starts before the folder
    exists.

    Args:
        printed_path: Absolute path to the Printed root folder.

    Returns:
        List of Job objects sorted alphabetically by name.
    """
    jobs: list[Job] = []

    if not os.path.isdir(printed_path):
        logger.info("Printed directory unavailable: %s", printed_path)
        return jobs

    try:
        entries = os.listdir(printed_path)
    except OSError:
        logger.warning("Cannot read printed directory: %s", printed_path)
        return jobs

    for entry in entries:
        job_path = os.path.join(printed_path, entry)
        if not os.path.isdir(job_path):
            continue

        files = scan_folder_files(job_path)
        job_type = detect_job_type(files)

        jobs.append(Job(
            name=entry,
            path=job_path,
            job_type=job_type,
            files=files,
            source_folder="Printed",
            display_name=build_display_name(entry, files),
            is_printed=True,
        ))

    jobs.sort(key=lambda job: job.name.lower())

    logger.info("Total printed jobs found: %d", len(jobs))
    return jobs
