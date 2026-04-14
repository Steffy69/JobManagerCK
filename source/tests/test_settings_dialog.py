"""Tests for :class:`settings_dialog.SettingsDialog`.

Uses pytest-qt to drive the Qt event loop. External dependencies
(``save_settings``, ``QMessageBox`` statics, ``printer_service``) are
monkeypatched so tests stay hermetic and never touch the real file
system or a real Zebra printer.
"""

from __future__ import annotations

# IMPORTANT: this must run before any PyQt/pytest-qt import so pytest-qt
# binds to PyQt5 rather than the (also-installed) PyQt6.
import os as _os

_os.environ.setdefault("PYTEST_QT_API", "pyqt5")

import os
import sys

import pytest

sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
)

pytest.importorskip("PyQt5.QtWidgets")

from PyQt5.QtWidgets import QMessageBox  # noqa: E402

from settings import AppSettings  # noqa: E402
from settings_dialog import SettingsDialog  # noqa: E402
import settings_dialog as settings_dialog_module  # noqa: E402


def _make_settings(**overrides) -> AppSettings:
    defaults = dict(
        reverse_order=True,
        print_delay_seconds=2.0,
        material_priority=("WHMR",),
        print_separators=True,
        auto_mark_printed=False,
        status_poll_interval_ms=10000,
        zebra_printer_name="",
    )
    defaults.update(overrides)
    return AppSettings(**defaults)


@pytest.fixture()
def dialog(qtbot, monkeypatch):
    """Build a SettingsDialog with save_settings no-oped."""
    # Block real disk writes — every commit path must route through our
    # stub so tests can't pollute the user's settings file.
    monkeypatch.setattr(
        "settings_dialog.save_settings", lambda *a, **k: None
    )
    dlg = SettingsDialog(_make_settings())
    qtbot.addWidget(dlg)
    return dlg


# -- construction & population ---------------------------------------------


