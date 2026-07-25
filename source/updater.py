"""Auto-update checker using GitHub Releases API."""

import json
import logging
import os
import re
import sys
import tempfile
import time
from typing import Optional

from PyQt5.QtCore import QThread, pyqtSignal

# NOTE: ``requests`` is imported lazily inside the two methods that use it.
# It pulls in urllib3, ssl, http.client, email and certifi — several hundred
# modules to unmarshal out of the frozen bundle — and this module is imported
# at start-up purely for CURRENT_VERSION (the window title). The first actual
# HTTP call happens two seconds later, on a worker thread.

logger = logging.getLogger(__name__)

CURRENT_VERSION = "2.2.0"
GITHUB_API_URL = (
    "https://api.github.com/repos/Steffy69/JobManagerCK/releases/latest"
)
ASSET_NAME = "JobManager.exe"

#: Cache of the last successful check. Skipping recent re-checks keeps a
#: workshop full of machines behind one NAT inside GitHub's unauthenticated
#: rate limit (60 requests/hour/IP), and the stored ETag turns most real
#: checks into 304s — which don't count against the limit at all.
CHECK_CACHE_PATH = os.path.join(
    os.path.expanduser("~"), ".jobmanager", "update_check.json"
)
CHECK_INTERVAL_SECONDS = 4 * 60 * 60  # automatic checks at most every 4 h


def _parse_version(version_str: str) -> tuple[int, ...]:
    """Parse ``v2.2.0`` / ``2.2.0-rc1`` into a comparable tuple.

    Pre-release/build suffixes are cut at the first ``-``/``+`` rather than
    silently dropped per-segment — the old per-segment filter parsed
    ``2.1.6-rc1`` as ``(2, 1)``, which compared OLDER than 2.1.6.
    """
    cleaned = version_str.strip().lstrip("vV")
    cleaned = re.split(r"[-+]", cleaned, maxsplit=1)[0]
    parts: list[int] = []
    for segment in cleaned.split("."):
        if not segment.isdigit():
            break
        parts.append(int(segment))
    return tuple(parts)


