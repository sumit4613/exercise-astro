#!/usr/bin/env python3
"""
CLI entry point for processing job execution records.

Usage:
    python main.py <path-to-json-file> [--json]
"""

import argparse
import json
import logging
import sys
from dataclasses import asdict
from pathlib import Path

# Add src to sys.path so the package can be imported directly
sys.path.insert(0, str(Path(__file__).parent / "src"))

from job_processor import JobProcessor


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(
        description="Process job execution records from a JSON file."
    )
    parser.add_argument("file_path", type=str, help="Path to input JSON file")
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output summary and completed job durations as JSON",
    )
    args = parser.parse_args(argv)

    processor = JobProcessor()
    try:
        summary = processor.process_file(args.file_path)
    except (
        OSError,
        ValueError,
    ) as exc:  # file problems, invalid JSON, non-list top level
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.json_output:
        print(json.dumps(asdict(summary), indent=2))
        return 0

    avg = summary.average_successful_job_duration
    avg_text = f"{avg:.2f}s" if avg is not None else "n/a"

    print("\n==========================================")
    print("         JOB PROCESSING RESULTS           ")
    print("==========================================")

    print("\nCompleted Job Durations:")
    if summary.completed_job_durations:
        width = max(len(j) for j in summary.completed_job_durations)
        for job_id, dur in sorted(summary.completed_job_durations.items()):
            print(f"  • {job_id:<{width}} : {dur:.2f}s")
    else:
        print("  (No completed jobs found)")

    print("\nSummary:")
    print(f"  • Total jobs                     : {summary.total_jobs}")
    print(f"  • Successful jobs                : {summary.successful_jobs}")
    print(f"  • Failed jobs                    : {summary.failed_jobs}")
    print(f"  • Average successful-job duration: {avg_text}")
    print(
        f"  • Jobs that required retries     : {', '.join(summary.jobs_that_required_retries) or 'None'}"
    )
    if summary.malformed_records_count > 0:
        print(
            f"  • Malformed records skipped      : {summary.malformed_records_count} (details on stderr)"
        )
    print("==========================================\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
