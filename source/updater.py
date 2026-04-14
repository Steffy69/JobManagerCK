"""Auto-update checker with S drive primary and GitHub fallback."""

import json
import logging
import os
import shutil
import sys
import tempfile
from typing import Optional

import requests
from PyQt5.QtCore import QThread, pyqtSignal

logger = logging.getLogger(__name__)

CURRENT_VERSION = "2.1.0"
S_DRIVE_VERSION_PATH = r"S:\Software\JobManagerCK\releases\version.json"
S_DRIVE_RELEASES_PATH = r"S:\Software\JobManagerCK\releases"
GITHUB_VERSION_URL = (
    "https://raw.githubusercontent.com/Steffy69/JobManagerCK/main/version.json"
)


def _parse_version(version_str: str) -> tuple[int, ...]:
    """Convert '2.1.0' to (2, 1, 0) for comparison."""
    return tuple(int(x) for x in version_str.strip().split("."))


class UpdateChecker(QThread):
    """Check for updates via S drive (primary) or GitHub (fallback)."""

    update_available = pyqtSignal(dict)  # {version, source, release_notes, download_url}
    error = pyqtSignal(str)

    def run(self) -> None:
        result = self._check_s_drive() or self._check_github()
        if result is None:
            self.error.emit("No update source reachable")
            return

        info, source = result
        new_version = info["version"]
        if _parse_version(new_version) > _parse_version(CURRENT_VERSION):
            logger.info("Update available: %s (from %s)", new_version, source)
            self.update_available.emit({
                "version": new_version,
                "source": source,
                "release_notes": info.get("release_notes", ""),
                "download_url": info.get("download_url", ""),
            })

    def _check_s_drive(self) -> Optional[tuple[dict, str]]:
        try:
            with open(S_DRIVE_VERSION_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            return (data, "s_drive")
        except (OSError, KeyError, json.JSONDecodeError) as e:
            logger.debug("S drive check failed: %s", e)
            return None

    def _check_github(self) -> Optional[tuple[dict, str]]:
        try:
            resp = requests.get(GITHUB_VERSION_URL, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            return (data, "github")
        except (requests.RequestException, KeyError, ValueError) as e:
            logger.debug("GitHub check failed: %s", e)
            return None


class UpdateDownloader(QThread):
    """Download and apply an update from S drive or GitHub."""

    progress = pyqtSignal(int)  # percentage 0-100
    finished = pyqtSignal(bool, str)  # (success, result_path_or_error)

    def __init__(self, source: str, download_url: str = "") -> None:
        super().__init__()
        self._source = source
        self._download_url = download_url

    def run(self) -> None:
        try:
            tmp_path = os.path.join(tempfile.gettempdir(), "JobManager_update.exe")

            if self._source == "s_drive":
                self._copy_from_s_drive(tmp_path)
            else:
                self._download_from_github(tmp_path)

            self.finished.emit(True, tmp_path)
        except Exception as e:
            logger.error("Update failed: %s", e)
            self.finished.emit(False, str(e))

    def _copy_from_s_drive(self, dest: str) -> None:
        src = os.path.join(S_DRIVE_RELEASES_PATH, "JobManager.exe")
        self.progress.emit(10)
        shutil.copy2(src, dest)
        self.progress.emit(100)

    def _download_from_github(self, dest: str) -> None:
        url = self._download_url
        if not url:
            raise ValueError("No download_url in version.json")

        resp = requests.get(url, stream=True, timeout=30)
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        downloaded = 0

        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
                downloaded += len(chunk)
                if total > 0:
                    self.progress.emit(int(downloaded / total * 100))

def apply_update(tmp_exe: str) -> None:
    """Create a batch file that replaces the running exe and restarts."""
    current_exe = sys.executable
    batch = os.path.join(tempfile.gettempdir(), "update_jobmanager.bat")

    script = (
        "@echo off\n"
        "timeout /t 2 /nobreak >nul\n"
        f'copy /y "{tmp_exe}" "{current_exe}"\n'
        f'del "{tmp_exe}"\n'
        f'start "" "{current_exe}"\n'
        f'del "%~f0"\n'
    )

    with open(batch, "w", encoding="utf-8") as f:
        f.write(script)

    os.startfile(batch)
    sys.exit(0)
