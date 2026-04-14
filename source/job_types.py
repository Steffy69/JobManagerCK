"""Job type detection and file categorization for JobManagerCK.

Supports both flat folders (all files in root) and structured folders
(with subfolders like 'Label Data', 'Pix', 'Labels').
"""

import logging
import os
from dataclasses import dataclass
from enum import Enum, auto

logger = logging.getLogger(__name__)


class JobType(Enum):
    CABINETRY_ONLINE = auto()  # Has .mdb and/or .wmf files
    CUSTOM_DESIGN = auto()      # Has .ljd files


@dataclass(frozen=True)
class JobFiles:
    nc_files: tuple[str, ...]   # .nc  -- CNC cutting programs
    mdb_files: tuple[str, ...]  # .mdb -- CO label database
    wmf_files: tuple[str, ...]  # .wmf -- CO panel images
    ljd_files: tuple[str, ...]  # .ljd -- CD label files
    emf_files: tuple[str, ...]  # .emf -- CD preview images


# Map of lowercase extension to JobFiles field name
_EXTENSION_MAP: dict[str, str] = {
    ".nc": "nc_files",
    ".mdb": "mdb_files",
    ".wmf": "wmf_files",
    ".ljd": "ljd_files",
    ".emf": "emf_files",
}


def detect_job_type(files: JobFiles) -> JobType:
    """Detect job type from categorized files.

    Priority: CO (.mdb/.wmf) > CD (.ljd) > default (CO).
    """
    has_co = bool(files.mdb_files or files.wmf_files)
    has_cd = bool(files.ljd_files)

    if has_co:
        return JobType.CABINETRY_ONLINE
    if has_cd:
        return JobType.CUSTOM_DESIGN

    return JobType.CABINETRY_ONLINE


def extract_job_id(files: JobFiles) -> str | None:
    """Extract job ID from the first .mdb filename (stem without extension).

    Returns None if no .mdb files exist.
    E.g., 'S:\\Jobs\\...\\Label Data\\12345.mdb' → '12345'
    """
    if not files.mdb_files:
        return None
    return os.path.splitext(os.path.basename(files.mdb_files[0]))[0]


def build_display_name(folder_name: str, files: JobFiles) -> str:
    """Build display name, appending the .mdb job ID if not already present.

    If the folder is 'Smith Kitchen' and the .mdb is '12345.mdb',
    returns 'Smith Kitchen-12345'.
    If the folder already ends with '-12345', returns it unchanged.
    """
    job_id = extract_job_id(files)
    if job_id is None:
        return folder_name
    # Check if folder name already contains the ID at the end
    if folder_name.rstrip().endswith(f"-{job_id}") or folder_name.rstrip().endswith(f"- {job_id}"):
        return folder_name
    return f"{folder_name}-{job_id}"


def scan_folder_files(folder_path: str) -> JobFiles:
    """Recursively scan a folder and categorize files by extension.

    Handles both flat layouts (all files in root) and nested layouts
    (files in subfolders like 'Label Data', 'Pix', 'Labels').

    Args:
        folder_path: Absolute path to the job folder.

    Returns:
        Frozen JobFiles with tuples of absolute paths.
    """
    categorized: dict[str, list[str]] = {
        field: [] for field in _EXTENSION_MAP.values()
    }

    if not os.path.isdir(folder_path):
        logger.warning("Folder does not exist: %s", folder_path)
        return JobFiles(
            nc_files=(),
            mdb_files=(),
            wmf_files=(),
            ljd_files=(),
            emf_files=(),
        )

    for dirpath, _dirnames, filenames in os.walk(folder_path):
        for filename in filenames:
            ext = os.path.splitext(filename)[1].lower()
            field = _EXTENSION_MAP.get(ext)
            if field is not None:
                absolute_path = os.path.join(dirpath, filename)
                categorized[field].append(absolute_path)

    logger.debug(
        "Scanned %s: %s",
        folder_path,
        {k: len(v) for k, v in categorized.items()},
    )

    return JobFiles(
        nc_files=tuple(sorted(categorized["nc_files"])),
        mdb_files=tuple(sorted(categorized["mdb_files"])),
        wmf_files=tuple(sorted(categorized["wmf_files"])),
        ljd_files=tuple(sorted(categorized["ljd_files"])),
        emf_files=tuple(sorted(categorized["emf_files"])),
    )
