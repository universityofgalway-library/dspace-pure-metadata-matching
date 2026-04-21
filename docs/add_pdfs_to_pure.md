# upload_pdfs_to_pure.py

Downloads PDFs from a DSpace repository, uploads them to Pure's file-upload endpoint, and immediately attaches them to the matching Pure research output as a `FileElectronicVersion`. No other fields on the Pure record are modified.

---

## Requirements

```
requests
python-dotenv
tqdm
python-dateutil
```

Install with:
```bash
pip install requests python-dotenv tqdm python-dateutil
```

---

## .env file

```env
PURE_ROOT_API_KEY_TEST=your_uat_key_here
PURE_ROOT_API_KEY=your_production_key_here
```

---

## Inputs

| Argument | Description |
|---|---|
| `--dspace-csv` | Path to the enriched DSpace CSV export. Must contain the columns listed below. |
| `--pure-json` | Path to a Pure research-outputs JSON export (list of record dicts). |

**Required DSpace CSV columns:**

| Column | Description |
|---|---|
| `pdf_handle_paths` | Semicolon-separated handle-based paths to PDFs, e.g. `/10379/4728/1/file.pdf` or `/10379/4728/1/cover.pdf ; /10379/4728/2/fulltext.pdf`. Rows without this value are skipped. All paths are processed. |
| `handle` | The item handle, e.g. `http://hdl.handle.net/10379/4728`. Used as the primary handle source for matching and for the file references output. |
| `uuid` | DSpace item UUID. |
| `dc.identifier.uri` | Semicolon-separated URIs (handles and DOIs). Used as fallback for matching when `handle` is empty. |
| `dc.identifier.doi` | Publisher DOI. Used as the first matching strategy. |
| `dc.rights` | Rights/license string (e.g. `CC BY`). Mapped to a Pure license URI. |
| `dc.date.embargo` | Embargo end date. Determines access type (`open` or `embargoed`). |
| `dc.description.embargo` | Alternative embargo end date field. |
| `dc.title` | Record title. Used for logging only. |

---

## Options

| Flag | Default | Description |
|---|---|---|
| `--test` / `--no-test` | `--test` | Use UAT (`--test`) or Production (`--no-test`) API and bitstream base URL. |
| `--source` | `dspace` | Where to get PDFs from: `dspace` (download from DSpace) or `local` (read from disk). |
| `--pdf-dir` | `./downloaded_dspace_pdfs` | If `--source dspace`: directory to save PDFs locally (only used with `--save-locally`). If `--source local`: directory to read PDFs from. Must exist when using `--source local`. |
| `--save-locally` | `False` | Only applicable when `--source dspace`. Write downloaded PDFs to disk as a backup. When saving, each file is written to disk and buffered in memory simultaneously — the same data is used for the Pure upload without a second read. If a file already exists locally it is reused rather than re-downloaded. |
| `--log-dir` | `./pdf_upload_logs` | Directory where all log files are written. |
| `--skip-existing` / `--no-skip-existing` | `--skip-existing` | Skip individual files that already exist in Pure as a `FileElectronicVersion` with the **same filename and size**. Applied per file when a record has multiple PDFs. If filename matches but size differs or cannot be determined, the file is uploaded and appended alongside the existing one. |
| `--dry-run` | `False` | Match records and report what would be done without making any API calls. |

---

## Usage

**Standard run against UAT (download from DSpace):**
```bash
python upload_pdfs_to_pure.py \
  --dspace-csv ./dspace_data/export.csv \
  --pure-json  ./pure_research_outputs/outputs.json
```

**Production run, saving PDFs locally:**
```bash
python upload_pdfs_to_pure.py \
  --dspace-csv ./dspace_data/export.csv \
  --pure-json  ./pure_research_outputs/outputs.json \
  --no-test \
  --save-locally \
  --pdf-dir ./downloaded_pdfs
```

**Upload from local directory instead of DSpace:**
```bash
python upload_pdfs_to_pure.py \
  --dspace-csv ./dspace_data/export.csv \
  --pure-json  ./pure_research_outputs/outputs.json \
  --source local \
  --pdf-dir ./downloaded_pdfs
```

**Dry run to check matches before uploading:**
```bash
python upload_pdfs_to_pure.py \
  --dspace-csv ./dspace_data/export.csv \
  --pure-json  ./pure_research_outputs/outputs.json \
  --dry-run
```

---

## How it works

For each DSpace row that has a `pdf_handle_paths` value, the script:

