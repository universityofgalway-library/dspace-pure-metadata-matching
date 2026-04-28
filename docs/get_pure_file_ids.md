# get_pure_file_ids.py

Reads a CSV log file, queries the Pure API for each record's research output using its `pure_uuid`, extracts `fileId` and `fileName` from the first `FileElectronicVersion` found, and writes an enriched CSV with two new columns appended.

---

## Requirements

- Python 3.10+
- Dependencies: `requests`, `python-dotenv`

```bash
pip install requests python-dotenv
```

A `.env` file in the working directory containing:

```
PURE_ROOT_API_KEY_TEST=<your-uat-api-key>
PURE_ROOT_API_KEY=<your-prod-api-key>
```

If the required key is not found, the script exits with an error.

---

## Usage

```bash
# UAT (default)
python get_pure_file_ids.py <input_csv>

# UAT (explicit)
python get_pure_file_ids.py <input_csv> --test

# Production
python get_pure_file_ids.py <input_csv> --no-test
```

### Arguments

| Argument | Required | Default | Description |
|---|---|---|---|
| `input_csv` | Yes | — | Path to the source CSV file. |
| `--test` / `--no-test` | No | `--test` (UAT) | Target environment. Pass `--no-test` to use production. |

---

## Input Format

A CSV file with at minimum a `pure_uuid` column. All other columns are preserved as-is in the output. Rows where `pure_uuid` is empty are skipped (the two new columns are written as empty strings).

---

## Behaviour

For each row the script:

1. Reads the `pure_uuid` value.
2. Makes a `GET` request to `{base_url}research-outputs/{uuid}`.
3. Iterates over `electronicVersions` in the response and returns the `fileId` and `fileName` from the first entry with `typeDiscriminator == "FileElectronicVersion"`.
4. If no such entry exists, or the request fails, both values are returned as empty strings.
5. Appends `pure_file_id` and `pure_file_name` to the row and writes it to the output CSV.

A 100 ms delay is applied between API requests to avoid overloading the API.

---

## API Environments

| Flag | Environment | Base URL |
|---|---|---|
| `--test` (default) | UAT | `https://galway-staging.elsevierpure.com/ws/api/` |
| `--no-test` | Production | `https://research.universityofgalway.ie/ws/api/` |

---

## Output

The enriched CSV is written to `updated_<original_filename>` in the same directory as the input file. The input file is never modified. Two columns are appended:

| Column | Description |
|---|---|
| `pure_file_id` | `fileId` from the first `FileElectronicVersion` found, or empty string |
| `pure_file_name` | `fileName` from the first `FileElectronicVersion` found, or empty string |

---

## Console Output

```
Environment : UAT
Base URL    : https://galway-staging.elsevierpure.com/ws/api/
Input  : /path/to/input.csv
Output : /path/to/updated_input.csv
[1] pure_uuid=abc-123 ... fileId=MDAxOTAxYjI5  fileName=my_paper.pdf
[2] pure_uuid=def-456 ... fileId=—  fileName=—
[3] pure_uuid= ... [SKIP] no uuid fileId=—  fileName=—

Done. Enriched file written to:
  /path/to/updated_input.csv
```

HTTP errors and request failures are printed to stderr with the affected UUID. Processing continues for all remaining rows.