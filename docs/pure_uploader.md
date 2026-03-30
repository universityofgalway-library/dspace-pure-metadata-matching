# pure_uploader.py

Uploads JSON records to the University of Galway's Pure research information system via its REST API. Supports creating new records and updating existing ones, across multiple data types, in either the staging (UAT) or production environment.

## Requirements

- Python 3.x
- Dependencies: `requests`, `tqdm`, `python-dotenv`
- A `.env` file in the working directory containing a valid Pure API key

```
PURE_ROOT_API_KEY=your_api_key_here
```

## Usage

```bash
# Upload a folder of JSON files (mode inferred from folder name)
python pure_uploader.py --folder path/to/matched_records/ --data research-outputs

# Upload a single JSON file with an explicit mode
python pure_uploader.py --file path/to/record.json --mode create --data persons

# Target the production environment instead of UAT
python pure_uploader.py --folder path/to/matched/ --data research-outputs --test False

# Write logs to a custom directory
python pure_uploader.py --folder path/to/matched/ --data journals --log-dir /custom/log/dir
```

## Arguments

| Argument | Required | Default | Description |
|---|---|---|---|
| `--folder` | One of `--folder` or `--file` | — | Path to a folder of JSON files to upload. Mode is inferred from the folder name (see [Folder Mode](#folder-mode)). |
| `--file` | One of `--folder` or `--file` | — | Path to a single JSON file to upload. Requires `--mode`. |
| `--mode` | Only with `--file` | — | `create` or `update`. Required when using `--file`. |
| `--data` | No | `research-outputs` | The Pure data type to upload. See [Supported Data Types](#supported-data-types). |
| `--test` | No | `True` | `True` targets the UAT environment; `False` targets production. |
| `--log-dir` | No | `./logs/` next to input | Directory where log files are written. |

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

## Folder Mode

When using `--folder`, the upload mode is inferred from the folder name:

- Folder name **starts with `matched`** (case-insensitive) → `update`
- Any other folder name → `create`

The script walks the folder recursively and processes every `.json` file found.

## API Behaviour

The script targets different endpoints depending on the mode:

| Mode | HTTP Method | Endpoint |
|---|---|---|
| `create` | `PUT` | `{base_url}/{data_type}` |
| `update` | `PUT` | `{base_url}/{data_type}/{uuid}` |

Base URLs:

| Environment | URL |
|---|---|
| UAT (staging) | `https://galway-staging.elsevierpure.com/ws/api/` |
| Production | `https://research.universityofgalway.ie/ws/api/` |

HTTP status codes `200` and `201` are treated as success. All other codes are treated as failures and logged.

## Output and Logs

All logs are written to `{log_dir}/uploader_logs/` (created automatically if it does not exist).

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

The `portalUrl`, `handle`, and `portalUrlPROD` fields are only present for `research-outputs`. `portalUrlPROD` is only included when running in test mode (`--test True`) on `update` operations, because newly created records do not yet exist in production.

### Error log — `uploader_errors_<timestamp>.log`

A plain-text file listing one error per line. Each line includes the file path, a record identifier (title, handle, and UUID where available), and the error detail. Written only if errors occurred.

### Failed records — `failed_records_<data_type>_<mode>_<timestamp>.json`

If any records fail (API error or missing `uuid` on update), they are written to a single JSON file in the same directory as the source input. This file can be corrected and re-submitted.

### CSV summary — `created_records_<timestamp>.csv` / `updated_records_<timestamp>.csv`

Generated automatically after a run, but **only for `research-outputs`**. Contains one row per successful record with the following columns:

| Column | Description |
|---|---|
| `name` | Record title |
| `handle` | Handle URL (e.g. `http://hdl.handle.net/...`) |
| `portalUrl` | URL in the target environment |
| `portalUrlPROD` | Production URL (test + update mode only) |

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
- If `PURE_ROOT_API_KEY` is missing from the environment, a warning is printed and the run continues — all API calls will likely fail with authentication errors.
- Progress bars (via `tqdm`) are shown at both the file level and the record level during processing.
- Running against production (`--test False`) without verifying results in UAT first is not recommended.