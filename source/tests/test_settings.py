"""Tests for source/settings.py."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import FrozenInstanceError

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import settings as settings_module
from settings import (
    AppSettings,
    load_settings,
    save_settings,
    update_settings,
)


def test_default_settings():
    defaults = AppSettings()
    assert defaults.reverse_order is True
    assert defaults.print_delay_seconds == 2.0
    # v2.1: default is just WHMR-first — the PrintOrderDialog grows this
    # tuple as the user confirms per-job material orders.
    assert defaults.material_priority == ("WHMR",)
    assert isinstance(defaults.material_priority, tuple)
    assert defaults.print_separators is True
    assert defaults.auto_mark_printed is False
    assert defaults.status_poll_interval_ms == 10000
    assert defaults.zebra_printer_name == ""


def test_frozen_dataclass():
    defaults = AppSettings()
    with pytest.raises(FrozenInstanceError):
        defaults.reverse_order = False  # type: ignore[misc]


def test_save_and_load_roundtrip(tmp_path):
    path = str(tmp_path / "settings.json")
    original = AppSettings(
        reverse_order=False,
        print_delay_seconds=5.5,
        material_priority=("A", "B", "C"),
        print_separators=False,
        auto_mark_printed=True,
        status_poll_interval_ms=15000,
        zebra_printer_name="ZD420",
    )
    save_settings(original, path=path)
    loaded = load_settings(path=path)
    assert loaded == original


def test_load_missing_file_returns_defaults(tmp_path):
    path = str(tmp_path / "does_not_exist.json")
    loaded = load_settings(path=path)
    assert loaded == AppSettings()


def test_load_malformed_json_returns_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not valid json", encoding="utf-8")
    loaded = load_settings(path=str(path))
    assert loaded == AppSettings()


def test_load_partial_json_uses_defaults_for_missing_fields(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"reverse_order": False, "zebra_printer_name": "ZD420"}),
        encoding="utf-8",
    )
    loaded = load_settings(path=str(path))
    defaults = AppSettings()
    assert loaded.reverse_order is False
    assert loaded.zebra_printer_name == "ZD420"
    assert loaded.print_delay_seconds == defaults.print_delay_seconds
    assert loaded.material_priority == defaults.material_priority
    assert loaded.print_separators == defaults.print_separators
    assert loaded.auto_mark_printed == defaults.auto_mark_printed
    assert loaded.status_poll_interval_ms == defaults.status_poll_interval_ms


def test_load_clamps_invalid_delay(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"print_delay_seconds": -5}), encoding="utf-8")
    assert load_settings(path=str(path)).print_delay_seconds == 0.5

    path.write_text(json.dumps({"print_delay_seconds": 999}), encoding="utf-8")
    assert load_settings(path=str(path)).print_delay_seconds == 30.0


def test_load_clamps_invalid_poll_interval(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"status_poll_interval_ms": 100}), encoding="utf-8")
    assert load_settings(path=str(path)).status_poll_interval_ms == 1000


def test_load_converts_material_priority_list_to_tuple(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"material_priority": ["X", "Y", "Z"]}),
        encoding="utf-8",
    )
    loaded = load_settings(path=str(path))
    assert loaded.material_priority == ("X", "Y", "Z")
    assert isinstance(loaded.material_priority, tuple)


def test_save_creates_directory(tmp_path):
    nested_dir = tmp_path / "nested" / "dir" / "that" / "does_not_exist"
    path = str(nested_dir / "settings.json")
    assert not nested_dir.exists()
    save_settings(AppSettings(), path=path)
    assert os.path.exists(path)


def test_save_is_atomic(tmp_path):
    path = tmp_path / "settings.json"
    save_settings(AppSettings(), path=str(path))
    tmp_file = tmp_path / "settings.json.tmp"
    assert not tmp_file.exists()
    assert path.exists()


def test_update_settings_returns_new_instance():
    original = AppSettings()
    updated = update_settings(original, reverse_order=False, zebra_printer_name="ZD420")
    assert original.reverse_order is True
    assert original.zebra_printer_name == ""
    assert updated.reverse_order is False
    assert updated.zebra_printer_name == "ZD420"
    assert updated is not original


def test_default_path_constants_use_home_dir():
    assert settings_module.SETTINGS_DIR == os.path.join(
        os.path.expanduser("~"), ".jobmanager"
    )
    assert settings_module.SETTINGS_PATH == os.path.join(
        settings_module.SETTINGS_DIR, "settings.json"
    )
