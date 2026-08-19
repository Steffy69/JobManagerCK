"""Tests for source/ui_font.py — app-wide accessibility font scaling."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt5.QtWidgets")

import ui_font  # noqa: E402
from ui_font import apply_ui_font_size  # noqa: E402


@pytest.fixture()
def _qapp():
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _restore_font(_qapp):
    """Leave the shared QApplication's font as we found it."""
    original = _qapp.font()
    ui_font._system_default_size = None
    yield
    _qapp.setFont(original)
    ui_font._system_default_size = None


def test_apply_sets_point_size(_qapp) -> None:
    apply_ui_font_size(14)
    assert _qapp.font().pointSize() == 14


def test_zero_restores_system_default(_qapp) -> None:
    default = _qapp.font().pointSize()

    apply_ui_font_size(18)
    assert _qapp.font().pointSize() == 18

    apply_ui_font_size(0)
    assert _qapp.font().pointSize() == default


def test_zero_before_any_change_is_a_noop(_qapp) -> None:
    default = _qapp.font().pointSize()
    apply_ui_font_size(0)
    assert _qapp.font().pointSize() == default


def test_repeated_apply_is_stable(_qapp) -> None:
    apply_ui_font_size(12)
    apply_ui_font_size(12)
    assert _qapp.font().pointSize() == 12
