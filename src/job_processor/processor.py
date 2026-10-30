"""
Job records processing and aggregation logic.
"""

import json
import logging
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import JobRecord, JobStatus, ProcessingSummary

logger = logging.getLogger(__name__)

# Common synonyms are normalized to one canonical status at parse time.
_STATUS_ALIASES: dict[str, JobStatus] = {
    "success": JobStatus.SUCCESS,
    "successful": JobStatus.SUCCESS,
    "completed": JobStatus.SUCCESS,
    "failed": JobStatus.FAILED,
    "failure": JobStatus.FAILED,
    "error": JobStatus.FAILED,
    "running": JobStatus.RUNNING,
    "in_progress": JobStatus.RUNNING,
    "cancelled": JobStatus.CANCELLED,
}


def parse_datetime(val: Any) -> datetime:
    """Parse an ISO 8601 string, normalizing naive datetimes to UTC."""
    if isinstance(val, datetime):
        dt = val
    elif isinstance(val, str):
        dt = datetime.fromisoformat(val)  # Python 3.11+ accepts 'Z'
    else:
        raise TypeError(f"Expected datetime or string, got {type(val).__name__}")

    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)

    try:
        return dt.astimezone(UTC)
    except OverflowError as exc:
        raise ValueError(f"Datetime offset out of supported range (1..9999): {val!r}") from exc


def validate_and_build_record(raw: dict[str, Any]) -> JobRecord:
    """Validate a single raw record dict and construct a JobRecord."""
    if not isinstance(raw, dict):
        raise TypeError(f"Record must be a JSON object, got {type(raw).__name__}")

    # job_id
    raw_job_id = raw.get("job_id")
    if raw_job_id is None or str(raw_job_id).strip() == "":
        raise ValueError("Missing or empty 'job_id'")
    job_id = str(raw_job_id).strip()

    # status
    raw_status = raw.get("status")
    if not isinstance(raw_status, str) or not raw_status.strip():
        raise ValueError(f"Invalid status: {raw_status!r}")
    status = _STATUS_ALIASES.get(raw_status.strip().lower())
    if status is None:
        raise ValueError(f"Unrecognized status: {raw_status!r}")

    # started_at
    if raw.get("started_at") is None:
        raise ValueError("Missing 'started_at'")
    started_at = parse_datetime(raw["started_at"])

    # finished_at
    finished_at = None
    if raw.get("finished_at") is not None:
        finished_at = parse_datetime(raw["finished_at"])
        if finished_at < started_at:
            raise ValueError(
                f"finished_at ({finished_at}) is earlier than started_at ({started_at})"
            )

    # Terminal statuses must have finished_at, otherwise there is no duration.
    if status.is_terminal and finished_at is None:
        raise ValueError(f"Status {status.value!r} requires 'finished_at'")

    # attempt: strict positive int (rejects bool, floats, strings)
    raw_attempt = raw.get("attempt", 1)
    if (
        isinstance(raw_attempt, bool)
        or not isinstance(raw_attempt, int)
        or raw_attempt < 1
    ):
        raise ValueError(f"Invalid attempt value: {raw_attempt!r}")

    return JobRecord(
        job_id=job_id,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        attempt=raw_attempt,
    )


class JobProcessor:
    """Processes, validates, and summarizes job execution records."""

    def __init__(self) -> None:
        self.malformed_records: list[tuple[int, Any, str]] = []

    def parse_records(self, raw_items: list[Any]) -> list[JobRecord]:
        """Validate raw items, skipping malformed records gracefully."""
        if not isinstance(raw_items, list):
            raise ValueError(
                f"Expected top-level JSON list, got {type(raw_items).__name__}"
            )

        self.malformed_records = []  # reset so a reused processor doesn't leak state
        valid_records: list[JobRecord] = []
        for idx, item in enumerate(raw_items):
            try:
                valid_records.append(validate_and_build_record(item))
            except (
                ValueError,
                TypeError,
            ) as exc:  # validation errors only, not our own bugs
                logger.warning("Skipping malformed record at index %d: %s", idx, exc)
                self.malformed_records.append((idx, item, str(exc)))
        return valid_records

    def process(self, raw_items: list[Any]) -> ProcessingSummary:
        """Validate records, then compute per-job outcomes and summary metrics."""
        records = self.parse_records(raw_items)

        grouped: dict[str, list[JobRecord]] = defaultdict(list)
        for r in records:
            grouped[r.job_id].append(r)

        successful_jobs = 0
        failed_jobs = 0
        retried_jobs: list[str] = []
        successful_durations: list[float] = []
        completed_durations: dict[str, float] = {}

        for job_id, attempts in grouped.items():
            # One rule: a job's outcome AND duration come from its latest attempt.
            latest = max(attempts, key=lambda a: (a.attempt, a.started_at))

            if latest.attempt > 1:
                retried_jobs.append(job_id)

            # Only terminal statuses (success/failed) count; running/cancelled are
            # included in total_jobs but not in successful/failed.
            if latest.status.is_terminal:
                completed_durations[job_id] = latest.duration_seconds
                if latest.is_successful:
                    successful_jobs += 1
                    successful_durations.append(latest.duration_seconds)
                else:
                    failed_jobs += 1

        avg_duration = (
            round(sum(successful_durations) / len(successful_durations), 2)
            if successful_durations
            else None
        )

        return ProcessingSummary(
            total_jobs=len(grouped),
            successful_jobs=successful_jobs,
            failed_jobs=failed_jobs,
            average_successful_job_duration=avg_duration,
            jobs_that_required_retries=sorted(retried_jobs),
            completed_job_durations=completed_durations,
            total_records_processed=len(records),
            malformed_records_count=len(self.malformed_records),
        )

    def process_file(self, file_path: str | Path) -> ProcessingSummary:
        """Read a JSON file and process its records."""
        path = Path(file_path)
        if not path.is_file():
            raise FileNotFoundError(f"File not found: {path}")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)  # JSONDecodeError is a ValueError
        return self.process(data)
