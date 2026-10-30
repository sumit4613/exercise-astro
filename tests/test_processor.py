"""
Tests for job processor core behavior, validation, and calculations.
"""

import json
from pathlib import Path

import pytest

from job_processor.models import JobStatus
from job_processor.processor import (
    JobProcessor,
    parse_datetime,
    validate_and_build_record,
)
from main import main


def test_parse_datetime_iso_and_utc():
    # UTC with Z
    dt1 = parse_datetime("2026-10-30T10:00:00Z")
    assert dt1.tzinfo is not None

    # Naive ISO string gets normalized to UTC
    dt2 = parse_datetime("2026-10-30T10:00:00")
    assert dt2.tzinfo is not None
    assert dt1 == dt2


@pytest.mark.parametrize("iso_str", [
    "9999-12-31T23:59:00-05:00",  # Upper-bound: negative offset pushes year 9999 to 10000 UTC
    "0001-01-01T01:00:00+05:00",  # Lower-bound: positive offset pushes year 0001 to year 0000 UTC
])
def test_parse_datetime_overflow_raises_value_error(iso_str):
    with pytest.raises(ValueError, match="Datetime offset out of supported range"):
        parse_datetime(iso_str)


def test_validate_record_valid():
    raw = {
        "job_id": "job-1",
        "status": "success",
        "started_at": "2026-10-30T10:00:00Z",
        "finished_at": "2026-10-30T10:02:30Z",
        "attempt": 1,
    }
    rec = validate_and_build_record(raw)
    assert rec.job_id == "job-1"
    assert rec.is_successful is True
    assert rec.duration_seconds == 150.0


def test_validate_record_clock_skew_raises():
    raw = {
        "job_id": "job-skew",
        "status": "success",
        "started_at": "2026-10-30T10:05:00Z",
        "finished_at": "2026-10-30T10:00:00Z",
    }
    with pytest.raises(ValueError, match="earlier than started_at"):
        validate_and_build_record(raw)


@pytest.mark.parametrize("raw_record, expected_match", [
    ({"status": "success", "started_at": "2026-10-30T10:00:00Z"}, "Missing or empty 'job_id'"),
    ({"job_id": "   ", "status": "success", "started_at": "2026-10-30T10:00:00Z"}, "Missing or empty 'job_id'"),
    ({"job_id": "job-1", "status": "success"}, "Missing 'started_at'"),
    ({"job_id": "job-1", "started_at": "2026-10-30T10:00:00Z"}, "Invalid status"),
    ({"job_id": "job-1", "status": "   ", "started_at": "2026-10-30T10:00:00Z"}, "Invalid status"),
])
def test_validate_record_missing_fields_raises(raw_record, expected_match):
    with pytest.raises(ValueError, match=expected_match):
        validate_and_build_record(raw_record)


@pytest.mark.parametrize("alias, expected_status", [
    ("success", JobStatus.SUCCESS),
    ("successful", JobStatus.SUCCESS),
    ("completed", JobStatus.SUCCESS),
    ("failed", JobStatus.FAILED),
    ("failure", JobStatus.FAILED),
    ("error", JobStatus.FAILED),
    ("running", JobStatus.RUNNING),
    ("in_progress", JobStatus.RUNNING),
    ("cancelled", JobStatus.CANCELLED),
])
def test_status_synonyms_normalization(alias, expected_status):
    raw = {"job_id": "j", "status": alias, "started_at": "2026-10-30T10:00:00Z"}
    if expected_status.is_terminal:
        raw["finished_at"] = "2026-10-30T10:01:00Z"
    rec = validate_and_build_record(raw)
    assert rec.status is expected_status


def test_graceful_malformed_records_handling():
    data = [
        # Valid
        {
            "job_id": "job-valid",
            "status": "success",
            "started_at": "2026-10-30T10:00:00Z",
            "finished_at": "2026-10-30T10:01:00Z",
            "attempt": 1,
        },
        # Corrupt: not a dict
        "corrupted_primitive_string",
        # Corrupt: bad date
        {
            "job_id": "job-bad-date",
            "status": "success",
            "started_at": "invalid-date-string",
        },
        # Corrupt: negative attempt
        {
            "job_id": "job-neg-attempt",
            "status": "success",
            "started_at": "2026-10-30T10:00:00Z",
            "attempt": -1,
        },
        # Corrupt: unrecognized status
        {
            "job_id": "job-bad-status",
            "status": "invalid_xyz",
            "started_at": "2026-10-30T10:00:00Z",
        },
    ]

    processor = JobProcessor()
    summary = processor.process(data)

    assert summary.total_jobs == 1
    assert summary.successful_jobs == 1
    assert summary.malformed_records_count == 4
    assert summary.total_records_processed == 1


