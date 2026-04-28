# pure_uploader.py

Uploads JSON records to the University of Galway's Pure research information system via its REST API. Supports creating new records and updating existing ones, across multiple data types, in either the staging (UAT) or production environment.

## Requirements

- Python 3.x
- Dependencies: `requests`, `tqdm`, `python-dotenv`
- A `.env` file in the working directory containing valid Pure API keys

```
PURE_ROOT_API_KEY=your_production_api_key_here
PURE_ROOT_API_KEY_TEST=your_uat_api_key_here
```

`PURE_ROOT_API_KEY` is used by default (production). `PURE_ROOT_API_KEY_TEST` is used when `--test` is set. If the required variable is missing from the environment, a warning is printed and the run continues — all API calls will likely fail with authentication errors.

## Usage

```bash
# Upload a folder of JSON files in create mode
python pure_uploader.py --folder path/to/unmatched_records/ --mode create --data research-outputs

# Upload a folder of JSON files in update mode
python pure_uploader.py --folder path/to/matched_records/ --mode update --data research-outputs

# Upload a single JSON file
python pure_uploader.py --file path/to/record.json --mode create --data persons

# Target the UAT environment instead of production
python pure_uploader.py --folder path/to/matched/ --mode update --data research-outputs --test

# Write logs to a custom directory
python pure_uploader.py --folder path/to/matched/ --mode update --data journals --log-dir /custom/log/dir
```

## Arguments

| Argument | Required | Default | Description |
|---|---|---|---|
| `--folder` | One of `--folder` or `--file` | — | Path to a folder of JSON files to upload. The folder is walked recursively; all `.json` files found are processed. |
| `--file` | One of `--folder` or `--file` | — | Path to a single JSON file to upload. |
| `--mode` | **Always required** | — | `create` or `update`. Required regardless of whether `--folder` or `--file` is used. |
| `--data` | No | `research-outputs` | The Pure data type to upload. See [Supported Data Types](#supported-data-types). |
| `--test` | No | *(omit for production)* | Flag — include to target the UAT environment. Omit to target production. |
| `--log-dir` | No | `./logs/` next to input | Parent directory for log output. Logs are written to `<log-dir>/uploader_logs/`. |

## Supported Data Types

| Value | Description |
|---|---|
| `research-outputs` | Publications and research outputs |
| `persons` | Internal university persons |
| `external-persons` | External persons |
| `journals` | Journal records |
| `events` | Events |
| `organizations` | Internal organisations |
| `external-organizations` | External organisations |
| `publishers` | Publishers |

## Input Format

Input files must be valid JSON. Each file may contain either a single record object or an array of record objects.

**Create** records do not require a `uuid` field — Pure assigns one on creation.

**Update** records must include a top-level `uuid` field identifying the record to update:

```json
[
  {
    "uuid": "abc-123-...",
    "title": { "value": "Updated Title" },
    ...
  }
]
```

Records missing a `uuid` in update mode are treated as failures and written to the failed records file.

## API Behaviour

The script targets different endpoints depending on the mode:

| Mode | HTTP Method | Endpoint |
|---|---|---|
| `create` | `PUT` | `{base_url}/{data_type}` |
| `update` | `PUT` | `{base_url}/{data_type}/{uuid}` |

Base URLs:

| Environment | URL |
|---|---|
| UAT (staging) — `--test` | `https://galway-staging.elsevierpure.com/ws/api/` |
| Production — default | `https://research.universityofgalway.ie/ws/api/` |

HTTP status codes `200` and `201` are treated as success. All other codes are treated as failures and logged.

## Output and Logs

All logs are written to `<log-dir>/uploader_logs/` (created automatically if it does not exist). File names include a full timestamp in `YYYY-MM-DD_HH-MM-SS` format.

### Success log — `created_records_<timestamp>.json` / `updated_records_<timestamp>.json`

One entry is appended per successfully created or updated record:

```json
{
  "data": "research-outputs",
  "name": "Article Title",
  "uuid": "abc-123",
  "createdDate": "2024-01-01",
  "modifiedDate": "2024-06-01",
  "createdBy": "system",
  "modifiedBy": "system",
  "success": true,
  "portalUrl": "https://...",
  "handle": "http://hdl.handle.net/...",
  "portalUrlPROD": "https://research.universityofgalway.ie/en/publications/abc-123"
}
```

The `portalUrl`, `handle`, and `portalUrlPROD` fields are only present for `research-outputs`. `portalUrlPROD` is only included when running in UAT mode (`--test`) on `update` operations, because newly created records do not yet exist in production.

> **Note:** The success log uses `"data"` (not `"type"`) as the field name for the data type. If you use this log with `patch_records.py --workflow-from-log`, note that `patch_records.py` filters on a `"type"` field — the two are not currently compatible without pre-processing the log.

### Error log — `uploader_errors_<timestamp>.log`

A plain-text file listing one error per line. Each line includes the file path, a record identifier (title, handle, and UUID where available), and the error detail. Written only if errors occurred.

### Failed records — `failed_records_<data_type>_<mode>_<timestamp>.json`

If any records fail (API error, exception, or missing `uuid` on update), they are written to a single JSON file. For `--folder` uploads the file is placed in the **parent directory of the input folder**; for `--file` uploads it is placed in the same directory as the input file. This file can be corrected and re-submitted.

### CSV summary — `created_records_<timestamp>.csv` / `updated_records_<timestamp>.csv`

Generated automatically after a run, but **only for `research-outputs`**. Contains one row per successful record with the following columns:

| Column | Description |
|---|---|
| `name` | Record title |
| `handle` | Handle URL (e.g. `http://hdl.handle.net/...`) |
| `portalUrl` | URL in the target environment |
| `portalUrlPROD` | Production URL (UAT + update mode only) |

## Localized Field Handling

Pure API responses use several field formats. The script normalizes all of them:

| Format | Example | Resolved as |
|---|---|---|
| Direct string | `"name": "Value"` | `"Value"` |
| Value dict | `"title": {"value": "Value"}` | `"Value"` |
| Language dict | `"name": {"en_IE": "Value"}` | `"Value"` |

For language dicts, the preference order is `en_IE` → `en_GB` → `en_US` → first available value.

## Notes

- The script uses a persistent `requests.Session` for all API calls, with a 60-second timeout per request.
- Progress bars (via `tqdm`) are shown at both the file level and the record level during processing.
- Running against production (default, without `--test`) without verifying results in UAT first is not recommended.