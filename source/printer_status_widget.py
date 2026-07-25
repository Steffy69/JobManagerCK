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

from PyQt5.QtCore import QThread, QTimer, Qt, pyqtSignal
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QWidget

from printer_service import list_printers

logger = logging.getLogger(__name__)

_DOT_CHARACTER = "\u25cf"  # Unicode BLACK CIRCLE

_DOT_STYLE_ONLINE = "color: green; font-size: 16px;"
_DOT_STYLE_OFFLINE = "color: red; font-size: 16px;"


class _PollThread(QThread):
    """Runs one printer query off the GUI thread.

    ``EnumPrinters`` with ``PRINTER_ENUM_CONNECTIONS`` enumerates network
    printer connections and has no bounded latency \u2014 against a slow or
    unreachable print server it blocks for seconds. On the GUI thread that is
    a visible freeze, repeated on every poll.
    """

    polled = pyqtSignal(bool, str)

    def __init__(self, query, parent=None) -> None:
        super().__init__(parent)
        self._query = query

    def run(self) -> None:  # noqa: D102 - QThread override
        available, name = self._query()
        self.polled.emit(available, name)


class PrinterStatusWidget(QWidget):
    """Polls printer availability and displays a dot + status label.

    Parameters
    ----------
    poll_interval_ms:
        Polling period in milliseconds. Forwarded to the internal QTimer.
    printer_name:
        Target printer. Empty string means "auto-detect the first installed
        printer whose name contains 'Zebra'" — useful when the user hasn't
        pinned a specific printer in settings.
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
        # Name the last poll actually resolved. With auto-detect this is the
        # discovered Zebra; consumers read it instead of re-enumerating.
        self._resolved_name: str = ""
        self._poll_thread: _PollThread | None = None

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
        self._timer.timeout.connect(self._poll_async)

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Begin polling and run one immediate check."""
        self._check_status()
        self._timer.start(self._poll_interval_ms)

    def stop(self) -> None:
        """Stop polling. Safe to call multiple times."""
        if self._timer.isActive():
            self._timer.stop()

        # Let an in-flight poll finish before the widget can be torn down,
        # so its result slot never runs against a deleted widget.
        thread = self._poll_thread
        if thread is not None:
            try:
                thread.polled.disconnect(self._apply_status)
            except TypeError:
                pass  # already disconnected
            if thread.isRunning():
                thread.wait(5000)
            self._poll_thread = None

    def resolved_printer_name(self) -> str:
        """Return the printer name the most recent poll resolved.

        Empty when no printer was found. Lets callers act on an already-known
        name rather than paying for another spooler enumeration.
        """
        return self._resolved_name

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

    def _query(self) -> tuple[bool, str]:
        """Resolve (available, printer_name) from a single spooler query.

        Pure with respect to widget state, so it is safe to run on a worker
        thread. Previously this took two enumerations — one to find the Zebra
        and another to confirm it was present — but the second is redundant:
        an auto-detected name came *out of* the enumeration, so membership is
        already established. One enumeration answers both questions.

        Wrapped in a broad try/except so a bad poll can never crash the
        widget — the worst case is a transient "offline" flicker.
        """
        try:
            names = list_printers()
            target = self._printer_name
            if not target:
                target = next(
                    (n for n in names if "zebra" in n.lower()), ""
                )
            return (bool(target) and target in names), target
        except Exception:  # noqa: BLE001 — contract: poll never raises
            logger.exception("PrinterStatusWidget poll failed")
            return False, ""

    def _apply_status(self, new_state: bool, resolved_name: str) -> None:
        """Record a poll result and update the UI on transition."""
        self._resolved_name = resolved_name

        if new_state == self._available:
            return

        self._available = new_state
        self._update_appearance(new_state)
        self.statusChanged.emit(new_state)

    def _check_status(self) -> None:
        """Poll synchronously and apply the result.

        Used for the one-off checks the user is waiting on anyway — start-up
        and an explicit printer change from the Settings dialog. The periodic
        poll goes through :meth:`_poll_async` instead.
        """
        available, name = self._query()
        self._apply_status(available, name)

    def _poll_async(self) -> None:
        """Run the periodic poll on a worker thread.

        Skips this tick if the previous poll is still running, which is what
        happens when the spooler is slow — queueing more would pile work onto
        an already-struggling print server.
        """
        if self._poll_thread is not None and self._poll_thread.isRunning():
            return

        thread = _PollThread(self._query, parent=self)
        thread.polled.connect(self._apply_status)
        # Retire on completion — the thread is parented to this widget, so
        # otherwise one QThread child would pile up per poll, forever.
        thread.finished.connect(lambda t=thread: self._retire_poll_thread(t))
        self._poll_thread = thread
        thread.start()

    def _retire_poll_thread(self, thread: _PollThread) -> None:
        """Drop and delete a finished poll thread."""
        if self._poll_thread is thread:
            self._poll_thread = None
        thread.deleteLater()

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