def test_completed_job_durations_and_summary_metrics_with_retries():
    data = [
        # Job 1: Attempt 1 failed (30s), Attempt 2 succeeded (50s)
        {
            "job_id": "job-etl-01",
            "status": "failed",
            "started_at": "2026-10-30T10:00:00Z",
            "finished_at": "2026-10-30T10:00:30Z",
            "attempt": 1,
        },
        {
            "job_id": "job-etl-01",
            "status": "success",
            "started_at": "2026-10-30T10:01:00Z",
            "finished_at": "2026-10-30T10:01:50Z",
            "attempt": 2,
        },
        # Job 2: Attempt 1 failed (20s), Attempt 2 failed (20s) -> Terminal state FAILED
        {
            "job_id": "job-etl-02",
            "status": "failed",
            "started_at": "2026-10-30T10:00:00Z",
            "finished_at": "2026-10-30T10:00:20Z",
            "attempt": 1,
        },
        {
            "job_id": "job-etl-02",
            "status": "failed",
            "started_at": "2026-10-30T10:01:00Z",
            "finished_at": "2026-10-30T10:01:20Z",
            "attempt": 2,
        },
        # Job 3: Single attempt success (70s)
        {
            "job_id": "job-etl-03",
            "status": "completed",
            "started_at": "2026-10-30T10:00:00Z",
            "finished_at": "2026-10-30T10:01:10Z",
            "attempt": 1,
        },
    ]

    processor = JobProcessor()
    summary = processor.process(data)

    # Requirement 3: Completed job durations
    assert summary.completed_job_durations["job-etl-01"] == 50.0
    assert summary.completed_job_durations["job-etl-02"] == 20.0
    assert summary.completed_job_durations["job-etl-03"] == 70.0

    # Requirement 4: Summary metrics
    assert summary.total_jobs == 3
    assert summary.successful_jobs == 2  # job-etl-01, job-etl-03
    assert summary.failed_jobs == 1  # job-etl-02
    assert summary.jobs_that_required_retries == ["job-etl-01", "job-etl-02"]

    # Successful durations: job-etl-01 (50s) + job-etl-03 (70s) -> avg = 60.0s
    assert summary.average_successful_job_duration == 60.0


def test_empty_records():
    processor = JobProcessor()
    summary = processor.process([])
    assert summary.total_jobs == 0
    assert summary.successful_jobs == 0
    assert summary.failed_jobs == 0
    assert summary.average_successful_job_duration is None
    assert summary.jobs_that_required_retries == []
    assert summary.completed_job_durations == {}


def test_cli_end_to_end(tmp_path: Path, capsys):
    data = [
        {
            "job_id": "job-1",
            "status": "success",
            "started_at": "2026-10-30T10:00:00Z",
            "finished_at": "2026-10-30T10:02:00Z",
            "attempt": 1,
        }
    ]
    test_file = tmp_path / "test.json"
    test_file.write_text(json.dumps(data), encoding="utf-8")

    # Test text output
    exit_code = main([str(test_file)])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "Completed Job Durations:" in captured.out
    assert "job-1" in captured.out
    assert "120.00s" in captured.out
    assert "Total jobs                     : 1" in captured.out
    assert "Successful jobs                : 1" in captured.out
    assert "Average successful-job duration: 120.00s" in captured.out

    # Test JSON output
    exit_code_json = main([str(test_file), "--json"])
    assert exit_code_json == 0
    captured_json = capsys.readouterr()
    parsed = json.loads(captured_json.out)
    assert parsed["total_jobs"] == 1
    assert parsed["average_successful_job_duration"] == 120.0
    assert parsed["completed_job_durations"]["job-1"] == 120.0


@pytest.mark.parametrize("status", ["success", "failed"])
def test_terminal_status_requires_finished_at(status):
    raw = {"job_id": "j", "status": status, "started_at": "2026-10-30T10:00:00Z"}
    with pytest.raises(ValueError, match="requires 'finished_at'"):
        validate_and_build_record(raw)


@pytest.mark.parametrize("bad_attempt", [True, 2.9, "3", 0, -1])
def test_attempt_must_be_positive_int(bad_attempt):
    raw = {
        "job_id": "j",
        "status": "success",
        "started_at": "2026-10-30T10:00:00Z",
        "finished_at": "2026-10-30T10:01:00Z",
        "attempt": bad_attempt,
    }
    with pytest.raises(ValueError, match="Invalid attempt"):
        validate_and_build_record(raw)


def test_failed_latest_attempt_does_not_borrow_earlier_success_duration():
    data = [
        {
            "job_id": "j",
            "status": "success",
            "attempt": 1,
            "started_at": "2026-10-30T10:00:00Z",
            "finished_at": "2026-10-30T10:00:50Z",
        },
        {
            "job_id": "j",
            "status": "failed",
            "attempt": 2,
            "started_at": "2026-10-30T10:05:00Z",
            "finished_at": "2026-10-30T10:05:20Z",
        },
    ]
    summary = JobProcessor().process(data)
    assert summary.failed_jobs == 1
    assert summary.successful_jobs == 0
    assert summary.completed_job_durations["j"] == 20.0  # the latest attempt's
    assert summary.average_successful_job_duration is None


@pytest.mark.parametrize("status", ["running", "in_progress", "cancelled"])
def test_running_job_counts_in_total_but_not_success_or_failed(status):
    data = [{"job_id": "j", "status": status, "started_at": "2026-10-30T10:00:00Z"}]
    summary = JobProcessor().process(data)
    assert (summary.total_jobs, summary.successful_jobs, summary.failed_jobs) == (
        1,
        0,
        0,
    )
    assert summary.completed_job_durations == {}


def test_cli_prints_na_when_no_successful_jobs(tmp_path, capsys):
    data = [
        {
            "job_id": "j",
            "status": "failed",
            "started_at": "2026-10-30T10:00:00Z",
            "finished_at": "2026-10-30T10:00:10Z",
        }
    ]
    f = tmp_path / "f.json"
    f.write_text(json.dumps(data), encoding="utf-8")
    assert main([str(f)]) == 0
    assert "Average successful-job duration: n/a" in capsys.readouterr().out
