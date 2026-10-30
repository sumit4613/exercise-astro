# Job Execution Record Processor

A small, modular Python application that processes job execution records from a JSON file, validates inputs,
handles malformed records gracefully, calculates execution durations for completed jobs, and produces an
aggregated summary.

---

## Project Structure

```
coding_exercise/
├── data/
│   ├── sample_valid.json          # Clean sample records with retries
│   └── sample_with_malformed.json # Sample with edge cases and malformed data
├── src/
│   └── job_processor/
│       ├── __init__.py            # Package exports
│       ├── models.py              # Lightweight dataclasses (JobRecord, ProcessingSummary)
│       └── processor.py           # Ingestion, validation, duration calculation & aggregation
├── tests/
│   └── test_processor.py          # Pytest suite
├── main.py                        # CLI entry point
├── pyproject.toml                 # Project metadata & test configuration
└── README.md
```

---

## Setup & Environment

This project uses [`uv`](https://docs.astral.sh/uv/) for virtual environment and dependency management.

```bash
# 1. Create a virtual environment and install dependencies
uv sync

# 2. Activate the virtual environment (optional)
source .venv/bin/activate
```

Alternatively, you can run all commands directly via `uv run` without activating the environment.

---

## How to Run

### 1. Running the CLI
Run against any JSON file:

```bash
# Using uv:
uv run python main.py data/sample_valid.json

# Or using standard python with activated .venv:
python3 main.py data/sample_valid.json
```

**Output:**
```
==========================================
         JOB PROCESSING RESULTS           
==========================================

Completed Job Durations:
  • extract_customers          : 210.00s
  • sync_to_snowflake          : 70.00s
  • train_recommendation_model : 1800.00s
  • transform_orders           : 240.00s

Summary:
  • Total jobs                     : 4
  • Successful jobs                : 3
  • Failed jobs                    : 1
  • Average successful-job duration: 750.00s
  • Jobs that required retries     : sync_to_snowflake, transform_orders
==========================================
```

### 2. Machine-Readable JSON Output
Pass `--json` to output results as JSON:

```bash
uv run python main.py data/sample_valid.json --json
# or: python3 main.py data/sample_valid.json --json
```

### 3. Handling Malformed Records
Run against a dataset with corrupt or invalid entries:

```bash
uv run python main.py data/sample_with_malformed.json
# or: python3 main.py data/sample_with_malformed.json
```
Malformed records (missing fields, clock skew, invalid dates, unknown statuses) are logged with line indices to `stderr` and skipped. Valid records continue to be processed.

---

## Running Automated Tests

Run the test suite using `pytest`:

```bash
pytest -v
# or using uv:
uv run pytest -v
```

---

## Assumptions and known limitations

- Each record is one execution **attempt**. A **job** is a unique `job_id`; its outcome
  and duration come from its **latest attempt** (highest `attempt`, then latest `started_at`).
- A job required retries if its latest `attempt` > 1.
- Only `success` and `failed` are terminal and require `finished_at`. Jobs whose latest
  attempt is `running` or `cancelled` count toward `total_jobs` but not toward
  successful/failed, so `total_jobs` can exceed their sum.
- Duration is `finished_at - started_at` of the latest attempt, not end-to-end time across retries.
- Average successful duration is `None` (printed as `n/a`) when no jobs succeeded.
- Common status synonyms (`completed`, `error`, `in_progress`, ...) are normalized to canonical statuses.
- Malformed records are skipped; index and reason go to stderr. Exit code is 1 only
  for an unusable file (missing, invalid JSON, not a list), otherwise 0.
- Naive timestamps are treated as UTC.
- Duplicate `(job_id, attempt)` rows are not detected; the later `started_at` wins.
- The whole file is loaded into memory.

---

## Items That Can Be Improved

1. **Streaming Input for Large Files:**
   The entire JSON file is currently loaded into memory with `json.load()`. Supporting line-delimited JSON (`JSONL`) or streaming parsers would allow processing large files with constant $O(1)$ memory.
2. **Duplicate Detection:**
   If multiple records share the same `(job_id, attempt)`, the code currently lets the later timestamp win. A dedicated deduplication step could explicitly resolve or reject conflicting duplicate records.
3. **Malformed Record Export:**
   Malformed records are currently printed as warnings to `stderr`. Adding a CLI flag to export them into a structured JSON file would make it easier to inspect and debug invalid input.
4. **Visibility into Non-Terminal States:**
   The summary currently reports `successful_jobs` and `failed_jobs`. Adding explicit counts for `running` and `cancelled` jobs would make the summary breakdown complete.
5. **Strict Mode:**
   Allowing users to enable a strict mode (fail-fast instead of skip) would make the processor more adaptable to different data sources.
