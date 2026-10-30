"""
Lightweight domain models using Python standard library dataclasses.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class JobStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    RUNNING = "running"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        """Terminal statuses are the only ones that count as success/failed."""
        return self in (JobStatus.SUCCESS, JobStatus.FAILED)


@dataclass(slots=True, frozen=True)
class JobRecord:
    """Represents a single job execution attempt."""

    job_id: str
    status: JobStatus
    started_at: datetime
    finished_at: datetime | None = None
    attempt: int = 1

    @property
    def duration_seconds(self) -> float | None:
        """finished_at - started_at in seconds, or None if not finished."""
        if self.finished_at is None:
            return None
        return round((self.finished_at - self.started_at).total_seconds(), 2)

    @property
    def is_successful(self) -> bool:
        return self.status is JobStatus.SUCCESS

    @property
    def is_failed(self) -> bool:
        return self.status is JobStatus.FAILED


@dataclass(slots=True)
class ProcessingSummary:
    """Summary metrics required by the exercise."""

    total_jobs: int
    successful_jobs: int
    failed_jobs: int
    average_successful_job_duration: (
        float | None
    )  # None when there are no successful jobs
    jobs_that_required_retries: list[str] = field(default_factory=list)
    completed_job_durations: dict[str, float] = field(default_factory=dict)
    total_records_processed: int = 0
    malformed_records_count: int = 0
