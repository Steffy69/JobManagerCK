"""Auto-update checker using GitHub Releases API."""

import json
import logging
import os
import sys
import tempfile
from typing import Optional

import requests
from PyQt5.QtCore import QThread, pyqtSignal

logger = logging.getLogger(__name__)

CURRENT_VERSION = "2.1.4"
GITHUB_API_URL = (
    "https://api.github.com/repos/Steffy69/JobManagerCK/releases/latest"
)
ASSET_NAME = "JobManager.exe"


def _parse_version(version_str: str) -> tuple[int, ...]:
    cleaned = version_str.strip().lstrip("vV")
    parts = cleaned.split(".")
    return tuple(int(x) for x in parts if x.isdigit())


class UpdateChecker(QThread):
    """Poll GitHub Releases for a newer version."""

    update_available = pyqtSignal(dict)  # {version, release_notes, download_url}
    error = pyqtSignal(str)

    def run(self) -> None:
        info = self._fetch_latest_release()
        if info is None:
            self.error.emit("Could not reach GitHub")
            return

        new_version = info["version"]
        try:
            if _parse_version(new_version) <= _parse_version(CURRENT_VERSION):
                logger.info("No update: current=%s latest=%s", CURRENT_VERSION, new_version)
                return
        except ValueError as e:
            logger.warning("Version parse failed: %s", e)
            return

        logger.info("Update available: %s -> %s", CURRENT_VERSION, new_version)
        self.update_available.emit(info)

    def _fetch_latest_release(self) -> Optional[dict]:
        try:
            resp = requests.get(
                GITHUB_API_URL,
                timeout=10,
                headers={"Accept": "application/vnd.github+json"},
            )
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, json.JSONDecodeError) as e:
            logger.debug("GitHub release check failed: %s", e)
            return None

        tag = data.get("tag_name", "")
        if not tag:
            return None

        download_url = ""
        for asset in data.get("assets", []):
            if asset.get("name") == ASSET_NAME:
                download_url = asset.get("browser_download_url", "")
                break

        if not download_url:
            logger.warning("Release %s has no %s asset", tag, ASSET_NAME)
            return None

        return {
            "version": tag,
            "release_notes": data.get("body", "") or "",
            "download_url": download_url,
        }


class UpdateDownloader(QThread):
    """Stream-download the new exe to a temp path."""

    progress = pyqtSignal(int)  # 0-100
    finished = pyqtSignal(bool, str)  # (success, tmp_path_or_error)

    def __init__(self, download_url: str) -> None:
        super().__init__()
        self._download_url = download_url

    def run(self) -> None:
        try:
            tmp_path = os.path.join(tempfile.gettempdir(), "JobManager_update.exe")
            self._download(tmp_path)
            self.finished.emit(True, tmp_path)
        except Exception as e:
            logger.error("Update download failed: %s", e)
            self.finished.emit(False, str(e))

    def _download(self, dest: str) -> None:
        if not self._download_url:
            raise ValueError("No download_url provided")

        resp = requests.get(self._download_url, stream=True, timeout=60)
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        downloaded = 0

        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                f.write(chunk)
                downloaded += len(chunk)
                if total > 0:
                    self.progress.emit(int(downloaded / total * 100))


def apply_update(tmp_exe: str) -> None:
    """Spawn a helper .bat that waits for this process to exit, swaps the exe,
    relaunches it, and self-deletes."""
    current_exe = sys.executable
    batch = os.path.join(tempfile.gettempdir(), "update_jobmanager.bat")

    script = (
        "@echo off\r\n"
        "timeout /t 2 /nobreak >nul\r\n"
        f':waitloop\r\n'
        f'tasklist /FI "IMAGENAME eq JobManager.exe" 2>nul | find /I "JobManager.exe" >nul\r\n'
        f'if not errorlevel 1 (\r\n'
        f'    timeout /t 1 /nobreak >nul\r\n'
        f'    goto :waitloop\r\n'
        f')\r\n'
        f'copy /y "{tmp_exe}" "{current_exe}"\r\n'
        f'if errorlevel 1 (\r\n'
        f'    echo Update failed. Old version preserved.\r\n'
        f'    pause\r\n'
        f'    del "{tmp_exe}" 2>nul\r\n'
        f'    del "%~f0" 2>nul\r\n'
        f'    exit /b 1\r\n'
        f')\r\n'
        f'del "{tmp_exe}" 2>nul\r\n'
        f'start "" "{current_exe}"\r\n'
        f'del "%~f0" 2>nul\r\n'
    )

    with open(batch, "w", encoding="utf-8", newline="") as f:
        f.write(script)

    os.startfile(batch)
    sys.exit(0)
