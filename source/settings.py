"""Persistent user settings for JobManagerCK v2.1.

Immutable AppSettings dataclass with atomic JSON persistence.
Paths default to ~/.jobmanager/settings.json but can be overridden for tests.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Optional

logger = logging.getLogger(__name__)

SETTINGS_DIR = os.path.join(os.path.expanduser("~"), ".jobmanager")
SETTINGS_PATH = os.path.join(SETTINGS_DIR, "settings.json")

_MIN_PRINT_DELAY = 0.5
_MAX_PRINT_DELAY = 30.0
_MIN_POLL_INTERVAL_MS = 1000


@dataclass(frozen=True)
class AppSettings:
    reverse_order: bool = True
    print_delay_seconds: float = 2.0
    # Sticky per-job material order. Starts with WHMR as the built-in
    # fallback — the PrintOrderDialog grows this tuple over time as the
    # user confirms per-job orders, reflecting their last-used preference.
    material_priority: tuple[str, ...] = ("WHMR",)
    print_separators: bool = True
    auto_mark_printed: bool = False
    status_poll_interval_ms: int = 10000
    zebra_printer_name: str = ""


def _clamp_delay(value: Any) -> float:
    try:
        delay = float(value)
    except (TypeError, ValueError):
        return AppSettings().print_delay_seconds
    if delay < _MIN_PRINT_DELAY:
        return _MIN_PRINT_DELAY
    if delay > _MAX_PRINT_DELAY:
        return _MAX_PRINT_DELAY
    return delay


def _clamp_poll_interval(value: Any) -> int:
    try:
        interval = int(value)
    except (TypeError, ValueError):
        return AppSettings().status_poll_interval_ms
    if interval < _MIN_POLL_INTERVAL_MS:
        return _MIN_POLL_INTERVAL_MS
    return interval


def _coerce_material_priority(value: Any) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    return AppSettings().material_priority


def _from_dict(data: dict[str, Any]) -> AppSettings:
    defaults = AppSettings()
    return AppSettings(
        reverse_order=bool(data.get("reverse_order", defaults.reverse_order)),
        print_delay_seconds=_clamp_delay(
            data.get("print_delay_seconds", defaults.print_delay_seconds)
        ),
        material_priority=_coerce_material_priority(
            data.get("material_priority", defaults.material_priority)
        ),
        print_separators=bool(
            data.get("print_separators", defaults.print_separators)
        ),
        auto_mark_printed=bool(
            data.get("auto_mark_printed", defaults.auto_mark_printed)
        ),
        status_poll_interval_ms=_clamp_poll_interval(
            data.get("status_poll_interval_ms", defaults.status_poll_interval_ms)
        ),
        zebra_printer_name=str(
            data.get("zebra_printer_name", defaults.zebra_printer_name)
        ),
    )


def _to_dict(settings: AppSettings) -> dict[str, Any]:
    data = asdict(settings)
    data["material_priority"] = list(settings.material_priority)
    return data


def load_settings(path: Optional[str] = None) -> AppSettings:
    target = path or SETTINGS_PATH
    if not os.path.exists(target):
        return AppSettings()
    try:
        with open(target, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to load settings from %s: %s", target, exc)
        return AppSettings()
    if not isinstance(data, dict):
        logger.warning("Settings file %s did not contain a JSON object", target)
        return AppSettings()
    return _from_dict(data)


def save_settings(settings: AppSettings, path: Optional[str] = None) -> None:
    target = path or SETTINGS_PATH
    target_dir = os.path.dirname(target)
    if target_dir:
        os.makedirs(target_dir, exist_ok=True)
    tmp_path = target + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(_to_dict(settings), fh, indent=2)
        os.replace(tmp_path, target)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def update_settings(current: AppSettings, **changes: Any) -> AppSettings:
    return replace(current, **changes)