1. **Filters** to rows in a Publications collection with a `pdf_handle_paths` value.
2. **Matches** the row to a Pure record using (in priority order): Publisher DOI → Repository DOI → Handle. The `handle` column is checked first for handle matching; `dc.identifier.uri` is used as a fallback. Lookup is O(1) via a pre-built index.
3. **Parses all PDF paths** from `pdf_handle_paths` (semicolon-separated). Each path is processed independently through the steps below.
4. **Checks for duplicates** (if `--skip-existing`): skips a file only if a `FileElectronicVersion` with the **same filename and size** already exists in Pure. When a match is found, the script also checks whether the existing FileEV's metadata is up to date — specifically `licenseType`, `accessType`, `versionType`, `visibleOnPortalDate`, and `embargoPeriod`. If any field is missing or differs from the DSpace-derived values, the Pure record is PUTted with the corrected metadata without re-uploading the file. If size differs or is unknown, the file is uploaded and appended alongside the existing version.
5. **Gets the PDF** — either streams it from DSpace (`--source dspace`) or reads it from `--pdf-dir` (`--source local`). When `--save-locally` is set, each file is written to disk and a `BytesIO` buffer simultaneously in a single pass; the buffer is used for the Pure upload with no second read or file re-open. When using `--source local`, filenames are matched using the URL-decoded form; if not found, the script also tries the URL-encoded variant as a fallback.
6. **Uploads** each PDF to Pure's temporary file-upload endpoint.
7. **PUTs** the Pure record immediately with the new `FileElectronicVersion` appended to `electronicVersions`.

Steps 6 and 7 happen back-to-back for each file to stay well within Pure's **2-hour temporary file expiry window**. System/read-only fields (`pureId`, `createdBy`, `modifiedDate`, etc.) are stripped from the record before the PUT.

---

## Logging

All log files are written to `--log-dir` and timestamped with the run start time.

| File | Contents |
|---|---|
| `run_<timestamp>.log` | Full console output for the entire run. |
| `results_<timestamp>.json` | One entry per processed DSpace row with all fields, status, and error detail. |
| `success_<timestamp>.csv` | Rows where the PDF was uploaded and the PUT succeeded. |
| `failed_<timestamp>.csv` | Rows where the PDF upload or PUT failed. |
| `skipped_<timestamp>.csv` | Rows with no Pure match, or already having a `FileElectronicVersion`. |
| `matched_records_<timestamp>.csv` | All DSpace rows matched to a Pure record, regardless of whether a PDF was uploaded. `dspace_file_id` is blank for rows with no `pdf_handle_paths`. |

### matched_records CSV

| Column | Example | Description |
|---|---|---|
| `dspace_uuid` | `3f2a1b...` | DSpace item UUID. |
| `pure_uuid` | `a1b2c3...` | Pure record UUID. |
| `pure_id` | `12345678` | Pure internal numeric ID (`pureId`), captured before system fields are stripped. |
| `handle` | `https://hdl.handle.net/10379/4728` | Item handle, prefixed with `https://hdl.handle.net/` if not already a full URL. |
| `dspace_file_id` | `/10379/4728/1/file.pdf ; /10379/4728/2/fulltext.pdf` | All handle-based file paths from `pdf_handle_paths`, semicolon-separated, as-is. Empty if the row had no PDF. |


### Status values

| Status | Meaning |
|---|---|
| `success` | All PDFs uploaded and Pure record updated. |
| `partial_success` | At least one PDF uploaded successfully, but one or more failed. |
| `metadata_updated` | File already existed with the same filename and size, but one or more metadata fields (license, access type, version type, visible on portal date, or embargo period) were missing or out of date and have been updated. |
| `no_match` | No Pure record could be matched to this DSpace row. |
| `skipped_existing_fev` | All files for this row already exist in Pure with the same filename and size. Metadata may have been updated silently. |
| `pdf_upload_failed` | All PDF uploads failed (not found locally, or download/upload error). |
| `put_failed` | File uploaded but the subsequent PUT to Pure failed. |
| `dry_run` | Dry-run mode — no action taken. |

---

## Notes

- The script only modifies `electronicVersions`. All other fields on the Pure record are preserved as-is.
- Re-runs are safe when `--skip-existing` is on — files with an identical filename and size already in Pure will be skipped on a per-file basis.
- If a PUT fails after a successful upload, the uploaded file will be orphaned in Pure and deleted automatically after 2 hours. The failed row is written to `failed_<timestamp>.csv` for manual follow-up.
- The Pure JSON input does not need to be regenerated between runs; the script reads it once at startup.
- Filenames saved to disk and sent to Pure are always URL-decoded. The original encoded paths from `pdf_handle_paths` are preserved as-is in `dspace_file_id` in all log outputs.