def test_dialog_constructs_with_settings(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(
        "settings_dialog.save_settings", lambda *a, **k: None
    )
    dlg = SettingsDialog(_make_settings())
    qtbot.addWidget(dlg)

    assert dlg.windowTitle() == "Print Settings"
    assert dlg.reverse_order_checkbox.isChecked() is True
    assert dlg.print_separators_checkbox.isChecked() is True
    assert dlg.auto_mark_printed_checkbox.isChecked() is False
    assert dlg.print_delay_spinbox.value() == pytest.approx(2.0)


def test_material_peel_order_group_removed(qtbot, monkeypatch) -> None:
    """v2.1: material list moved to the per-job PrintOrderDialog."""
    monkeypatch.setattr(
        "settings_dialog.save_settings", lambda *a, **k: None
    )
    dlg = SettingsDialog(_make_settings(material_priority=("A", "B", "C")))
    qtbot.addWidget(dlg)

    # None of the old material-list widgets should exist on the dialog.
    assert not hasattr(dlg, "material_list")
    assert not hasattr(dlg, "material_input")
    assert not hasattr(dlg, "add_material_button")
    assert not hasattr(dlg, "remove_material_button")

    # And no group-box titled "Material Peel Order" should be present.
    from PyQt5.QtWidgets import QGroupBox

    titles = [gb.title() for gb in dlg.findChildren(QGroupBox)]
    assert "Material Peel Order" not in titles


# -- collect & commit ------------------------------------------------------


def test_collect_settings_returns_updated_instance(
    qtbot, monkeypatch
) -> None:
    captured: list[AppSettings] = []
    monkeypatch.setattr(
        "settings_dialog.save_settings", lambda s, *a, **k: captured.append(s)
    )
    dlg = SettingsDialog(_make_settings(reverse_order=True))
    qtbot.addWidget(dlg)

    dlg.reverse_order_checkbox.setChecked(False)
    dlg._on_apply()

    with qtbot.waitSignal(dlg.settingsApplied, timeout=1000) as blocker:
        dlg._on_apply()  # fire a second time to capture the signal payload
    emitted = blocker.args[0]

    assert isinstance(emitted, AppSettings)
    assert emitted.reverse_order is False
    assert len(captured) >= 1
    assert captured[-1].reverse_order is False


def test_collect_settings_preserves_material_priority(qtbot, monkeypatch) -> None:
    """v2.1: SettingsDialog no longer edits ``material_priority`` — it must
    pass the existing value through untouched so the PrintOrderDialog's
    sticky order is never clobbered by an Apply on the Settings dialog."""
    monkeypatch.setattr(
        "settings_dialog.save_settings", lambda *a, **k: None
    )
    settings = _make_settings(material_priority=("A", "B", "C"))
    dlg = SettingsDialog(settings)
    qtbot.addWidget(dlg)

    collected = dlg._collect_settings()
    assert collected.material_priority == ("A", "B", "C")


def test_apply_does_not_close_dialog(dialog, qtbot) -> None:
    assert dialog.isHidden() is True  # never shown

    with qtbot.waitSignal(dialog.settingsApplied, timeout=1000):
        dialog._on_apply()

    # Apply should not trigger accept() / close().
    assert dialog.result() == 0  # QDialog.Rejected is 0 before accept/reject


def test_ok_closes_dialog_and_saves(qtbot, monkeypatch) -> None:
    call_log: list[AppSettings] = []

    def fake_save(settings, *a, **kw):
        call_log.append(settings)

    monkeypatch.setattr("settings_dialog.save_settings", fake_save)

    dlg = SettingsDialog(_make_settings())
    qtbot.addWidget(dlg)
    dlg.reverse_order_checkbox.setChecked(False)

    with qtbot.waitSignal(dlg.settingsApplied, timeout=1000):
        dlg._on_accept()

    assert len(call_log) == 1
    assert call_log[0].reverse_order is False
    assert dlg.result() == 1  # QDialog.Accepted


def test_cancel_does_not_save(qtbot, monkeypatch) -> None:
    call_log: list[AppSettings] = []
    monkeypatch.setattr(
        "settings_dialog.save_settings",
        lambda s, *a, **k: call_log.append(s),
    )

    dlg = SettingsDialog(_make_settings())
    qtbot.addWidget(dlg)
    dlg.reverse_order_checkbox.setChecked(False)

    dlg.reject()

    assert call_log == []


def test_apply_updates_initial_settings_for_next_collect(
    qtbot, monkeypatch
) -> None:
    """After Apply, _initial_settings should reflect the committed state so
    subsequent Cancel/Apply cycles start from the right baseline."""
    monkeypatch.setattr(
        "settings_dialog.save_settings", lambda *a, **k: None
    )
    dlg = SettingsDialog(_make_settings(reverse_order=True))
    qtbot.addWidget(dlg)
    dlg.reverse_order_checkbox.setChecked(False)

    dlg._on_apply()

    assert dlg._initial_settings.reverse_order is False


# -- troubleshooting handlers ----------------------------------------------


def test_test_print_no_printer_shows_warning(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(
        "settings_dialog.save_settings", lambda *a, **k: None
    )
    monkeypatch.setattr(
        settings_dialog_module.printer_service, "find_zebra_printer",
        lambda: None,
    )
    sent: list[tuple] = []
    monkeypatch.setattr(
        settings_dialog_module.printer_service,
        "send_raw_zpl",
        lambda *a, **k: sent.append(a),
    )

    warn_calls: list[tuple] = []
    monkeypatch.setattr(
        QMessageBox, "warning",
        lambda *a, **k: warn_calls.append(a) or QMessageBox.Ok,
    )

    dlg = SettingsDialog(_make_settings(zebra_printer_name=""))
    qtbot.addWidget(dlg)

    dlg._on_test_print()

    assert len(warn_calls) == 1
    assert sent == []


def test_test_print_success_calls_send_raw_zpl(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(
        "settings_dialog.save_settings", lambda *a, **k: None
    )
    monkeypatch.setattr(
        settings_dialog_module.printer_service,
        "find_zebra_printer",
        lambda: "ZebraGC420D",
    )
    sent: list[tuple] = []

    def fake_send(printer, zpl, **k):
        sent.append((printer, zpl))

    monkeypatch.setattr(
        settings_dialog_module.printer_service, "send_raw_zpl", fake_send
    )
    info_calls: list[tuple] = []
    monkeypatch.setattr(
        QMessageBox, "information",
        lambda *a, **k: info_calls.append(a) or QMessageBox.Ok,
    )
    # Ensure warning is not the path we take — fail loudly if it is.
    monkeypatch.setattr(
        QMessageBox, "warning",
        lambda *a, **k: pytest.fail("Unexpected warning on successful test print"),
    )

    dlg = SettingsDialog(_make_settings())
    qtbot.addWidget(dlg)

    dlg._on_test_print()

    assert len(sent) == 1
    assert sent[0][0] == "ZebraGC420D"
    assert len(info_calls) == 1


def test_test_print_exception_shows_critical(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(
        "settings_dialog.save_settings", lambda *a, **k: None
    )
    monkeypatch.setattr(
        settings_dialog_module.printer_service,
        "find_zebra_printer",
        lambda: "ZebraGC420D",
    )

    def boom(*a, **k):
        raise RuntimeError("printer on fire")

    monkeypatch.setattr(
        settings_dialog_module.printer_service, "send_raw_zpl", boom
    )
    critical_calls: list[tuple] = []
    monkeypatch.setattr(
        QMessageBox, "critical",
        lambda *a, **k: critical_calls.append(a) or QMessageBox.Ok,
    )

    dlg = SettingsDialog(_make_settings())
    qtbot.addWidget(dlg)

    dlg._on_test_print()

    assert len(critical_calls) == 1


def test_clear_queue_confirmation_cancel_skips_clear(
    qtbot, monkeypatch
) -> None:
    monkeypatch.setattr(
        "settings_dialog.save_settings", lambda *a, **k: None
    )
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *a, **k: QMessageBox.No,
    )

    cleared: list[str] = []
    monkeypatch.setattr(
        settings_dialog_module.printer_service,
        "clear_print_queue",
        lambda p: cleared.append(p) or 0,
    )

    dlg = SettingsDialog(_make_settings())
    qtbot.addWidget(dlg)

    dlg._on_clear_queue()

    assert cleared == []


def test_clear_queue_confirmation_yes_calls_clear(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(
        "settings_dialog.save_settings", lambda *a, **k: None
    )
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *a, **k: QMessageBox.Yes,
    )
    monkeypatch.setattr(
        settings_dialog_module.printer_service,
        "find_zebra_printer",
        lambda: "ZebraGC420D",
    )

    cleared: list[str] = []
    monkeypatch.setattr(
        settings_dialog_module.printer_service,
        "clear_print_queue",
        lambda p: cleared.append(p) or 3,
    )
    info_calls: list[tuple] = []
    monkeypatch.setattr(
        QMessageBox, "information",
        lambda *a, **k: info_calls.append(a) or QMessageBox.Ok,
    )

    dlg = SettingsDialog(_make_settings())
    qtbot.addWidget(dlg)

    dlg._on_clear_queue()

    assert cleared == ["ZebraGC420D"]
    assert len(info_calls) == 1


def test_clear_queue_permission_error_friendly_message(
    qtbot, monkeypatch
) -> None:
    monkeypatch.setattr(
        "settings_dialog.save_settings", lambda *a, **k: None
    )
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *a, **k: QMessageBox.Yes,
    )
    monkeypatch.setattr(
        settings_dialog_module.printer_service,
        "find_zebra_printer",
        lambda: "ZebraGC420D",
    )

    def deny(*a, **k):
        raise PermissionError("access denied")

    monkeypatch.setattr(
        settings_dialog_module.printer_service, "clear_print_queue", deny
    )
    warn_calls: list[tuple] = []
    monkeypatch.setattr(
        QMessageBox, "warning",
        lambda *a, **k: warn_calls.append(a) or QMessageBox.Ok,
    )

    dlg = SettingsDialog(_make_settings())
    qtbot.addWidget(dlg)

    dlg._on_clear_queue()

    assert len(warn_calls) == 1
    text = " ".join(str(arg) for arg in warn_calls[0])
    assert "administrator" in text.lower()


def test_clear_queue_no_printer_shows_warning(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(
        "settings_dialog.save_settings", lambda *a, **k: None
    )
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *a, **k: QMessageBox.Yes,
    )
    monkeypatch.setattr(
        settings_dialog_module.printer_service,
        "find_zebra_printer",
        lambda: None,
    )
    cleared: list[str] = []
    monkeypatch.setattr(
        settings_dialog_module.printer_service,
        "clear_print_queue",
        lambda p: cleared.append(p),
    )
    warn_calls: list[tuple] = []
    monkeypatch.setattr(
        QMessageBox, "warning",
        lambda *a, **k: warn_calls.append(a) or QMessageBox.Ok,
    )

    dlg = SettingsDialog(_make_settings(zebra_printer_name=""))
    qtbot.addWidget(dlg)

    dlg._on_clear_queue()

    assert cleared == []
    assert len(warn_calls) == 1
