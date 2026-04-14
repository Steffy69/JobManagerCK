"""Backend integration service for Continental-Kitchens job status.

Provides a Protocol-based abstraction so the app can report job
progress to a future CK FastAPI backend without coupling to it.
"""

import logging
from typing import Protocol

logger = logging.getLogger(__name__)

# Expected backend stage progression:
#   files_ready -> labels_printed -> files_loaded_cnc -> production_complete


class JobStatusService(Protocol):
    """Interface for reporting job status to a remote backend."""

    def report_transfer(self, job_name: str, job_type: str) -> None: ...

    def update_stage(self, job_name: str, stage: str) -> None: ...

    def is_available(self) -> bool: ...


class NullJobStatusService:
    """No-op implementation -- default when the backend is not available."""

    def report_transfer(self, job_name: str, job_type: str) -> None:
        pass

    def update_stage(self, job_name: str, stage: str) -> None:
        pass

    def is_available(self) -> bool:
        return False


class ApiJobStatusService:
    """Stub for the future CK FastAPI backend at http://192.168.0.250:8000.

    Planned endpoints (not yet built):
        PATCH /api/jobs/{job_id}/stage  body: {"stage": "labels_printed"}

    Stages:
        files_ready -> labels_printed -> files_loaded_cnc -> production_complete
    """

    def __init__(self, base_url: str = "http://192.168.0.250:8000") -> None:
        self.base_url = base_url

    def report_transfer(self, job_name: str, job_type: str) -> None:
        # TODO: POST to backend when it's ready
        logger.debug(
            "ApiJobStatusService.report_transfer(%s, %s) -- not implemented",
            job_name,
            job_type,
        )

    def update_stage(self, job_name: str, stage: str) -> None:
        # TODO: PATCH /api/jobs/{job_id}/stage
        logger.debug(
            "ApiJobStatusService.update_stage(%s, %s) -- not implemented",
            job_name,
            stage,
        )

    def is_available(self) -> bool:
        # TODO: health check endpoint
        return False
