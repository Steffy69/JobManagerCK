"""Live printer status widget for JobManagerCK v2.1.

Small pill-shaped widget with a coloured dot and a text label that polls
``printer_service`` every ``poll_interval_ms`` milliseconds. Emits
``statusChanged(bool)`` only on transitions so consumers can react to
connect/disconnect events without thrashing.

The widget never raises from a poll — any exception from the underlying
printer service is caught, the widget is marked offline, and a debug log
entry is written.
"""

from __future__ import annotations

import logging

from PyQt5.QtCore import QTimer, Qt, pyqtSignal
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QWidget

from printer_service import find_zebra_printer, is_printer_available

logger = logging.getLogger(__name__)

_DOT_CHARACTER = "\u25cf"  # Unicode BLACK CIRCLE

_DOT_STYLE_ONLINE = "color: green; font-size: 16px;"
_DOT_STYLE_OFFLINE = "color: red; font-size: 16px;"


class PrinterStatusWidget(QWidget):
    """Polls printer availability and displays a dot + status label.

    Parameters
    ----------
    poll_interval_ms:
        Polling period in milliseconds. Forwarded to the internal QTimer.
    printer_name:
        Target printer. Empty string means "auto-detect via
        ``find_zebra_printer``" — useful when the user hasn't pinned a
        specific printer in settings.
    parent:
        Optional parent widget for ownership.
    """

    statusChanged = pyqtSignal(bool)

    def __init__(
        self,
        poll_interval_ms: int,
        printer_name: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self._available: bool = False
        self._printer_name: str = printer_name or ""
        self._poll_interval_ms: int = max(1, int(poll_interval_ms))

        # Build layout: [dot] [text]
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(6)

        self._dot_label = QLabel(_DOT_CHARACTER, self)
        self._dot_label.setAlignment(Qt.AlignVCenter | Qt.AlignCenter)
        self._dot_label.setStyleSheet(_DOT_STYLE_OFFLINE)

        self._text_label = QLabel("Zebra: Disconnected", self)
        self._text_label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)

        layout.addWidget(self._dot_label)
        layout.addWidget(self._text_label)
        layout.addStretch(1)

        self.setLayout(layout)
        self.setToolTip("Zebra printer status — updates every few seconds")

        # Timer drives polling. Not started until start() is called so tests
        # can construct the widget without side-effects.
        self._timer = QTimer(self)
        self._timer.setInterval(self._poll_interval_ms)
        self._timer.timeout.connect(self._check_status)

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Begin polling and run one immediate check."""
        self._check_status()
        self._timer.start(self._poll_interval_ms)

    def stop(self) -> None:
        """Stop polling. Safe to call multiple times."""
        if self._timer.isActive():
            self._timer.stop()

    # -- configuration -----------------------------------------------------

    def set_printer_name(self, name: str) -> None:
        """Update the target printer and re-check immediately."""
        self._printer_name = name or ""
        self._check_status()

    def set_poll_interval(self, ms: int) -> None:
        """Update the polling interval. Restarts the timer if it's running."""
        self._poll_interval_ms = max(1, int(ms))
        self._timer.setInterval(self._poll_interval_ms)

    def is_online(self) -> bool:
        """Return the widget's current view of printer availability."""
        return self._available

    # -- polling -----------------------------------------------------------

    def _check_status(self) -> None:
        """Query the printer service and update the UI on transition.

        Wrapped in a broad try/except so a bad poll can never crash the
        widget — the worst case is a transient "offline" flicker.
        """
        try:
            target = self._printer_name
            if not target:
                target = find_zebra_printer() or ""
            new_state = bool(target) and is_printer_available(target)
        except Exception:  # noqa: BLE001 — contract: poll never raises
            logger.exception("PrinterStatusWidget poll failed")
            new_state = False

        if new_state == self._available:
            return

        self._available = new_state
        self._update_appearance(new_state)
        self.statusChanged.emit(new_state)

    def _update_appearance(self, available: bool) -> None:
        """Refresh dot colour, label text, and tooltip for the new state."""
        if available:
            self._dot_label.setStyleSheet(_DOT_STYLE_ONLINE)
            self._text_label.setText("Zebra: Connected")
            self.setToolTip("Zebra printer is connected and ready")
        else:
            self._dot_label.setStyleSheet(_DOT_STYLE_OFFLINE)
            self._text_label.setText("Zebra: Disconnected")
            self.setToolTip(
                "Zebra printer is offline — check USB cable and power"
            )