def _read_check_cache() -> dict:
    try:
        with open(CHECK_CACHE_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_check_cache(data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(CHECK_CACHE_PATH), exist_ok=True)
        with open(CHECK_CACHE_PATH, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    except OSError:
        logger.debug("Could not write update-check cache", exc_info=True)


class UpdateChecker(QThread):
    """Poll GitHub Releases for a newer version.

    Exactly one of the three signals fires per run — a manual "Check for
    Updates" must always produce a visible answer, never a status-bar
    message that sticks forever.
    """

    update_available = pyqtSignal(dict)  # {version, release_notes, download_url, size}
    up_to_date = pyqtSignal(str)  # latest known version
    error = pyqtSignal(str)

    def __init__(self, force: bool = False) -> None:
        """``force=True`` (the Help-menu path) bypasses the check cache."""
        super().__init__()
        self._force = force

    def run(self) -> None:
        cache = _read_check_cache()

        if not self._force:
            last_check = cache.get("last_check", 0)
            if (
                isinstance(last_check, (int, float))
                and time.time() - last_check < CHECK_INTERVAL_SECONDS
            ):
                logger.debug("Update check skipped (checked recently)")
                self.up_to_date.emit(str(cache.get("latest", CURRENT_VERSION)))
                return

        info, not_modified = self._fetch_latest_release(cache.get("etag"))

        if not_modified:
            _write_check_cache({**cache, "last_check": time.time()})
            self.up_to_date.emit(str(cache.get("latest", CURRENT_VERSION)))
            return

        if info is None:
            self.error.emit("Could not reach GitHub")
            return

        _write_check_cache(
            {
                "last_check": time.time(),
                "etag": info.get("etag", ""),
                "latest": info["version"],
            }
        )

        new_version = info["version"]
        if _parse_version(new_version) <= _parse_version(CURRENT_VERSION):
            logger.info(
                "No update: current=%s latest=%s", CURRENT_VERSION, new_version
            )
            self.up_to_date.emit(new_version)
            return

        logger.info("Update available: %s -> %s", CURRENT_VERSION, new_version)
        self.update_available.emit(info)

    def _fetch_latest_release(
        self, etag: Optional[str] = None
    ) -> tuple[Optional[dict], bool]:
        """Return ``(release_info, not_modified)``.

        ``(None, True)`` means the stored ETag still matches (HTTP 304);
        ``(None, False)`` means the check failed.
        """
        import requests

        headers = {"Accept": "application/vnd.github+json"}
        if etag:
            headers["If-None-Match"] = etag

        try:
            resp = requests.get(
                GITHUB_API_URL,
                # (connect, read): fail fast on a dead network instead of
                # holding the thread for the full read timeout.
                timeout=(3, 10),
                headers=headers,
            )
            if resp.status_code == 304:
                return None, True
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, json.JSONDecodeError) as e:
            logger.debug("GitHub release check failed: %s", e)
            return None, False

        tag = data.get("tag_name", "")
        if not tag:
            return None, False

        download_url = ""
        asset_size = 0
        for asset in data.get("assets", []):
            if asset.get("name") == ASSET_NAME:
                download_url = asset.get("browser_download_url", "")
                asset_size = int(asset.get("size", 0) or 0)
                break

        if not download_url:
            logger.warning("Release %s has no %s asset", tag, ASSET_NAME)
            return None, False

        return {
            "version": tag,
            "release_notes": data.get("body", "") or "",
            "download_url": download_url,
            "size": asset_size,
            "etag": resp.headers.get("ETag", ""),
        }, False


#: Sentinel message emitted when the user cancels a download; the UI
#: suppresses the error dialog for this case.
DOWNLOAD_CANCELLED = "cancelled"


class UpdateDownloader(QThread):
    """Stream-download the new exe to a temp path.

    Supports cancellation via ``requestInterruption()`` and verifies the
    downloaded size — a proxy or captive portal returning an HTML page with
    HTTP 200 must not be installed as an "exe".
    """

    progress = pyqtSignal(int)  # 0-100
    finished = pyqtSignal(bool, str)  # (success, tmp_path_or_error)

    def __init__(self, download_url: str, expected_size: int = 0) -> None:
        super().__init__()
        self._download_url = download_url
        self._expected_size = int(expected_size or 0)

    def run(self) -> None:
        tmp_path = os.path.join(tempfile.gettempdir(), "JobManager_update.exe")
        try:
            self._download(tmp_path)
            self.finished.emit(True, tmp_path)
        except InterruptedError:
            logger.info("Update download cancelled by user")
            self._remove_partial(tmp_path)
            self.finished.emit(False, DOWNLOAD_CANCELLED)
        except Exception as e:  # noqa: BLE001 - surface to UI
            logger.error("Update download failed: %s", e)
            self._remove_partial(tmp_path)
            self.finished.emit(False, str(e))

    @staticmethod
    def _remove_partial(path: str) -> None:
        try:
            os.remove(path)
        except OSError:
            pass

    def _download(self, dest: str) -> None:
        import requests

        if not self._download_url:
            raise ValueError("No download_url provided")

        resp = requests.get(
            self._download_url, stream=True, timeout=(3, 30)
        )
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        downloaded = 0

        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                if self.isInterruptionRequested():
                    raise InterruptedError
                if not chunk:
                    continue
                f.write(chunk)
                downloaded += len(chunk)
                if total > 0:
                    self.progress.emit(int(downloaded / total * 100))

        # Integrity: the file must be exactly as long as advertised. Prefer
        # the release asset's size from the GitHub API (survives proxies
        # that strip content-length); fall back to content-length.
        expected = self._expected_size or total
        if expected > 0 and downloaded != expected:
            raise OSError(
                f"Download incomplete ({downloaded} of {expected} bytes). "
                "Check the network connection and try again."
            )
        if downloaded == 0:
            raise OSError("Download produced an empty file.")


def apply_update(tmp_exe: str) -> None:
    """Spawn a helper .bat that waits for this process to exit, swaps the exe,
    relaunches it, and self-deletes.

    The old exe is kept next to the new one as ``JobManager.exe.bak`` — if
    the swapped-in file turns out corrupt, the .bat restores it when the
    copy fails, and a human can restore it manually otherwise.
    """
    if not getattr(sys, "frozen", False):
        # Run from source, sys.executable is python.exe — the .bat's
        # taskkill-wait would exit immediately and overwrite the Python
        # interpreter with JobManager.exe.
        raise RuntimeError(
            "apply_update is only available in the packaged app "
            "(running from source would overwrite python.exe)"
        )

    current_exe = sys.executable
    backup_exe = current_exe + ".bak"
    batch = os.path.join(tempfile.gettempdir(), "update_jobmanager.bat")

    script = (
        "@echo off\r\n"
        "timeout /t 2 /nobreak >nul\r\n"
        ":waitloop\r\n"
        'tasklist /FI "IMAGENAME eq JobManager.exe" 2>nul | find /I "JobManager.exe" >nul\r\n'
        "if not errorlevel 1 (\r\n"
        "    timeout /t 1 /nobreak >nul\r\n"
        "    goto :waitloop\r\n"
        ")\r\n"
        f'copy /y "{current_exe}" "{backup_exe}" >nul\r\n'
        f'copy /y "{tmp_exe}" "{current_exe}"\r\n'
        "if errorlevel 1 (\r\n"
        "    echo Update failed. Restoring previous version.\r\n"
        f'    copy /y "{backup_exe}" "{current_exe}" >nul\r\n'
        "    pause\r\n"
        f'    del "{tmp_exe}" 2>nul\r\n'
        '    del "%~f0" 2>nul\r\n'
        "    exit /b 1\r\n"
        ")\r\n"
        f'del "{tmp_exe}" 2>nul\r\n'
        f'start "" "{current_exe}"\r\n'
        'del "%~f0" 2>nul\r\n'
    )

    with open(batch, "w", encoding="utf-8", newline="") as f:
        f.write(script)

    os.startfile(batch)
    sys.exit(0)
