"""File logging for JobManagerCK.

The packaged exe is built with ``console=False``, so stderr goes nowhere —
without a file handler every ``logger.exception`` in the codebase is
silently discarded and a problem on the workshop PC leaves no trail at all.
This module gives the app a small rotating log next to its other state in
``~/.jobmanager``.
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

LOG_DIR = os.path.join(os.path.expanduser("~"), ".jobmanager", "logs")
LOG_PATH = os.path.join(LOG_DIR, "jobmanager.log")

_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def setup_logging(level: int = logging.INFO) -> None:
    """Configure root logging: rotating file, plus stderr when running
    from source.

    1 MB × 3 backups is plenty for an app this chatty, and never worth
    worrying about disk-wise. Any failure to set up the file handler is
    swallowed — logging must never stop the app from starting.
    """
    root = logging.getLogger()
    root.setLevel(level)

    stream = logging.StreamHandler()
    stream.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    root.addHandler(stream)

    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        file_handler = RotatingFileHandler(
            LOG_PATH,
            maxBytes=1_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(logging.Formatter(_FORMAT))
        root.addHandler(file_handler)
    except OSError:  # pragma: no cover - depends on host FS state
        root.exception("Could not set up file logging at %s", LOG_PATH)
