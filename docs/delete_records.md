# delete_records.py

A command-line tool for deleting records from the Elsevier Pure API based on JSON log files, with optional date filtering, dry-run preview, and detailed deletion logging.

---

## Overview

This script reads one or more JSON log files containing Pure entity records, optionally filters them by modification date, and deletes the matching records via the Pure REST API. Before any deletions are made, the script prints a summary of what will be deleted and asks for interactive confirmation. Results are saved to categorised log files.

---

## Requirements

- Python 3.7+
- `requests`
- `python-dotenv`

Install dependencies:

```bash
pip install requests python-dotenv
```

---

## Authentication

The script requires a Pure API key with **root access**. Store it in a `.env` file in the working directory:

```env
PURE_ROOT_API_KEY=your_api_key_here
```

If the key is not found, the script exits immediately with an error.

---

## Usage

```bash
python delete_records.py [OPTIONS]
```

Either `--log` or `--log-dir` must be provided.

### Example: dry run against a single log file

```bash
python delete_records.py \
  --log ./logs/import_log_2026-03-01.json \
  --dry-run
```

### Example: delete all records from a folder of logs, staging environment

```bash
python delete_records.py \
  --log-dir ./logs/march/ \
  --test True
```

### Example: delete only records modified after a given date, production

```bash
python delete_records.py \
  --log ./logs/import_log.json \
  --after-date 2025-12-01 \
  --test False
```

### Example: custom output directory for deletion logs

```bash
python delete_records.py \
  --log-dir ./logs/ \
  --log-output-dir ./audit/deletions/
```

---

## Arguments

| Argument | Type | Default | Description |
|---|---|---|---|
| `--log` | `str` | `None` | Path to a single JSON log file to process |
| `--log-dir` | `str` | `None` | Path to a directory — all `.json` files inside are processed in sorted order |
| `--log-output-dir` | `str` | See below | Directory to write deletion result logs. Defaults to a `deletion_logs/` folder next to the input file, or inside `--log-dir` if that was used |
| `--after-date` | `str` | `None` | Only delete records with a `modifiedDate` after this value. Accepts `YYYY-MM-DD` or full ISO datetime `YYYY-MM-DDTHH:MM:SSZ`. If omitted, all records in the log are eligible for deletion. |
| `--test` | `bool` | `True` | Target environment. `True` uses UAT staging; `False` uses production. |
| `--dry-run` | flag | `False` | Print a preview of records that would be deleted without making any API calls or asking for confirmation. |

---

## Input Format

Log files must be JSON arrays (or a single JSON object) where each element represents a record to be deleted. At minimum, each record should contain:

```json
{
  "uuid": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "data": "research-outputs",
  "name": "Example Publication Title",
  "modifiedDate": "2025-12-14T10:30:00Z"
}
```

### Required fields

| Field | Description |
|---|---|
| `uuid` | The Pure UUID of the record to delete. Records without a UUID are skipped and logged as failures. |
| `data` | The entity type, used to resolve the API endpoint (see [Supported Entity Types](#supported-entity-types)). Defaults to `research-outputs` if missing. |

### Optional fields

| Field | Description |
|---|---|
| `modifiedDate` | ISO datetime string. Required if using `--after-date` filtering; records without this field are excluded from the filtered set. |
| `name` | Display name used in console output and logs. |
| `endpoint` | Manual override for the API endpoint, used if `data` cannot be resolved. |

---

## Supported Entity Types

The following values are valid for the `data` field in log records:

- `research-outputs`
- `persons`
- `external-persons`
- `organizations`
- `external-organizations`
- `journals`
- `publishers` *(remapped internally to `external-organizations`)*

---

## Deletion Behaviour

For each record, the script makes a `DELETE` request to:

```
<base_url>/<endpoint>/<uuid>
```

Responses are handled as follows:

| HTTP Status | Outcome | Description |
|---|---|---|
| `204` | ✅ Deleted | Record successfully removed from Pure |
| `404` | ⚠️ Not found | Record does not exist in Pure (already deleted or never created). Logged but not treated as a failure. |
| Other | ❌ Failed | Unexpected error. Status code and response body are logged. |

---

## Confirmation Prompt

Unless `--dry-run` is set, the script always prints a preview of records to be deleted and then requires explicit confirmation before proceeding:

```
Are you sure you want to DELETE these 42 records? (yes/no):
```

Typing anything other than `yes` cancels the operation with no changes made.

---

## Output Logs

After deletion, result files are written to the log output directory with a timestamp in the filename:

| File | Contents |
|---|---|
| `deleted_records_<timestamp>.json` | Records successfully deleted, with a `deletedAt` timestamp added |
| `not_found_records_<timestamp>.json` | Records that returned 404 (not present in Pure) |
| `failed_deletions_<timestamp>.json` | Records that failed due to errors, with an `error` field added |

Files are only created if there are records in that category. All files use UTF-8 encoding and 2-space indentation.

---

## API Environments

| Mode | URL |
|---|---|
| UAT / Staging (`--test True`) | `https://galway-staging.elsevierpure.com/ws/api` |
| Production (`--test False`) | `https://research.universityofgalway.ie/ws/api` |

---

## Notes

- Both `--log` and `--log-dir` can be provided at the same time; records from both sources are merged before filtering and deletion.
- When `--log-dir` is used, files are processed in alphabetical order.
- Records missing a `modifiedDate` are silently excluded when `--after-date` is set.
- A `--dry-run` is strongly recommended before running against production to verify the correct records will be targeted.